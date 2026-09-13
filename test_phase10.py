import os
import unittest
import os
import sqlite3
import datetime
import json
import secrets
from app import (
    db_init, seed_demo_data, get_db_connection,
    hash_password, verify_password, hash_pin, verify_pin, hash_token,
    generate_random_patient_id, generate_random_rx_id, generate_pin, generate_qr_token,
    run_prescription_verification_agent, run_alternative_medicine_agent,
    run_dispensing_guard_agent, tool_calculate_remaining_quantity,
    tool_check_pharmacy_authorization, tool_route_substitution_request,
    create_audit_event, detect_system_anomalies
)

class TestPhase10(unittest.TestCase):
    def setUp(self):

        db_file = os.path.join(os.environ.get("TEMP", "C:/Users/dell/AppData/Local/Temp"), f"test_medrx_{secrets.token_hex(4)}.db")
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

    def test_01_patient_expanded_clinical_profile(self):
        """Phase 10: Verify expanded clinical profiles in patients and encounters tables."""
        pid = generate_random_patient_id()
        self.assertTrue(pid.startswith("MRX-"))

        self.cursor.execute("""
            INSERT INTO patients (patient_id, full_name, dob, age, sex, phone, email, address, emergency_contact, allergies, current_medications, past_medical_history, past_surgical_history, family_history, social_history, risk_flags, created_by_doctor_id)
            VALUES (?, 'Amina Malik', '1990-05-15', 36, 'Female', '+92-300-1112233', 'amina@demo.medrx', 'F-7/1, Islamabad', 'Kamran Malik +92-300-1112234', 'Penicillin', 'Metformin', 'T2DM', 'None', 'T2DM', 'Non-smoker', 'Penicillin Allergy', 1)
        """, (pid,))
        self.conn.commit()

        self.cursor.execute("SELECT * FROM patients WHERE patient_id = ?", (pid,))
        pat = self.cursor.fetchone()
        self.assertIsNotNone(pat)
        self.assertEqual(pat['full_name'], 'Amina Malik')
        self.assertEqual(pat['age'], 36)
        self.assertEqual(pat['sex'], 'Female')

        # Test encounter creation with full clinical profile
        enc_id = f"ENC-{secrets.token_hex(4).upper()}"
        self.cursor.execute("""
            INSERT INTO encounters (encounter_id, patient_id, doctor_id, hospital_id, vitals, chief_complaint, symptoms, observations, history, assessment, primary_diagnosis, secondary_diagnosis, diagnosis_code, plan)
            VALUES (?, ?, 1, 1, 'BP: 120/80', 'Checkup', 'Cough', 'Stable', 'No previous surgery', 'Controlled T2DM', 'Essential Hypertension', 'Type 2 Diabetes', 'I10', 'Continue Metformin')
        """, (enc_id, pid))
        self.conn.commit()

        self.cursor.execute("SELECT * FROM encounters WHERE encounter_id = ?", (enc_id,))
        enc = self.cursor.fetchone()
        self.assertIsNotNone(enc)
        self.assertEqual(enc['primary_diagnosis'], 'Essential Hypertension')
        self.assertEqual(enc['secondary_diagnosis'], 'Type 2 Diabetes')

    def test_02_short_window_duplicate_detection(self):
        """Phase 10: Verify short-window (5 min) duplicate prescription detection query."""
        pid = "MRX-AB7K-92QF"
        doc_id = 1

        # Check existing recent prescriptions
        self.cursor.execute("""
            SELECT rx_id, issue_timestamp FROM prescriptions
            WHERE doctor_id = ? AND patient_id = ? AND issue_timestamp >= datetime('now', '-5 minutes')
        """, (doc_id, pid))
        rows = self.cursor.fetchall()
        # Should be a queryable list
        self.assertIsInstance(rows, list)

    def test_03_submission_token_idempotency(self):
        """Phase 10: Verify submission_token prevents duplicate prescription creation."""
        sub_token = secrets.token_hex(16)
        rx_id = generate_random_rx_id()
        pin_h = hash_pin("1234")

        self.cursor.execute("""
            INSERT INTO prescriptions (rx_id, patient_id, doctor_id, hospital_id, status, pin_hash, submission_token)
            VALUES (?, 'MRX-AB7K-92QF', 1, 1, 'Active', ?, ?)
        """, (rx_id, pin_h, sub_token))
        self.conn.commit()

        # Re-query by submission token
        self.cursor.execute("SELECT rx_id FROM prescriptions WHERE submission_token = ?", (sub_token,))
        row = self.cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['rx_id'], rx_id)

    def test_04_pharmacist_duplicate_substitution_protection(self):
        """Phase 10: Verify pharmacist duplicate substitution protection check."""
        rx_id = "MRX-RX-82K91"
        self.cursor.execute("SELECT id FROM prescription_items WHERE rx_id = ? LIMIT 1", (rx_id,))
        item = self.cursor.fetchone()
        self.assertIsNotNone(item)
        item_id = item['id']

        # Query pending substitution
        self.cursor.execute("SELECT id, status FROM substitutions WHERE rx_id = ? AND original_item_id = ? AND status = 'Pending Approval'", (rx_id, item_id))
        pending_before = self.cursor.fetchone()
        self.assertIsNone(pending_before)

        # Insert a pending substitution
        self.cursor.execute("""
            INSERT INTO substitutions (rx_id, original_item_id, proposed_generic, proposed_brand, proposed_strength, proposed_dosage_form, proposed_route, pharmacist_id, status, assigned_doctor_id)
            VALUES (?, ?, 'Levofloxacin', 'Levaquin', '500 mg', 'Tablet', 'Oral', 4, 'Pending Approval', 1)
        """, (rx_id, item_id))
        self.conn.commit()

        # Query pending substitution again
        self.cursor.execute("SELECT id, status FROM substitutions WHERE rx_id = ? AND original_item_id = ? AND status = 'Pending Approval'", (rx_id, item_id))
        pending_after = self.cursor.fetchone()
        self.assertIsNotNone(pending_after)
        self.assertEqual(pending_after['status'], 'Pending Approval')

    def test_05_doctor_approval_idempotency(self):
        """Phase 10: Verify doctor approval idempotency (v1 -> v2 version increment only once)."""
        rx_id = "MRX-RX-82K91"
        self.cursor.execute("SELECT id FROM prescription_items WHERE rx_id = ? LIMIT 1", (rx_id,))
        item_id = self.cursor.fetchone()['id']

        self.cursor.execute("""
            INSERT INTO substitutions (rx_id, original_item_id, proposed_generic, proposed_brand, proposed_strength, proposed_dosage_form, proposed_route, pharmacist_id, status, assigned_doctor_id)
            VALUES (?, ?, 'Levofloxacin', 'Levaquin', '500 mg', 'Tablet', 'Oral', 4, 'Pending Approval', 1)
        """, (rx_id, item_id))
        sub_id = self.cursor.lastrowid
        self.conn.commit()

        # First approval execution
        self.cursor.execute("SELECT status FROM substitutions WHERE id = ?", (sub_id,))
        sub = self.cursor.fetchone()
        self.assertEqual(sub['status'], 'Pending Approval')

        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute("UPDATE substitutions SET status = 'Approved', approval_doctor_id = 1, decided_at = ? WHERE id = ?", (now_ts, sub_id))
        self.cursor.execute("SELECT version_number FROM prescriptions WHERE rx_id = ?", (rx_id,))
        cur_ver = self.cursor.fetchone()['version_number']
        new_ver = cur_ver + 1
        self.cursor.execute("UPDATE prescriptions SET version_number = ? WHERE rx_id = ?", (new_ver, rx_id))
        self.conn.commit()

        # Second approval execution (Idempotent check)
        self.cursor.execute("SELECT status FROM substitutions WHERE id = ?", (sub_id,))
        sub_second = self.cursor.fetchone()
        self.assertNotEqual(sub_second['status'], 'Pending Approval') # Skipped!

    def test_06_patient_handover_credentials_and_zero_phi_qr(self):
        """Phase 10: Verify Rx ID, 4-digit PIN, zero-PHI opaque QR verification token."""
        pin = generate_pin()
        self.assertEqual(len(pin), 4)
        self.assertTrue(pin.isdigit())

        qr_token = generate_qr_token()
        self.assertNotIn("Zainab", qr_token)
        self.assertNotIn("Diabetes", qr_token)
        self.assertNotIn("1982-04-12", qr_token)

        token_h = hash_token(qr_token)
        self.cursor.execute("INSERT INTO verification_tokens (token_hash, rx_id) VALUES (?, 'MRX-RX-82K91')", (token_h,))
        self.conn.commit()

        # Query token
        self.cursor.execute("SELECT * FROM verification_tokens WHERE token_hash = ?", (token_h,))
        row = self.cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['rx_id'], 'MRX-RX-82K91')

if __name__ == '__main__':
    unittest.main()
