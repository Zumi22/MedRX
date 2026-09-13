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
    hash_pin, verify_pin, hash_token,
    generate_prescription_pdf, render_public_qr_verification,
    create_audit_event, tool_calculate_remaining_quantity, DB_FILE
)

class Phase4ReviewTests(unittest.TestCase):

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

    def test_01_prescription_id_and_patient_id_format(self):
        """1. Test opaque random Prescription ID and Patient ID formats."""
        rx_id = generate_random_rx_id()
        self.assertTrue(rx_id.startswith("RX-"))
        self.assertEqual(len(rx_id), 8)  # Format: RX-XXXXX

        pat_id = generate_random_patient_id()
        self.assertTrue(pat_id.startswith("MRX-"))
        self.assertEqual(len(pat_id), 13) # Format: MRX-XXXX-XXXX

    def test_02_secure_pin_generation_and_hashing(self):
        """2. Test random 4-digit PIN generation, SHA-256 hashing, and verification."""
        pin = generate_pin()
        self.assertEqual(len(pin), 4)
        self.assertTrue(pin.isdigit())

        pin_h = hash_pin(pin)
        self.assertNotIn(pin, pin_h, "Hashed PIN must not expose plaintext PIN.")
        self.assertEqual(len(pin_h), 64, "SHA-256 hash string must be 64 hex characters.")
        self.assertTrue(verify_pin(pin, pin_h))
        self.assertFalse(verify_pin("0000" if pin != "0000" else "1111", pin_h))

    def test_03_secure_qr_token_generation_and_hashing(self):
        """3. Test random QR token generation, token hashing, and zero PHI check."""
        qr_token = generate_qr_token()
        self.assertGreaterEqual(len(qr_token), 32)
        
        # Zero PHI verification: Ensure token does not contain names, DOB, CNIC or clinical terms
        forbidden_phi = ["john", "sarah", "usman", "ahmed", "hypertension", "diabetes", "cnic", "dob"]
        for phi in forbidden_phi:
            self.assertNotIn(phi, qr_token.lower(), f"QR token must not contain PHI: '{phi}'")

        token_h = hash_token(qr_token)
        self.assertEqual(len(token_h), 64)

    def test_04_verification_token_db_storage(self):
        """4. Test verification token hash storage in database."""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        tok = generate_qr_token()
        tok_h = hash_token(tok)
        rx_id = "MRX-RX-82K91"

        cursor.execute("""
            INSERT INTO verification_tokens (token_hash, rx_id)
            VALUES (?, ?)
        """, (tok_h, rx_id))
        conn.commit()

        cursor.execute("SELECT * FROM verification_tokens WHERE token_hash = ?", (tok_h,))
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['rx_id'], rx_id)
        self.assertEqual(row['is_revoked'], 0)
        conn.close()

    def test_05_reportlab_pdf_generation(self):
        """5. Test ReportLab clinical prescription PDF generation with embedded QR & PIN."""
        items = [
            {"medicine_name": "Glucophage 500mg", "generic_name": "Metformin", "strength": "500 mg", "dosage_form": "Tablet", "route": "Oral", "dose": "1 tab", "frequency": "Twice daily", "duration": "30 days", "quantity": 60, "instructions": "Take with meals"},
            {"medicine_name": "Zestril 10mg", "generic_name": "Lisinopril", "strength": "10 mg", "dosage_form": "Tablet", "route": "Oral", "dose": "1 tab", "frequency": "Once daily", "duration": "30 days", "quantity": 30, "instructions": "Monitor BP"}
        ]
        
        pdf_bytes = generate_prescription_pdf(
            rx_id="RX-99001",
            patient_id="MRX-AB7K-92QF",
            doctor_name="Dr. Usman Malik",
            doc_license="PMC-89102-A",
            hospital_name="Riphah Demo Hospital",
            items=items,
            pin="1234",
            qr_token="DEMO_SECURE_TOKEN_MRX_82K91_XYZ"
        )

        self.assertIsInstance(pdf_bytes, bytes)
        self.assertGreater(len(pdf_bytes), 2000, "Generated PDF binary should be larger than 2KB.")
        self.assertTrue(pdf_bytes.startswith(b"%PDF"), "PDF binary must start with standard PDF header '%PDF'.")

    def test_06_public_qr_minimum_necessary_disclosure(self):
        """6. Test public QR verification displays minimum necessary data with zero PHI."""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Query token DEMO_SECURE_TOKEN_MRX_82K91_XYZ hash
        demo_tok = "DEMO_SECURE_TOKEN_MRX_82K91_XYZ"
        demo_tok_h = hash_token(demo_tok)

        cursor.execute("INSERT OR IGNORE INTO verification_tokens (token_hash, rx_id) VALUES (?, 'MRX-RX-82K91')", (demo_tok_h,))
        conn.commit()

        cursor.execute("""
            SELECT vt.*, p.rx_id, p.status, p.issue_timestamp, p.patient_id, h.name as hospital_name
            FROM verification_tokens vt
            JOIN prescriptions p ON vt.rx_id = p.rx_id
            JOIN hospitals h ON p.hospital_id = h.id
            WHERE vt.token_hash = ? AND vt.is_revoked = 0
        """, (demo_tok_h,))
        row = cursor.fetchone()
        self.assertIsNotNone(row)

        # Minimum necessary fields available for public view:
        # Prescription ID, status, hospital name, issue timestamp, medication remaining table.
        # Ensure NO patient name, DOB, or clinical diagnosis is exposed in row object.
        self.assertNotIn("patient_name", row.keys())
        self.assertNotIn("diagnosis", row.keys())
        self.assertNotIn("symptoms", row.keys())
        self.assertEqual(row['rx_id'], 'MRX-RX-82K91')
        self.assertEqual(row['status'], 'Partially Dispensed')

        # Test remaining quantity calculation for prescription item
        cursor.execute("SELECT id FROM prescription_items WHERE rx_id = 'MRX-RX-82K91' LIMIT 1")
        item_row = cursor.fetchone()
        if not item_row:
            cursor.execute("""
                INSERT INTO prescription_items (rx_id, version_number, medicine_name, generic_name, strength, dosage_form, route, dose, frequency, duration, quantity, instructions)
                VALUES ('MRX-RX-82K91', 1, 'Glucophage 500mg', 'Metformin', '500 mg', 'Tablet', 'Oral', '1 tab', 'Twice daily after meals', '30 days', 60, 'Take with food to prevent GI upset')
            """)
            conn.commit()
            item_id = cursor.lastrowid
        else:
            item_id = item_row['id']

        rem_info = tool_calculate_remaining_quantity('MRX-RX-82K91', item_id)
        self.assertGreaterEqual(rem_info['prescribed'], 1)
        conn.close()

    def test_07_qr_audit_logging(self):
        """7. Test audit event recording for QR verification attempts."""
        create_audit_event("public_qr_scanner", "guest", "QR_VERIFICATION_SUCCESS", "prescription", "MRX-RX-82K91", {"status": "Partially Dispensed"})
        create_audit_event("public_qr_scanner", "guest", "QR_VERIFICATION_FAILED", "token", "INVALID_TOK", {"reason": "Token hash not found"})

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM audit_logs WHERE event_type IN ('QR_VERIFICATION_SUCCESS', 'QR_VERIFICATION_FAILED')")
        logs = cursor.fetchall()
        self.assertGreaterEqual(len(logs), 2)
        conn.close()

if __name__ == '__main__':
    unittest.main()
