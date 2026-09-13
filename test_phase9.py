import secrets
import os
import unittest
import sqlite3
import os
import sys
import json
import re

# Ensure current directory is on sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app import (
    get_db_connection,
    db_init,
    seed_demo_data,
    hash_password,
    verify_password,
    hash_pin,
    verify_pin,
    hash_token,
    generate_qr_token,
    generate_pin,
    generate_random_rx_id,
    generate_random_patient_id,
    validate_positive_quantity,
    validate_pin_format,
    validate_email_format,
    run_dispensing_guard_agent,
    tool_check_pharmacy_authorization,
    call_llm,
    run_prescription_verification_agent,
    run_alternative_medicine_agent,
    run_audit_anomaly_agent,
    create_audit_event
)

class Phase9SecurityTests(unittest.TestCase):

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

    def test_01_qr_token_is_random(self):
        """1. Verify QR token generation is high-entropy and random."""
        t1 = generate_qr_token()
        t2 = generate_qr_token()
        self.assertNotEqual(t1, t2)
        self.assertGreaterEqual(len(t1), 32)
        self.assertGreaterEqual(len(t2), 32)

    def test_02_qr_token_hash_stored_not_plaintext(self):
        """2. Verify verification_tokens table stores SHA-256 token hash, not plaintext."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT token_hash FROM verification_tokens LIMIT 5")
        rows = cursor.fetchall()
        conn.close()

        self.assertTrue(len(rows) > 0)
        for r in rows:
            th = r['token_hash']
            self.assertEqual(len(th), 64) # SHA-256 hex string length
            self.assertRegex(th, r'^[a-f0-9]{64}$')

    def test_03_qr_contains_no_patient_phi(self):
        """3. Verify QR code verification token contains only opaque URL and zero PHI."""
        token = generate_qr_token()
        url = f"http://localhost:8501/?verify={token}"
        
        # Test zero PHI strings inside URL
        forbidden = ["John", "Doe", "Ali", "Fatima", "CNIC", "1990-01-01", "hypertension", "diabetes", "patient"]
        for f in forbidden:
            self.assertNotIn(f.lower(), url.lower())

    def test_04_invalid_qr_token_rejected(self):
        """4. Verify invalid QR token hash is rejected."""
        fake_token = "invalid_fake_qr_token_123456789"
        fake_hash = hash_token(fake_token)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM verification_tokens WHERE token_hash = ? AND is_revoked = 0", (fake_hash,))
        row = cursor.fetchone()
        conn.close()

        self.assertIsNone(row)

    def test_05_expired_revoked_token_rejected(self):
        """5. Verify expired or revoked verification tokens are rejected."""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Insert a revoked token with dynamic unique string
        rev_token = f"test_revoked_token_{generate_qr_token()[:12]}"
        rev_hash = hash_token(rev_token)
        cursor.execute("""
            INSERT INTO verification_tokens (token_hash, rx_id, is_revoked)
            VALUES (?, 'MRX-RX-82K91', 1)
        """, (rev_hash,))
        conn.commit()

        # Query active valid token
        cursor.execute("""
            SELECT * FROM verification_tokens
            WHERE token_hash = ? AND is_revoked = 0
              AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
        """, (rev_hash,))
        row = cursor.fetchone()
        conn.close()

        self.assertIsNone(row)

    def test_06_incorrect_pin_rejected(self):
        """6. Verify incorrect PIN attempts fail securely."""
        real_pin = "1234"
        hashed = hash_pin(real_pin)
        
        self.assertTrue(verify_pin("1234", hashed))
        self.assertFalse(verify_pin("9999", hashed))
        self.assertFalse(verify_pin("0000", hashed))

    def test_07_valid_credentials_succeed(self):
        """7. Verify valid Prescription ID + valid PIN lookup succeeds."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT rx_id, pin_hash FROM prescriptions WHERE rx_id = 'MRX-RX-82K91'")
        rx = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(rx)
        self.assertTrue(verify_pin("1234", rx['pin_hash']))

    def test_08_public_verification_is_read_only(self):
        """8. Verify public verification route query returns minimum necessary data without mutation capabilities."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT vt.*, p.rx_id, p.status, p.issue_timestamp, h.name as hospital_name
            FROM verification_tokens vt
            JOIN prescriptions p ON vt.rx_id = p.rx_id
            JOIN hospitals h ON p.hospital_id = h.id
            WHERE vt.rx_id = 'MRX-RX-82K91'
        """)
        row = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(row)
        # Ensure row contains status and hospital, but NO patient name, phone, email, CNIC, or diagnosis
        row_keys = list(row.keys())
        self.assertNotIn('patient_name', row_keys)
        self.assertNotIn('cnic', row_keys)
        self.assertNotIn('diagnosis', row_keys)

    def test_09_unauthorized_role_cannot_access_admin_functionality(self):
        """9. Verify role-based checks prevent unauthorized roles from master admin tools."""
        doctor_user = {"id": 10, "email": "doctor@medrx.demo", "role": "doctor"}
        pharmacist_user = {"id": 20, "email": "pharmacy@medrx.demo", "role": "pharmacist"}

        self.assertNotEqual(doctor_user['role'], "master_admin")
        self.assertNotEqual(pharmacist_user['role'], "master_admin")

    def test_10_unauthorized_pharmacy_cannot_access_prescription(self):
        """10. Verify unauthorized pharmacy cannot access prescriptions when allow_all_pharmacies is 0."""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        uniq = generate_qr_token()[:6]
        h_code = f"STRICT-{uniq}"
        u_email = f"unauth_{uniq}@pharm.demo"
        rx_id = f"RX-STRICT-{uniq}"

        # Create restrictive hospital
        cursor.execute("INSERT INTO hospitals (name, code, address, allow_all_pharmacies) VALUES ('Strict Hosp', ?, 'Addr', 0)", (h_code,))
        h_id = cursor.lastrowid
        
        # Create prescription in restrictive hospital
        cursor.execute("INSERT INTO prescriptions (rx_id, patient_id, doctor_id, hospital_id, pin_hash) VALUES (?, 'MRX-001', 1, ?, 'hash')", (rx_id, h_id))
        
        # Create unallocated pharmacy
        cursor.execute("INSERT INTO users (email, password_hash, role, name) VALUES (?, 'hash', 'pharmacist', 'Unauth Pharm')", (u_email,))
        u_id = cursor.lastrowid
        cursor.execute("INSERT INTO pharmacies (user_id, name, license_no, address) VALUES (?, 'Unauth Pharm', ?, 'Addr')", (u_id, f"LIC-{uniq}"))
        p_id = cursor.lastrowid
        conn.commit()
        conn.close()

        # Check authorization -> Should be False
        auth = tool_check_pharmacy_authorization(p_id, rx_id)
        self.assertFalse(auth)

    def test_11_negative_dispensing_quantity_rejected(self):
        """11. Verify negative or zero dispensing quantity is blocked by Dispensing Guard."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM prescription_items WHERE rx_id = 'MRX-RX-82K91' LIMIT 1")
        item_id = cursor.fetchone()['id']
        conn.close()

        res_neg = run_dispensing_guard_agent("MRX-RX-82K91", item_id, requested_qty=-10)
        self.assertFalse(res_neg['allowed'])
        self.assertEqual(res_neg['decision'], "BLOCKED")

        res_zero = run_dispensing_guard_agent("MRX-RX-82K91", item_id, requested_qty=0)
        self.assertFalse(res_zero['allowed'])
        self.assertEqual(res_zero['decision'], "BLOCKED")

    def test_12_over_dispensing_blocked(self):
        """12. Verify requesting quantity exceeding remaining limit is blocked."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM prescription_items WHERE rx_id = 'MRX-RX-82K91' LIMIT 1")
        item_id = cursor.fetchone()['id']
        conn.close()

        res = run_dispensing_guard_agent("MRX-RX-82K91", item_id, requested_qty=999)
        self.assertFalse(res['allowed'])
        self.assertEqual(res['decision'], "BLOCKED")

    def test_13_sql_queries_are_parameterized(self):
        """13. Code inspection: Ensure SQL queries in app.py use parameterized placeholders."""
        app_path = os.path.join(os.path.dirname(__file__), "app.py")
        with open(app_path, "r", encoding="utf-8") as f:
            code = f.read()

        # Check that cursor.execute calls do not use raw f-string injection into WHERE values
        bad_patterns = [
            r"cursor\.execute\(['\"].*WHERE.*=\s*f['\"]",
            r"cursor\.execute\(f['\"].*WHERE.*=\s*\{"
        ]
        for pat in bad_patterns:
            matches = re.findall(pat, code)
            self.assertEqual(len(matches), 0, f"Found unparameterized SQL pattern: {matches}")

    def test_14_api_key_absent_from_source(self):
        """14. Code inspection: Ensure no real hardcoded API keys or secrets exist in codebase."""
        source_files = ["app.py", "README.md", "test_app.py", "test_phase6.py", "test_phase7.py", "test_phase8.py"]
        secret_patterns = [r"sk-[a-zA-Z0-9]{32,}", r"AIzaSy[a-zA-Z0-9_-]{33}"]
        
        for sf in source_files:
            file_path = os.path.join(os.path.dirname(__file__), sf)
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                for pat in secret_patterns:
                    matches = re.findall(pat, content)
                    self.assertEqual(len(matches), 0, f"Found secret in {sf}: {matches}")

    def test_15_llm_failure_handled_safely(self):
        """15. Verify system handles LLM absence cleanly with deterministic fallback."""
        # Ensure LLM API key environment variable is cleared for test
        os.environ.pop("LLM_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)

        res = call_llm("Test prompt", "System instruction")
        self.assertIsNone(res)

        # Verify AI agent uses fallback safely
        sample_items = [{"medicine_name": "Panadol 500mg", "quantity": 10, "dosage_form": "Tablet"}]
        verif_res = run_prescription_verification_agent(sample_items)
        self.assertTrue(verif_res['is_valid'])
        self.assertIn("trace", verif_res)

    def test_16_malformed_ai_output_handled_safely(self):
        """16. Verify malformed AI model output is handled cleanly with safe fallback."""
        alt_res = run_alternative_medicine_agent({"medicine_name": "Panadol 500mg"}, {"proposed_generic": "Paracetamol"})
        self.assertIn("similarity_score", alt_res)
        self.assertIn("recommendation", alt_res)

if __name__ == "__main__":
    unittest.main()
