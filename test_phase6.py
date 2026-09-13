import secrets
import os
import os
import sys
import unittest
import json

from app import (
    db_init, seed_demo_data, get_db_connection,
    tool_lookup_drug,
    tool_check_duplicate_medications,
    tool_check_duplicates,
    tool_check_interactions,
    tool_get_medication_history,
    tool_get_dispensing_history,
    tool_compare_medicines,
    tool_calculate_remaining_quantity,
    tool_find_alternatives,
    tool_check_pharmacy_authorization,
    tool_route_substitution_request,
    run_prescription_verification_agent,
    run_medication_safety_agent,
    run_alternative_medicine_agent,
    run_dispensing_guard_agent,
    run_audit_anomaly_agent,
    run_approval_routing_agent,
    call_llm
)

class Phase6ReviewTests(unittest.TestCase):

    def setUp(self):
        db_file = f"test_db_{self._testMethodName}_{secrets.token_hex(4)}.db"
        os.environ["MEDRX_DB_FILE"] = db_file
        db_init()
        seed_demo_data(force_reset=True)
        self.conn = get_db_connection()
        self.cursor = self.conn.cursor()

    def tearDown(self):
        if hasattr(self, 'conn') and self.conn:
            try:
                self.conn.close()
            except Exception:
                pass

    def test_01_all_10_internal_tools(self):
        """1. Verify all 10 internal agentic tools exist and execute correctly."""
        # 1. lookup_drug
        drugs = tool_lookup_drug("Amoxicillin")
        self.assertGreaterEqual(len(drugs), 1)

        # 2. check_duplicate_medications & check_duplicates
        dupes = tool_check_duplicates([
            {"medicine_name": "Amoxil", "generic_name": "Amoxicillin"},
            {"medicine_name": "Amoxicillin Caps", "generic_name": "Amoxicillin"}
        ])
        self.assertEqual(len(dupes), 1)

        # 3. check_interactions
        interactions = tool_check_interactions([
            {"generic_name": "Ibuprofen"},
            {"generic_name": "Warfarin"}
        ])
        self.assertEqual(len(interactions), 1)

        # 4. get_medication_history
        med_hist = tool_get_medication_history("MRX-AB7K-92QF")
        self.assertIsInstance(med_hist, list)

        # 5. get_dispensing_history
        disp_hist = tool_get_dispensing_history()
        self.assertIsInstance(disp_hist, list)

        # 6. compare_medicines
        orig = {"medicine_name": "Panadol", "generic_name": "Paracetamol", "dosage_form": "Tablet"}
        prop = {"proposed_generic": "Paracetamol", "proposed_brand": "Calpol", "proposed_strength": "500 mg", "proposed_dosage_form": "Tablet"}
        comp = tool_compare_medicines(orig, prop)
        self.assertGreaterEqual(comp['similarity_score'], 0.90)

        # 7. calculate_remaining_quantity
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM prescription_items WHERE rx_id = 'MRX-RX-82K91' LIMIT 1")
        item_id = cursor.fetchone()['id']
        conn.close()
        rem = tool_calculate_remaining_quantity("MRX-RX-82K91", item_id)
        self.assertIn("remaining", rem)

        # 8. find_alternatives
        alts = tool_find_alternatives({"medicine_name": "Panadol", "generic_name": "Paracetamol"})
        self.assertIsInstance(alts, list)

        # 9. check_pharmacy_authorization
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM hospitals WHERE code = 'RIPHAH-01'")
        h1 = cursor.fetchone()['id']
        cursor.execute("SELECT id FROM pharmacies WHERE license_no = 'PHARM-ISB-4412'")
        p1 = cursor.fetchone()['id']
        cursor.execute("SELECT id FROM doctors WHERE license_no = 'PMC-89102-A'")
        d1 = cursor.fetchone()['id']
        conn.close()

        auth = tool_check_pharmacy_authorization(pharmacy_id=p1, hospital_id=h1)
        self.assertTrue(auth)

        # 10. route_substitution_request
        route = tool_route_substitution_request(hospital_id=h1, prescribing_doctor_id=d1)
        self.assertIsNotNone(route['assigned_doctor_id'])

    def test_02_prescription_verification_agent(self):
        """2. Verify Prescription Verification Agent output and visual trace."""
        items = [
            {"medicine_name": "Panadol", "generic_name": "Paracetamol", "dosage_form": "Tablet", "quantity": 30},
            {"medicine_name": "Brufen", "generic_name": "Ibuprofen", "dosage_form": "Tablet", "quantity": 15}
        ]
        res = run_prescription_verification_agent(items, "MRX-AB7K-92QF")
        self.assertTrue(res['valid'])
        self.assertIn("trace", res)
        self.assertGreaterEqual(len(res['trace']), 4)

    def test_03_medication_safety_agent(self):
        """3. Verify Medication Safety Agent allergy & interaction detection."""
        # Patient MRX-AB7K-92QF has 'Penicillin, Shellfish' allergy seeded
        items = [{"medicine_name": "Augmentin 625mg", "generic_name": "Amoxicillin / Clavulanate", "quantity": 10, "dosage_form": "Tablet"}]
        res = run_medication_safety_agent(items, "MRX-AB7K-92QF")
        self.assertFalse(res['safe'])
        self.assertTrue(any("ALLERGY" in flag for flag in res['safety_flags']))
        self.assertIn("trace", res)

    def test_04_alternative_medicine_agent(self):
        """4. Verify Alternative Medicine Agent similarity scoring & human approval disclaimer."""
        orig = {"medicine_name": "Zithromax 500mg", "generic_name": "Azithromycin", "dosage_form": "Tablet", "strength": "500 mg"}
        prop = {"proposed_generic": "Azithromycin", "proposed_brand": "Generic Azithro", "proposed_strength": "500 mg", "proposed_dosage_form": "Tablet"}
        res = run_alternative_medicine_agent(orig, prop, "MRX-AB7K-92QF")
        self.assertIn("similarity_score", res)
        self.assertIn("disclaimer", res)
        self.assertIn("trace", res)

    def test_05_dispensing_guard_agent(self):
        """5. Verify Dispensing Guard Agent allowed vs blocked decisions."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM prescription_items WHERE rx_id = 'MRX-RX-82K91' LIMIT 1")
        item_id = cursor.fetchone()['id']
        conn.close()

        # Check normal dispense within limit
        res_ok = run_dispensing_guard_agent("MRX-RX-82K91", item_id, requested_qty=5)
        self.assertTrue(res_ok['allowed'])
        self.assertEqual(res_ok['decision'], "ALLOWED")

        # Check over-dispense attempt (> remaining limit)
        res_blocked = run_dispensing_guard_agent("MRX-RX-82K91", item_id, requested_qty=999)
        self.assertFalse(res_blocked['allowed'])
        self.assertEqual(res_blocked['decision'], "BLOCKED")

    def test_06_audit_anomaly_agent(self):
        """6. Verify Audit / Anomaly Agent pattern scanning."""
        res = run_audit_anomaly_agent()
        self.assertIn("anomaly_count", res)
        self.assertIn("trace", res)

    def test_07_approval_routing_agent(self):
        """7. Verify Approval Routing Agent doctor selection."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM hospitals WHERE code = 'RIPHAH-01'")
        h1 = cursor.fetchone()['id']
        cursor.execute("SELECT id FROM doctors WHERE license_no = 'PMC-89102-A'")
        d1 = cursor.fetchone()['id']
        conn.close()

        res = run_approval_routing_agent(hospital_id=h1, prescribing_doctor_id=d1)
        self.assertIsNotNone(res['assigned_doctor_id'])
        self.assertIn("trace", res)

    def test_08_deterministic_fallback_and_no_key_security(self):
        """8. Verify deterministic fallback engine when LLM API key is absent."""
        orig_key = os.environ.get("LLM_API_KEY")
        if "LLM_API_KEY" in os.environ:
            del os.environ["LLM_API_KEY"]

        res = run_prescription_verification_agent([
            {"medicine_name": "Augmentin", "generic_name": "Amoxicillin / Clavulanate", "quantity": 10, "dosage_form": "Tablet"}
        ])
        self.assertIsNotNone(res['explanation'])
        self.assertGreater(len(res['explanation']), 10)

        if orig_key:
            os.environ["LLM_API_KEY"] = orig_key

if __name__ == '__main__':
    unittest.main()
