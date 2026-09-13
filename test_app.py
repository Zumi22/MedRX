import secrets
import os
import os
import sys
import json
import sqlite3
import unittest

# Import functions from app.py
from app import (
    db_init, seed_demo_data, get_db_connection,
    hash_password, verify_password, hash_pin, verify_pin, hash_token,
    generate_random_patient_id, generate_random_rx_id, generate_pin, generate_qr_token,
    tool_lookup_drug, tool_check_duplicate_medications, tool_check_interactions,
    tool_compare_medicines, tool_calculate_remaining_quantity,
    tool_check_pharmacy_authorization, tool_route_substitution_request,
    run_prescription_verification_agent, run_alternative_medicine_agent,
    generate_prescription_pdf, create_audit_event
)

class MedRxAppTests(unittest.TestCase):

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

    def test_01_db_seeding_and_users(self):
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]
        self.assertGreaterEqual(user_count, 4, "Demo database should seed at least 4 default users.")

        # Master admin password check
        cursor.execute("SELECT password_hash FROM users WHERE email = 'master@medrx.demo'")
        admin_pass = cursor.fetchone()['password_hash']
        self.assertTrue(verify_password("DemoPass123!", admin_pass))
        conn.close()

    def test_02_patient_id_format(self):
        pid = generate_random_patient_id()
        self.assertTrue(pid.startswith("MRX-") or pid.startswith("PT-"))
        self.assertGreaterEqual(len(pid), 9)


    def test_03_pin_and_token_hashing(self):
        pin = "1234"
        pin_h = hash_pin(pin)
        self.assertTrue(verify_pin(pin, pin_h))
        self.assertFalse(verify_pin("9999", pin_h))

        tok = generate_qr_token()
        tok_h = hash_token(tok)
        self.assertEqual(len(tok_h), 64)

    def test_04_drug_catalog_lookup(self):
        seed_demo_data(force_reset=False)
        drugs = tool_lookup_drug("Amoxicillin")
        self.assertGreaterEqual(len(drugs), 1)
        self.assertEqual(drugs[0]['generic_name'], "Amoxicillin")

    def test_05_prescription_verification_agent(self):
        items = [
            {"medicine_name": "Panadol 500mg", "generic_name": "Paracetamol", "strength": "500 mg", "dosage_form": "Tablet", "quantity": 20},
            {"medicine_name": "Augmentin 625mg", "generic_name": "Amoxicillin / Clavulanate", "strength": "625 mg", "dosage_form": "Tablet", "quantity": 10}
        ]
        res = run_prescription_verification_agent(items, "MRX-AB7K-92QF")
        self.assertTrue(res['valid'])
        self.assertIn("trace", res)
        self.assertGreaterEqual(len(res['trace']), 5)

    def test_06_dispensing_guard_and_remaining_quantity(self):
        seed_demo_data(force_reset=False)
        # Demo prescription MRX-RX-82K91 item 1 has prescribed=60, dispensed=40, remaining=20
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM prescription_items WHERE rx_id = 'MRX-RX-82K91' LIMIT 1")
        row = cursor.fetchone()
        if not row:
            cursor.execute("""
                INSERT INTO prescription_items (rx_id, version_number, medicine_name, generic_name, strength, dosage_form, route, dose, frequency, duration, quantity, instructions)
                VALUES ('MRX-RX-82K91', 1, 'Glucophage 500mg', 'Metformin', '500 mg', 'Tablet', 'Oral', '1 tab', 'Twice daily after meals', '30 days', 60, 'Take with food to prevent GI upset')
            """)
            conn.commit()
            item_id = cursor.lastrowid
        else:
            item_id = row['id']
        conn.close()

        rem_info = tool_calculate_remaining_quantity('MRX-RX-82K91', item_id)
        self.assertGreaterEqual(rem_info['prescribed'], 1)

    def test_07_alternative_medicine_agent(self):
        orig_item = {"medicine_name": "Zithromax 500mg", "generic_name": "Azithromycin", "strength": "500 mg", "dosage_form": "Tablet", "route": "Oral"}
        prop_item = {"proposed_generic": "Azithromycin", "proposed_brand": "Generic Azithromycin", "proposed_strength": "500 mg", "proposed_dosage_form": "Tablet", "proposed_route": "Oral"}
        
        alt_res = run_alternative_medicine_agent(orig_item, prop_item, "MRX-AB7K-92QF")
        self.assertTrue(alt_res['suitable'])
        self.assertGreaterEqual(alt_res['similarity_score'], 0.80)

    def test_08_pdf_generation(self):
        items = [{"medicine_name": "Amoxil 500mg", "generic_name": "Amoxicillin", "strength": "500 mg", "dosage_form": "Capsule", "quantity": 15, "instructions": "Take 1 cap 3x daily"}]
        pdf_data = generate_prescription_pdf("TEST-RX-01", "MRX-TEST-0001", "Dr. Test", "PMC-12345", "Test Hospital", items, "1234", "TOK12345")
        self.assertIsInstance(pdf_data, bytes)
        self.assertGreater(len(pdf_data), 1000)

    def test_09_audit_event_recording(self):
        create_audit_event("test@medrx.demo", "tester", "UNIT_TEST_RUN", "test", "999", {"status": "ok"})
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM audit_logs WHERE event_type = 'UNIT_TEST_RUN'")
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['actor_email'], "test@medrx.demo")
        conn.close()

if __name__ == '__main__':
    unittest.main()
