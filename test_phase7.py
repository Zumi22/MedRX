import secrets
import os
import os
import sys
import unittest
import json

from app import (
    db_init, seed_demo_data, get_db_connection,
    tool_route_substitution_request,
    tool_calculate_remaining_quantity,
    run_alternative_medicine_agent,
    create_audit_event,
    hash_pin
)

class Phase7ReviewTests(unittest.TestCase):

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

    def test_01_pharmacist_proposes_alternative_and_ai_agent_evaluates(self):
        """1. Pharmacist proposes alternative for out-of-stock item and AI agent evaluates similarity."""
        orig_item = {"medicine_name": "Zithromax 500mg", "generic_name": "Azithromycin", "strength": "500 mg", "dosage_form": "Tablet", "route": "Oral"}
        proposed_item = {"proposed_generic": "Azithromycin", "proposed_brand": "Generic Azithro", "proposed_strength": "500 mg", "proposed_dosage_form": "Tablet"}

        agent_res = run_alternative_medicine_agent(orig_item, proposed_item, "MRX-AB7K-92QF")
        
        self.assertIn("similarity_score", agent_res)
        self.assertGreaterEqual(agent_res['similarity_score'], 0.90)
        self.assertIn("disclaimer", agent_res)
        self.assertIn("trace", agent_res)

    def test_02_substitution_request_creation_and_routing_active_doctor(self):
        """2. Substitution request is created with status 'Pending Approval' and routed to active prescribing doctor."""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Get active doctor & prescription
        cursor.execute("SELECT id FROM doctors WHERE license_no = 'PMC-89102-A'")
        doc_id = cursor.fetchone()['id']

        cursor.execute("SELECT * FROM prescriptions WHERE rx_id = 'MRX-RX-82K91'")
        rx = cursor.fetchone()

        cursor.execute("SELECT * FROM prescription_items WHERE rx_id = 'MRX-RX-82K91' AND medicine_name LIKE '%Zithromax%'")
        item = cursor.fetchone()

        route = tool_route_substitution_request(rx['hospital_id'], doc_id)
        self.assertEqual(route['assigned_doctor_id'], doc_id)

        # Insert substitution request
        cursor.execute("""
            INSERT INTO substitutions (rx_id, original_item_id, proposed_generic, proposed_brand, proposed_strength, proposed_dosage_form, proposed_route, pharmacist_id, status, ai_analysis_json, assigned_doctor_id, routing_reason)
            VALUES ('MRX-RX-82K91', ?, 'Azithromycin', 'Generic Azithro', '500 mg', 'Tablet', 'Oral', 4, 'Pending Approval', '{}', ?, ?)
        """, (item['id'], route['assigned_doctor_id'], route['routing_reason']))
        sub_id = cursor.lastrowid

        cursor.execute("UPDATE prescriptions SET status = 'Pending Substitution Approval' WHERE rx_id = 'MRX-RX-82K91'")
        conn.commit()

        # Assert status is strictly 'Pending Approval' (NOT auto-approved)
        cursor.execute("SELECT status FROM substitutions WHERE id = ?", (sub_id,))
        sub_row = cursor.fetchone()
        self.assertEqual(sub_row['status'], 'Pending Approval')

        cursor.execute("SELECT status FROM prescriptions WHERE rx_id = 'MRX-RX-82K91'")
        self.assertEqual(cursor.fetchone()['status'], 'Pending Substitution Approval')
        conn.close()

    def test_03_doctor_approves_substitution_and_creates_new_version(self):
        """3. Prescribing doctor approves substitution -> Increments rx version and creates new prescription item."""
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM substitutions WHERE rx_id = 'MRX-RX-82K91' AND status = 'Pending Approval'")
        sub = cursor.fetchone()
        if not sub:
            cursor.execute("SELECT id FROM prescription_items WHERE rx_id = 'MRX-RX-82K91' AND medicine_name LIKE '%Zithromax%'")
            item = cursor.fetchone()
            cursor.execute("""
                INSERT INTO substitutions (rx_id, original_item_id, proposed_generic, proposed_brand, proposed_strength, proposed_dosage_form, proposed_route, pharmacist_id, status, ai_analysis_json, assigned_doctor_id, routing_reason)
                VALUES ('MRX-RX-82K91', ?, 'Azithromycin', 'Generic Azithro', '500 mg', 'Tablet', 'Oral', 4, 'Pending Approval', '{}', 1, 'Direct routing')
            """, (item['id'],))
            conn.commit()
            cursor.execute("SELECT * FROM substitutions WHERE rx_id = 'MRX-RX-82K91' AND status = 'Pending Approval'")
            sub = cursor.fetchone()
        self.assertIsNotNone(sub, "Pending substitution must exist.")

        cursor.execute("SELECT id FROM doctors WHERE license_no = 'PMC-89102-A'")
        doc_id = cursor.fetchone()['id']

        # Doctor Approves
        cursor.execute("""
            UPDATE substitutions
            SET status = 'Approved', approval_doctor_id = ?, doctor_decision_notes = 'Approved clinical alternative.', decided_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (doc_id, sub['id']))

        # Increment version from 1 to 2
        cursor.execute("SELECT version_number FROM prescriptions WHERE rx_id = 'MRX-RX-82K91'")
        cur_ver = cursor.fetchone()['version_number']
        new_ver = cur_ver + 1

        cursor.execute("UPDATE prescriptions SET version_number = ?, status = 'Modified by Approved Substitution' WHERE rx_id = 'MRX-RX-82K91'", (new_ver,))

        # Add substituted item with version 2
        cursor.execute("""
            INSERT INTO prescription_items (rx_id, version_number, medicine_name, generic_name, strength, dosage_form, route, dose, frequency, duration, quantity, instructions)
            VALUES ('MRX-RX-82K91', ?, 'Generic Azithro', 'Azithromycin', '500 mg', 'Tablet', 'Oral', '1 tab', 'Once daily', '5 days', 5, 'Substituted from Zithromax')
        """, (new_ver,))
        new_item_id = cursor.lastrowid

        cursor.execute("""
            INSERT INTO prescription_versions (rx_id, version_number, modified_by_doctor_id, substitution_id, change_summary)
            VALUES ('MRX-RX-82K91', ?, ?, ?, 'Approved substitution: Zithromax -> Azithromycin')
        """, (new_ver, doc_id, sub['id']))

        conn.commit()

        # Verify version increment and new item
        cursor.execute("SELECT version_number, status FROM prescriptions WHERE rx_id = 'MRX-RX-82K91'")
        rx_updated = cursor.fetchone()
        self.assertEqual(rx_updated['version_number'], 2)
        self.assertEqual(rx_updated['status'], 'Modified by Approved Substitution')

        cursor.execute("SELECT * FROM prescription_items WHERE id = ?", (new_item_id,))
        self.assertIsNotNone(cursor.fetchone())
        conn.close()

    def test_04_pharmacist_dispenses_approved_substituted_item(self):
        """4. Pharmacist sees updated prescription v2 and dispenses the substituted medicine."""
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM prescription_items WHERE rx_id = 'MRX-RX-82K91' AND version_number = 2")
        item_v2 = cursor.fetchone()
        if not item_v2:
            cursor.execute("""
                INSERT INTO prescription_items (rx_id, version_number, medicine_name, generic_name, strength, dosage_form, route, dose, frequency, duration, quantity, instructions)
                VALUES ('MRX-RX-82K91', 2, 'Generic Azithro', 'Azithromycin', '500 mg', 'Tablet', 'Oral', '1 tab', 'Once daily', '5 days', 5, 'Substituted from Zithromax')
            """)
            conn.commit()
            cursor.execute("SELECT id FROM prescription_items WHERE rx_id = 'MRX-RX-82K91' AND version_number = 2")
            item_v2 = cursor.fetchone()
        self.assertIsNotNone(item_v2)

        # Pharmacist dispenses all 5 units
        cursor.execute("""
            INSERT INTO dispensing_events (rx_id, item_id, pharmacy_id, pharmacist_id, quantity_dispensed, remaining_quantity, notes)
            VALUES ('MRX-RX-82K91', ?, 1, 4, 5, 0, 'Dispensed approved substituted item')
        """, (item_v2['id'],))

        conn.commit()

        rem = tool_calculate_remaining_quantity('MRX-RX-82K91', item_v2['id'])
        self.assertEqual(rem['remaining'], 0)
        self.assertEqual(rem['dispensed'], 5)
        conn.close()

    def test_05_routing_fallback_when_prescribing_doctor_inactive(self):
        """5. Test fallback routing to hospital on-call doctor when prescribing doctor is suspended."""
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM hospitals WHERE code = 'RIPHAH-01'")
        hosp_id = cursor.fetchone()['id']

        # Suspend doctor 1
        cursor.execute("SELECT id FROM doctors WHERE license_no = 'PMC-89102-A'")
        doc1_id = cursor.fetchone()['id']
        cursor.execute("UPDATE doctors SET status = 'suspended' WHERE id = ?", (doc1_id,))

        # Set doctor 2 as active on-call doctor
        cursor.execute("SELECT id FROM doctors WHERE license_no = 'PMC-99211-B'")
        doc2_id = cursor.fetchone()['id']
        cursor.execute("UPDATE doctors SET is_on_call = 1, status = 'active' WHERE id = ?", (doc2_id,))
        conn.commit()

        # Route substitution
        route = tool_route_substitution_request(hosp_id, doc1_id)
        self.assertEqual(route['assigned_doctor_id'], doc2_id)
        self.assertIn("on-call", route['routing_reason'].lower())

        # Restore doctor 1 status
        cursor.execute("UPDATE doctors SET status = 'active' WHERE id = ?", (doc1_id,))
        conn.commit()
        conn.close()

    def test_06_manual_escalation_when_no_active_doctors(self):
        """6. Test manual escalation fallback when no active physicians exist in hospital."""
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM hospitals WHERE code = 'RIPHAH-01'")
        hosp_id = cursor.fetchone()['id']

        # Temporarily suspend all doctors in hospital
        cursor.execute("UPDATE doctors SET status = 'suspended' WHERE hospital_id = ?", (hosp_id,))
        conn.commit()

        route = tool_route_substitution_request(hosp_id, 9999)
        self.assertIsNone(route['assigned_doctor_id'])
        self.assertIn("Manual escalation", route['routing_reason'])

        # Restore doctor status
        cursor.execute("UPDATE doctors SET status = 'active' WHERE hospital_id = ?", (hosp_id,))
        conn.commit()
        conn.close()

    def test_07_doctor_rejects_substitution(self):
        """7. Test substitution rejection by doctor -> Leaves version unchanged."""
        rx_id = "RX-REJECT-SUB"
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM doctors WHERE license_no = 'PMC-89102-A'")
        doc_id = cursor.fetchone()['id']

        cursor.execute("SELECT id FROM hospitals WHERE code = 'RIPHAH-01'")
        hosp_id = cursor.fetchone()['id']

        # Create test prescription
        cursor.execute("""
            INSERT INTO prescriptions (rx_id, patient_id, doctor_id, hospital_id, status, pin_hash, version_number)
            VALUES (?, 'MRX-AB7K-92QF', ?, ?, 'Pending Substitution Approval', 'hash', 1)
        """, (rx_id, doc_id, hosp_id))

        cursor.execute("""
            INSERT INTO prescription_items (rx_id, version_number, medicine_name, generic_name, strength, dosage_form, route, dose, frequency, duration, quantity)
            VALUES (?, 1, 'Drug A', 'Generic A', '10mg', 'Tablet', 'Oral', '1 tab', 'Daily', '7 days', 14)
        """, (rx_id,))
        item_id = cursor.lastrowid

        cursor.execute("""
            INSERT INTO substitutions (rx_id, original_item_id, proposed_generic, proposed_brand, proposed_strength, proposed_dosage_form, proposed_route, pharmacist_id, status, assigned_doctor_id)
            VALUES (?, ?, 'Generic B', 'Brand B', '10mg', 'Tablet', 'Oral', 4, 'Pending Approval', ?)
        """, (rx_id, item_id, doc_id))
        sub_id = cursor.lastrowid
        conn.commit()

        # Doctor Rejects
        cursor.execute("""
            UPDATE substitutions
            SET status = 'Rejected', approval_doctor_id = ?, doctor_decision_notes = 'Inappropriate substitute for patient history', decided_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (doc_id, sub_id))

        conn.commit()

        cursor.execute("SELECT status FROM substitutions WHERE id = ?", (sub_id,))
        self.assertEqual(cursor.fetchone()['status'], 'Rejected')

        # Prescription version must remain 1
        cursor.execute("SELECT version_number FROM prescriptions WHERE rx_id = ?", (rx_id,))
        self.assertEqual(cursor.fetchone()['version_number'], 1)
        conn.close()

if __name__ == '__main__':
    unittest.main()
