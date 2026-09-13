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
    tool_check_pharmacy_authorization, DB_FILE
)

class Phase2ReviewTests(unittest.TestCase):

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

    def test_01_hospital_creation_and_status(self):
        """Test Master Admin registering a new hospital and toggling active/suspended status."""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO hospitals (name, code, address, status, allow_all_pharmacies)
            VALUES ('City General Hospital', 'CITY-HOSP-01', 'Downtown Area', 'active', 0)
        """, ())
        hosp_id = cursor.lastrowid
        conn.commit()

        cursor.execute("SELECT * FROM hospitals WHERE id = ?", (hosp_id,))
        hosp = cursor.fetchone()
        self.assertEqual(hosp['code'], 'CITY-HOSP-01')
        self.assertEqual(hosp['status'], 'active')

        # Toggle status to suspended
        cursor.execute("UPDATE hospitals SET status = 'suspended' WHERE id = ?", (hosp_id,))
        conn.commit()

        cursor.execute("SELECT status FROM hospitals WHERE id = ?", (hosp_id,))
        self.assertEqual(cursor.fetchone()['status'], 'suspended')
        conn.close()

    def test_02_hospital_admin_creation(self):
        """Test Master Admin creating a Hospital Admin user."""
        conn = get_db_connection()
        cursor = conn.cursor()
        pass_h = hash_password("AdminPass123!")

        cursor.execute("""
            INSERT INTO users (email, password_hash, role, status, name)
            VALUES ('cityadmin@medrx.demo', ?, 'hospital_admin', 'active', 'City Admin')
        """, (pass_h,))
        u_id = cursor.lastrowid
        conn.commit()

        cursor.execute("SELECT * FROM users WHERE id = ?", (u_id,))
        u = cursor.fetchone()
        self.assertEqual(u['role'], 'hospital_admin')
        self.assertTrue(verify_password("AdminPass123!", u['password_hash']))
        conn.close()

    def test_03_doctor_registration_and_approval(self):
        """Test Doctor self-registration (pending) and Hospital Admin approval."""
        conn = get_db_connection()
        cursor = conn.cursor()
        pass_h = hash_password("DocPass123!")

        # 1. Doctor self-registers
        cursor.execute("""
            INSERT INTO users (email, password_hash, role, status, name)
            VALUES ('newdoc@medrx.demo', ?, 'doctor', 'pending', 'Dr. New Doc')
        """, (pass_h,))
        u_id = cursor.lastrowid

        cursor.execute("""
            INSERT INTO doctors (user_id, hospital_id, name, license_no, specialty, status)
            VALUES (?, 1, 'Dr. New Doc', 'PMC-77110-C', 'Pulmonology', 'pending')
        """, (u_id,))
        doc_id = cursor.lastrowid
        conn.commit()

        # Check pending status
        cursor.execute("SELECT status FROM doctors WHERE id = ?", (doc_id,))
        self.assertEqual(cursor.fetchone()['status'], 'pending')

        # 2. Hospital Admin approves doctor
        cursor.execute("UPDATE doctors SET status = 'active' WHERE id = ?", (doc_id,))
        cursor.execute("UPDATE users SET status = 'active' WHERE id = ?", (u_id,))
        conn.commit()

        cursor.execute("SELECT status FROM doctors WHERE id = ?", (doc_id,))
        self.assertEqual(cursor.fetchone()['status'], 'active')
        cursor.execute("SELECT status FROM users WHERE id = ?", (u_id,))
        self.assertEqual(cursor.fetchone()['status'], 'active')
        conn.close()

    def test_04_pharmacy_registration_and_activation(self):
        """Test Pharmacy registration and activation status."""
        conn = get_db_connection()
        cursor = conn.cursor()
        pass_h = hash_password("PharmPass123!")

        cursor.execute("""
            INSERT INTO users (email, password_hash, role, status, name)
            VALUES ('citypharm@medrx.demo', ?, 'pharmacist', 'active', 'City Pharmacist')
        """, (pass_h,))
        u_id = cursor.lastrowid

        cursor.execute("""
            INSERT INTO pharmacies (user_id, name, license_no, address, status)
            VALUES (?, 'City Care Pharmacy', 'PHARM-CITY-99', 'Main Street', 'active')
        """, (u_id,))
        p_id = cursor.lastrowid
        conn.commit()

        cursor.execute("SELECT * FROM pharmacies WHERE id = ?", (p_id,))
        p = cursor.fetchone()
        self.assertEqual(p['status'], 'active')
        self.assertEqual(p['license_no'], 'PHARM-CITY-99')
        conn.close()

    def test_05_hospital_pharmacy_allocation(self):
        """Test explicitly allocating a pharmacy to a hospital."""
        conn = get_db_connection()
        cursor = conn.cursor()

        # Allocate pharmacy #1 to hospital #1
        cursor.execute("""
            INSERT OR IGNORE INTO hospital_pharmacies (hospital_id, pharmacy_id, allocated_by)
            VALUES (1, 1, 2)
        """, ())
        conn.commit()

        cursor.execute("SELECT * FROM hospital_pharmacies WHERE hospital_id = 1 AND pharmacy_id = 1")
        hp = cursor.fetchone()
        self.assertIsNotNone(hp)
        conn.close()

    def test_06_allow_all_pharmacies_option(self):
        """Test allow-all-pharmacies toggle and authorization logic."""
        conn = get_db_connection()
        cursor = conn.cursor()

        # Create a private hospital with allow_all_pharmacies = 0
        cursor.execute("""
            INSERT INTO hospitals (name, code, address, status, allow_all_pharmacies)
            VALUES ('Private Clinic', 'PRIV-01', 'Private Road', 'active', 0)
        """, ())
        priv_hosp_id = cursor.lastrowid
        conn.commit()
        conn.close()

        # Unallocated pharmacy checking authorization
        auth_unallocated = tool_check_pharmacy_authorization(1, priv_hosp_id)
        self.assertFalse(auth_unallocated, "Unallocated pharmacy must be denied access when allow_all_pharmacies = 0.")

        # Toggle allow_all_pharmacies = 1
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE hospitals SET allow_all_pharmacies = 1 WHERE id = ?", (priv_hosp_id,))
        conn.commit()
        conn.close()

        auth_toggled = tool_check_pharmacy_authorization(1, priv_hosp_id)
        self.assertTrue(auth_toggled, "Pharmacy must be authorized when allow_all_pharmacies = 1.")

    def test_07_phase2_audit_logging(self):
        """Test audit logging for Phase 2 administrative actions."""
        create_audit_event("admin@riphah-demo.medrx", "hospital_admin", "USER_APPROVED", "doctor", "10", {"specialty": "Cardiology"})
        create_audit_event("master@medrx.demo", "master_admin", "HOSPITAL_CREATED", "hospital", "CITY-01", {})
        create_audit_event("admin@riphah-demo.medrx", "hospital_admin", "PHARMACY_ALLOCATED", "pharmacy", "1", {"hospital_id": 1})

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM audit_logs WHERE event_type IN ('USER_APPROVED', 'HOSPITAL_CREATED', 'PHARMACY_ALLOCATED')")
        logs = cursor.fetchall()
        self.assertGreaterEqual(len(logs), 3)
        conn.close()

if __name__ == '__main__':
    unittest.main()
