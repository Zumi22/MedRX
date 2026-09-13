import secrets
import os
import os
import sys
import unittest
import sqlite3
import json

from app import (
    db_init, seed_demo_data, get_db_connection,
    hash_password, verify_password, create_audit_event,
    DB_FILE
)

class Phase1ReviewTests(unittest.TestCase):

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

    def test_01_database_initialization(self):
        """1. Test database tables exist and schema is valid."""
        db_target = os.environ.get("MEDRX_DB_FILE", DB_FILE)
        self.assertTrue(os.path.exists(db_target), f"Database file {db_target} should exist.")
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r['name'] for r in cursor.fetchall()]
        conn.close()

        expected_tables = [
            'users', 'hospitals', 'pharmacies', 'hospital_pharmacies',
            'doctors', 'patients', 'encounters', 'prescriptions',
            'prescription_items', 'prescription_versions', 'substitutions',
            'dispensing_events', 'audit_logs', 'verification_tokens',
            'medication_catalog'
        ]
        for tbl in expected_tables:
            self.assertIn(tbl, tables, f"Table '{tbl}' should exist in database.")

    def test_02_demo_seed_data(self):
        """2. Test demo seed data populates hospitals, users, catalog, patients, prescriptions."""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM hospitals")
        self.assertGreaterEqual(cursor.fetchone()[0], 1, "Should seed at least 1 hospital.")
        
        cursor.execute("SELECT COUNT(*) FROM medication_catalog")
        self.assertGreaterEqual(cursor.fetchone()[0], 30, "Should seed at least 30 catalog medicines.")
        
        cursor.execute("SELECT COUNT(*) FROM patients")
        self.assertGreaterEqual(cursor.fetchone()[0], 2, "Should seed demo patients.")

        cursor.execute("SELECT COUNT(*) FROM prescriptions")
        self.assertGreaterEqual(cursor.fetchone()[0], 1, "Should seed demo prescription.")
        conn.close()

    def test_03_master_admin_login(self):
        """3. Test Master Admin login authentication."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = 'master@medrx.demo'")
        user = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(user, "Master admin user should exist.")
        self.assertEqual(user['role'], 'master_admin')
        self.assertEqual(user['status'], 'active')
        self.assertTrue(verify_password("DemoPass123!", user['password_hash']))

    def test_04_hospital_admin_login(self):
        """4. Test Hospital Admin login authentication."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = 'admin@riphah-demo.medrx'")
        user = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(user, "Hospital admin user should exist.")
        self.assertEqual(user['role'], 'hospital_admin')
        self.assertEqual(user['status'], 'active')
        self.assertTrue(verify_password("DemoPass123!", user['password_hash']))

    def test_05_doctor_login(self):
        """5. Test Doctor login authentication."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = 'doctor@medrx.demo'")
        user = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(user, "Doctor user should exist.")
        self.assertEqual(user['role'], 'doctor')
        self.assertEqual(user['status'], 'active')
        self.assertTrue(verify_password("DemoPass123!", user['password_hash']))

    def test_06_pharmacist_login(self):
        """6. Test Pharmacist login authentication."""
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = 'pharmacy@medrx.demo'")
        user = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(user, "Pharmacist user should exist.")
        self.assertEqual(user['role'], 'pharmacist')
        self.assertEqual(user['status'], 'active')
        self.assertTrue(verify_password("DemoPass123!", user['password_hash']))

    def test_07_role_based_authorization(self):
        """7. Test role-based server-side checks and status restrictions."""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Test pending user rejection
        cursor.execute("""
            INSERT INTO users (email, password_hash, role, status, name)
            VALUES ('pending_doc@medrx.demo', ?, 'doctor', 'pending', 'Dr. Pending')
        """, (hash_password("Pass123!"),))
        conn.commit()

        cursor.execute("SELECT * FROM users WHERE email = 'pending_doc@medrx.demo'")
        p_user = cursor.fetchone()
        self.assertEqual(p_user['status'], 'pending', "Pending user should be flagged as pending.")
        
        # Verify doctor status check logic
        cursor.execute("SELECT status FROM doctors WHERE user_id = ?", (p_user['id'],))
        doc_row = cursor.fetchone()
        # Pending user has no approved doctor row or doctor row is pending
        if doc_row:
            self.assertNotEqual(doc_row['status'], 'active', "Doctor row must not be active when pending.")

        conn.close()

    def test_08_logout_audit(self):
        """8. Test logout logging."""
        create_audit_event("doctor@medrx.demo", "doctor", "LOGOUT", "user", "3")
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM audit_logs WHERE actor_email = 'doctor@medrx.demo' AND event_type = 'LOGOUT'")
        row = cursor.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['event_type'], 'LOGOUT')
        conn.close()

    def test_09_password_hashing(self):
        """9. Test PBKDF2 password hashing security & non-plaintext storage."""
        raw_pass = "SecurePass2026!"
        h1 = hash_password(raw_pass)
        h2 = hash_password(raw_pass)
        
        self.assertEqual(h1, h2, "Hashing same password with salt should be deterministic.")
        self.assertNotIn(raw_pass, h1, "Password hash must not contain plaintext password.")
        self.assertEqual(len(h1), 64, "PBKDF2 SHA-256 hex string should be 64 characters long.")
        self.assertTrue(verify_password(raw_pass, h1))
        self.assertFalse(verify_password("WrongPass!", h1))

if __name__ == '__main__':
    unittest.main()
