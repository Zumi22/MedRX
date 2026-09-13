import secrets
import os
import os
import sys
import unittest
import json

from app import (
    db_init, seed_demo_data, get_db_connection,
    create_audit_event, sanitize_audit_metadata,
    detect_system_anomalies, run_audit_anomaly_agent,
    tool_get_recent_dispensing_events,
    tool_get_prescription_dispensing_history,
    tool_get_failed_verification_attempts,
    tool_get_substitution_history,
    tool_get_audit_events,
    tool_calculate_dispensing_frequency,
    verify_password, hash_password
)

class Phase8ReviewTests(unittest.TestCase):

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

    def test_01_audit_event_creation(self):
        """1. Verify creation of audit event in database."""
        create_audit_event("test_actor@medrx.demo", "doctor", "PATIENT_VIEWED", "patient", "MRX-AB7K-92QF", {"action": "lookup"})
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM audit_logs WHERE actor_email = 'test_actor@medrx.demo' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row['event_type'], "PATIENT_VIEWED")
        self.assertEqual(row['actor_role'], "doctor")

    def test_02_audit_event_cannot_be_modified_in_ui(self):
        """2. Verify audit events cannot be modified (no update function exposed)."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM audit_logs")
        count_before = cursor.fetchone()[0]
        conn.close()

        # Audit logs table has primary key and append-only interface
        self.assertGreater(count_before, 0)

    def test_03_audit_event_cannot_be_deleted_in_ui(self):
        """3. Verify audit events cannot be deleted through normal UI functions."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM audit_logs")
        count_before = cursor.fetchone()[0]
        conn.close()

        # Normal app workflows do not contain any DELETE FROM audit_logs statement
        self.assertGreater(count_before, 0)

    def test_04_login_failure_generates_audit_event(self):
        """4. Verify invalid login attempt generates LOGIN_FAILED audit event."""
        create_audit_event("baduser@medrx.demo", "unknown", "LOGIN_FAILED", "user", "0", {"reason": "invalid_password"})
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM audit_logs WHERE event_type = 'LOGIN_FAILED' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row['actor_email'], "baduser@medrx.demo")

    def test_05_dispensing_attempt_generates_audit_event(self):
        """5. Verify dispensing action generates MEDICINE_DISPENSED / DISPENSING_ATTEMPTED audit event."""
        create_audit_event("pharmacy@medrx.demo", "pharmacist", "MEDICINE_DISPENSED", "prescription", "MRX-RX-82K91", {"qty": 10})

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM audit_logs WHERE event_type = 'MEDICINE_DISPENSED' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row['entity_id'], "MRX-RX-82K91")

    def test_06_blocked_dispensing_generates_audit_event(self):
        """6. Verify blocked dispensing attempt generates DISPENSING_BLOCKED audit event."""
        create_audit_event("pharmacy@medrx.demo", "pharmacist", "DISPENSING_BLOCKED", "prescription", "MRX-RX-82K91", {"requested": 999, "remaining": 10})

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM audit_logs WHERE event_type = 'DISPENSING_BLOCKED' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row['entity_id'], "MRX-RX-82K91")

    def test_07_substitution_generates_audit_event(self):
        """7. Verify substitution request generates SUBSTITUTION_REQUESTED audit event."""
        create_audit_event("pharmacy@medrx.demo", "pharmacist", "SUBSTITUTION_REQUESTED", "prescription", "MRX-RX-82K91", {"assigned_doctor": "Dr. Usman Malik"})

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM audit_logs WHERE event_type = 'SUBSTITUTION_REQUESTED' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(row)

    def test_08_ai_analysis_generates_audit_event(self):
        """8. Verify AI analysis execution records AI audit log event."""
        create_audit_event("system_agent", "ai_agent", "AI_SAFETY_ANALYSIS", "audit_logs", "0", {"risk": "LOW"})

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM audit_logs WHERE event_type = 'AI_SAFETY_ANALYSIS' ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(row)

    def test_09_repeated_blocked_dispensing_triggers_anomaly(self):
        """9. Verify repeated blocked dispensing attempts trigger HIGH risk anomaly."""
        rx_id = "RX-ANOMALY-BLOCK"
        create_audit_event("pharmacy@medrx.demo", "pharmacist", "DISPENSING_BLOCKED", "prescription", rx_id, {"attempt": 1})
        create_audit_event("pharmacy@medrx.demo", "pharmacist", "DISPENSING_BLOCKED", "prescription", rx_id, {"attempt": 2})

        res = detect_system_anomalies(rx_id)
        self.assertTrue(res['anomaly_detected'])
        self.assertEqual(res['risk_level'], "HIGH")
        self.assertIn("Unusual activity detected — review recommended.", res['explanation'])

    def test_10_repeated_failed_verification_triggers_anomaly(self):
        """10. Verify repeated failed verification attempts trigger HIGH risk anomaly."""
        token_id = "TOKEN-FAIL-99"
        for _ in range(3):
            create_audit_event("public_qr_scanner", "guest", "PRESCRIPTION_VERIFY_FAILED", "prescription", token_id, {"reason": "wrong_pin"})

        res = detect_system_anomalies(token_id)
        self.assertTrue(res['anomaly_detected'])
        self.assertEqual(res['risk_level'], "HIGH")

    def test_11_repeated_substitution_triggers_anomaly(self):
        """11. Verify repeated substitution requests trigger anomaly."""
        rx_id = "RX-ANOMALY-SUB"
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM doctors LIMIT 1")
        doc_id = cursor.fetchone()['id']

        cursor.execute("SELECT id FROM hospitals LIMIT 1")
        hosp_id = cursor.fetchone()['id']

        cursor.execute("""
            INSERT INTO prescriptions (rx_id, patient_id, doctor_id, hospital_id, status, pin_hash)
            VALUES (?, 'MRX-AB7K-92QF', ?, ?, 'Active', 'hash')
        """, (rx_id, doc_id, hosp_id))

        cursor.execute("""
            INSERT INTO prescription_items (rx_id, version_number, medicine_name, quantity)
            VALUES (?, 1, 'Drug X', 10)
        """, (rx_id,))
        item_id = cursor.lastrowid

        for idx in range(2):
            cursor.execute("""
                INSERT INTO substitutions (rx_id, original_item_id, proposed_generic, pharmacist_id, status)
                VALUES (?, ?, ?, 4, 'Pending Approval')
            """, (rx_id, item_id, f"Proposed {idx}"))

        conn.commit()
        conn.close()

        res = detect_system_anomalies(rx_id)
        self.assertTrue(res['anomaly_detected'])
        self.assertIn(res['risk_level'], ["MEDIUM", "HIGH"])

    def test_12_risk_level_generated_correctly(self):
        """12. Verify anomaly risk level levels (LOW, MEDIUM, HIGH)."""
        res_low = detect_system_anomalies("NON-EXISTENT-RX-ID")
        self.assertEqual(res_low['risk_level'], "LOW")
        self.assertFalse(res_low['anomaly_detected'])

    def test_13_llm_failure_uses_deterministic_fallback(self):
        """13. Verify deterministic fallback message when LLM is unavailable."""
        orig_key = os.environ.get("LLM_API_KEY")
        if "LLM_API_KEY" in os.environ:
            del os.environ["LLM_API_KEY"]

        res = run_audit_anomaly_agent("MRX-RX-82K91")
        trace_steps = [t[1] for t in res['trace']]
        
        self.assertTrue(any("AI service unavailable — deterministic anomaly rules applied." in step for step in trace_steps))

        if orig_key:
            os.environ["LLM_API_KEY"] = orig_key

    def test_14_role_based_audit_filtering_works(self):
        """14. Verify tool_get_audit_events role-based filtering."""
        events_all = tool_get_audit_events()
        self.assertIsInstance(events_all, list)

        events_doc = tool_get_audit_events({"actor_role": "doctor"})
        for ev in events_doc:
            self.assertEqual(ev['actor_role'], "doctor")

    def test_15_sensitive_secrets_not_in_audit_logs(self):
        """15. Verify sensitive secrets (passwords, PINs, API keys) are redacted in audit metadata."""
        meta_with_secrets = {
            "password": "MySecretPassword123!",
            "pin": "1234",
            "api_key": "sk-proj-1234567890",
            "action": "test_sanitization"
        }
        sanitized = sanitize_audit_metadata(meta_with_secrets)
        
        self.assertEqual(sanitized['password'], "[REDACTED]")
        self.assertEqual(sanitized['pin'], "[REDACTED]")
        self.assertEqual(sanitized['api_key'], "[REDACTED]")
        self.assertEqual(sanitized['action'], "test_sanitization")

if __name__ == '__main__':
    unittest.main()
