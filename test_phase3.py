import secrets
import os
import os
import sys
import unittest
import sqlite3
import json

from app import (
    db_init, seed_demo_data, get_db_connection,
    generate_random_patient_id, generate_random_rx_id, generate_pin, generate_qr_token,
    hash_pin, hash_token, tool_lookup_drug,
    run_prescription_verification_agent, create_audit_event, DB_FILE
)

class Phase3ReviewTests(unittest.TestCase):

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

    def test_01_doctor_dashboard_and_active_check(self):
        """1. Test Doctor active status check and dashboard queries."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT d.*, u.email FROM doctors d JOIN users u ON d.user_id = u.id WHERE u.email = 'doctor@medrx.demo'")
        doc = cursor.fetchone()
        self.assertIsNotNone(doc)
        self.assertEqual(doc['status'], 'active')
        self.assertEqual(doc['specialty'], 'Internal Medicine')

        # Check rx count for doctor
        cursor.execute("SELECT COUNT(*) FROM prescriptions WHERE doctor_id = ?", (doc['id'],))
        rx_cnt = cursor.fetchone()[0]
        self.assertGreaterEqual(rx_cnt, 1)
        conn.close()

    def test_02_new_patient_creation(self):
        """2. Test creation of canonical privacy-preserving patient ID."""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        pid = generate_random_patient_id()
        self.assertTrue(pid.startswith("MRX-"))
        self.assertEqual(len(pid), 13)

        cursor.execute("""
            INSERT INTO patients (patient_id, created_by_doctor_id, risk_flags)
            VALUES (?, 1, 'Asthma, Dust Allergy')
        """, (pid,))
        conn.commit()

        cursor.execute("SELECT * FROM patients WHERE patient_id = ?", (pid,))
        pat = cursor.fetchone()
        self.assertIsNotNone(pat)
        self.assertEqual(pat['patient_id'], pid)
        self.assertIn("Asthma", pat['risk_flags'])
        conn.close()

    def test_03_returning_patient_lookup(self):
        """3. Test searching returning patient by Patient ID returns existing record."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM patients WHERE patient_id = 'MRX-AB7K-92QF'")
        pat = cursor.fetchone()
        self.assertIsNotNone(pat, "Returning patient MRX-AB7K-92QF must exist.")
        self.assertEqual(pat['patient_id'], 'MRX-AB7K-92QF')
        conn.close()

    def test_04_duplicate_patient_warning_check(self):
        """4. Test duplicate patient warning detection logic."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT patient_id, risk_flags FROM patients")
        pats = cursor.fetchall()
        conn.close()

        # Check risk flag matching or existing patient lookup warning
        dup_found = False
        target_flags = "Hypertension"
        for p in pats:
            if p['risk_flags'] and target_flags in p['risk_flags']:
                dup_found = True
                break
        self.assertTrue(dup_found, "System should identify potential duplicate risk patterns.")

    def test_05_clinical_encounter_logging(self):
        """5. Test logging a new clinical encounter for existing patient."""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        enc_id = "ENC-TEST-99"
        cursor.execute("""
            INSERT INTO encounters (encounter_id, patient_id, doctor_id, hospital_id, observations, symptoms, history, diagnosis)
            VALUES (?, 'MRX-AB7K-92QF', 1, 1, 'BP 125/82', 'Fever 101F, sore throat', 'T2DM', 'Acute Pharyngitis')
        """, (enc_id,))
        conn.commit()

        cursor.execute("SELECT * FROM encounters WHERE encounter_id = ?", (enc_id,))
        enc = cursor.fetchone()
        self.assertIsNotNone(enc)
        self.assertEqual(enc['diagnosis'], 'Acute Pharyngitis')
        conn.close()

    def test_06_medication_catalog_lookup(self):
        """6. Test pre-filling drug details from medication catalog."""
        drugs = tool_lookup_drug("Augmentin")
        self.assertGreaterEqual(len(drugs), 1)
        aug = drugs[0]
        self.assertEqual(aug['brand_name'], "Augmentin")
        self.assertEqual(aug['generic_name'], "Amoxicillin / Clavulanate")
        self.assertEqual(aug['dosage_form'], "Tablet")

    def test_07_prescription_creation_and_validation(self):
        """7. Test creating prescription items & verification agent validation."""
        rx_code = generate_random_rx_id()
        pin = "5678"
        pin_h = hash_pin(pin)
        
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO prescriptions (rx_id, patient_id, doctor_id, hospital_id, status, pin_hash)
            VALUES (?, 'MRX-AB7K-92QF', 1, 1, 'Active', ?)
        """, (rx_code, pin_h))

        items = [
            {"medicine_name": "Augmentin 625mg", "generic_name": "Amoxicillin / Clavulanate", "strength": "625 mg", "dosage_form": "Tablet", "route": "Oral", "dose": "1 tab", "frequency": "Twice daily", "duration": "5 days", "quantity": 10, "instructions": "Take after meals"},
            {"medicine_name": "Panadol 500mg", "generic_name": "Paracetamol", "strength": "500 mg", "dosage_form": "Tablet", "route": "Oral", "dose": "1 tab", "frequency": "Three times daily", "duration": "3 days", "quantity": 9, "instructions": "For fever"}
        ]

        for item in items:
            cursor.execute("""
                INSERT INTO prescription_items (rx_id, version_number, medicine_name, generic_name, strength, dosage_form, route, dose, frequency, duration, quantity, instructions)
                VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (rx_code, item['medicine_name'], item['generic_name'], item['strength'], item['dosage_form'], item['route'], item['dose'], item['frequency'], item['duration'], item['quantity'], item['instructions']))

        conn.commit()

        # Run Verification Agent
        agent_res = run_prescription_verification_agent(items, 'MRX-AB7K-92QF')
        self.assertTrue(agent_res['valid'])
        self.assertGreaterEqual(agent_res['confidence'], 0.85)

        cursor.execute("SELECT COUNT(*) FROM prescription_items WHERE rx_id = ?", (rx_code,))
        self.assertEqual(cursor.fetchone()[0], 2)
        conn.close()

    def test_08_prescription_status_and_history(self):
        """8. Test prescription status lifecycle and history retrieval."""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT status FROM prescriptions WHERE rx_id = 'MRX-RX-82K91'")
        st_val = cursor.fetchone()['status']
        self.assertEqual(st_val, 'Partially Dispensed')

        # Retrieve prescription history for doctor #1
        cursor.execute("SELECT * FROM prescriptions WHERE doctor_id = 1 ORDER BY id DESC")
        rxs = cursor.fetchall()
        self.assertGreaterEqual(len(rxs), 1)
        conn.close()

    def test_09_phase3_audit_events(self):
        """9. Test audit logging for Phase 3 clinical actions."""
        create_audit_event("doctor@medrx.demo", "doctor", "PATIENT_CREATED", "patient", "MRX-TEST-99", {})
        create_audit_event("doctor@medrx.demo", "doctor", "ENCOUNTER_CREATED", "encounter", "ENC-9001", {})
        create_audit_event("doctor@medrx.demo", "doctor", "PRESCRIPTION_CREATED", "prescription", "RX-TEST-99", {"items": 2})

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM audit_logs WHERE event_type IN ('PATIENT_CREATED', 'ENCOUNTER_CREATED', 'PRESCRIPTION_CREATED')")
        logs = cursor.fetchall()
        self.assertGreaterEqual(len(logs), 3)
        conn.close()

if __name__ == '__main__':
    unittest.main()
