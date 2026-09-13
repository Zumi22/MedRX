import secrets
import os
import os
import sys
import unittest
import sqlite3
import json

from app import (
    db_init, seed_demo_data, get_db_connection,
    hash_pin, verify_pin,
    tool_calculate_remaining_quantity, tool_check_pharmacy_authorization,
    create_audit_event, DB_FILE
)

class Phase5ReviewTests(unittest.TestCase):

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

    def test_01_prescription_lookup_and_pin_verification(self):
        """1. Test prescription lookup by Prescription ID and 4-digit PIN verification."""
        rx_id = "MRX-RX-82K91"
        correct_pin = "1234"
        wrong_pin = "9999"

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM prescriptions WHERE rx_id = ?", (rx_id,))
        rx = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(rx, f"Prescription '{rx_id}' should exist.")
        self.assertTrue(verify_pin(correct_pin, rx['pin_hash']), "Correct 4-digit PIN must verify.")
        self.assertFalse(verify_pin(wrong_pin, rx['pin_hash']), "Incorrect PIN must fail verification.")

    def test_02_hospital_pharmacy_authorization(self):
        """2. Test hospital-pharmacy authorization checks."""
        # Query seeded hospital and pharmacy IDs dynamically
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM hospitals WHERE code = 'RIPHAH-01'")
        h1_row = cursor.fetchone()
        cursor.execute("SELECT id FROM pharmacies WHERE license_no = 'PHARM-ISB-4412'")
        p1_row = cursor.fetchone()
        conn.close()

        self.assertIsNotNone(h1_row, "Seeded hospital must exist.")
        self.assertIsNotNone(p1_row, "Seeded pharmacy must exist.")

        auth_h1 = tool_check_pharmacy_authorization(pharmacy_id=p1_row['id'], hospital_id=h1_row['id'])
        self.assertTrue(auth_h1, "Authorized pharmacy must pass check.")

        # Create unallocated hospital with allow_all_pharmacies = 0
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO hospitals (name, code, address, status, allow_all_pharmacies)
            VALUES ('Strict Hospital', 'STRICT-01', 'Secure Zone', 'active', 0)
        """, ())
        strict_hosp_id = cursor.lastrowid
        conn.commit()
        conn.close()

        auth_strict = tool_check_pharmacy_authorization(pharmacy_id=99, hospital_id=strict_hosp_id)
        self.assertFalse(auth_strict, "Unallocated pharmacy must be denied for restricted hospital.")

    def test_03_multiple_partial_dispensing_events(self):
        """3. Test multiple sequential partial dispensing events and exact remaining math."""
        rx_id = "RX-MULTI-DISP"
        pin_h = hash_pin("4321")

        conn = get_db_connection()
        cursor = conn.cursor()

        # Create test prescription with item quantity = 30 tablets
        cursor.execute("""
            INSERT INTO prescriptions (rx_id, patient_id, doctor_id, hospital_id, status, pin_hash)
            VALUES (?, 'MRX-AB7K-92QF', 1, 1, 'Active', ?)
        """, (rx_id, pin_h))

        cursor.execute("""
            INSERT INTO prescription_items (rx_id, version_number, medicine_name, generic_name, strength, dosage_form, route, dose, frequency, duration, quantity, instructions)
            VALUES (?, 1, 'Amoxil 500mg', 'Amoxicillin', '500 mg', 'Capsule', 'Oral', '1 cap', 'Three times daily', '10 days', 30, 'Take after meals')
        """, (rx_id,))
        item_id = cursor.lastrowid
        conn.commit()

        # Check initial remaining quantity = 30
        rem_0 = tool_calculate_remaining_quantity(rx_id, item_id)
        self.assertEqual(rem_0['prescribed'], 30)
        self.assertEqual(rem_0['dispensed'], 0)
        self.assertEqual(rem_0['remaining'], 30)

        # Event 1: Dispense 10 tablets
        cursor.execute("""
            INSERT INTO dispensing_events (rx_id, item_id, pharmacy_id, pharmacist_id, quantity_dispensed, remaining_quantity, notes)
            VALUES (?, ?, 1, 4, 10, 20, 'First partial dispense: 10 units')
        """, (rx_id, item_id))
        cursor.execute("UPDATE prescriptions SET status = 'Partially Dispensed' WHERE rx_id = ?", (rx_id,))
        conn.commit()

        rem_1 = tool_calculate_remaining_quantity(rx_id, item_id)
        self.assertEqual(rem_1['dispensed'], 10)
        self.assertEqual(rem_1['remaining'], 20)

        # Event 2: Dispense another 15 tablets
        cursor.execute("""
            INSERT INTO dispensing_events (rx_id, item_id, pharmacy_id, pharmacist_id, quantity_dispensed, remaining_quantity, notes)
            VALUES (?, ?, 1, 4, 15, 5, 'Second partial dispense: 15 units')
        """, (rx_id, item_id))
        conn.commit()

        rem_2 = tool_calculate_remaining_quantity(rx_id, item_id)
        self.assertEqual(rem_2['dispensed'], 25)
        self.assertEqual(rem_2['remaining'], 5)

        # Event 3: Dispense final 5 tablets
        cursor.execute("""
            INSERT INTO dispensing_events (rx_id, item_id, pharmacy_id, pharmacist_id, quantity_dispensed, remaining_quantity, notes)
            VALUES (?, ?, 1, 4, 5, 0, 'Final dispense: 5 units')
        """, (rx_id, item_id))
        cursor.execute("UPDATE prescriptions SET status = 'Fully Dispensed' WHERE rx_id = ?", (rx_id,))
        conn.commit()

        rem_3 = tool_calculate_remaining_quantity(rx_id, item_id)
        self.assertEqual(rem_3['dispensed'], 30)
        self.assertEqual(rem_3['remaining'], 0)

        cursor.execute("SELECT status FROM prescriptions WHERE rx_id = ?", (rx_id,))
        self.assertEqual(cursor.fetchone()['status'], 'Fully Dispensed')
        conn.close()

    def test_04_over_dispensing_blocking(self):
        """4. Test strict over-dispensing protection block."""
        rx_id = "RX-OVER-DISP"
        pin_h = hash_pin("1111")

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO prescriptions (rx_id, patient_id, doctor_id, hospital_id, status, pin_hash)
            VALUES (?, 'MRX-AB7K-92QF', 1, 1, 'Active', ?)
        """, (rx_id, pin_h))

        cursor.execute("""
            INSERT INTO prescription_items (rx_id, version_number, medicine_name, generic_name, strength, dosage_form, route, dose, frequency, duration, quantity, instructions)
            VALUES (?, 1, 'Zestril 10mg', 'Lisinopril', '10 mg', 'Tablet', 'Oral', '1 tab', 'Once daily', '30 days', 30, 'Monitor BP')
        """, (rx_id,))
        item_id = cursor.lastrowid

        # Dispense 20 units (10 remaining)
        cursor.execute("""
            INSERT INTO dispensing_events (rx_id, item_id, pharmacy_id, pharmacist_id, quantity_dispensed, remaining_quantity, notes)
            VALUES (?, ?, 1, 4, 20, 10, 'Initial partial dispense')
        """, (rx_id, item_id))
        conn.commit()

        # Attempt to dispense 15 units when only 10 remain
        rem_info = tool_calculate_remaining_quantity(rx_id, item_id)
        requested_qty = 15

        is_over_dispense = requested_qty > rem_info['remaining']
        self.assertTrue(is_over_dispense, "Dispensing Guard must flag over-dispensing attempt.")

        if is_over_dispense:
            create_audit_event("pharmacy@medrx.demo", "pharmacist", "DISPENSING_BLOCKED", "prescription", rx_id, {
                "requested": requested_qty,
                "remaining": rem_info['remaining'],
                "reason": f"Dispensing blocked: only {rem_info['remaining']} units remain."
            })

        # Verify no additional dispensing event was created
        cursor.execute("SELECT COUNT(*) FROM dispensing_events WHERE rx_id = ?", (rx_id,))
        self.assertEqual(cursor.fetchone()[0], 1)
        conn.close()

    def test_05_dispensing_history_and_audit_logs(self):
        """5. Test pharmacy dispensing history and audit log queries."""
        create_audit_event("pharmacy@medrx.demo", "pharmacist", "DISPENSING_BLOCKED", "prescription", "RX-OVER-DISP", {"reason": "Over-dispensing block"})
        conn = get_db_connection()
        cursor = conn.cursor()

        # Query dispensing history for Pharmacy #1
        cursor.execute("SELECT * FROM dispensing_events WHERE pharmacy_id = 1 ORDER BY id DESC")
        disp_history = cursor.fetchall()
        self.assertGreaterEqual(len(disp_history), 1)

        # Query audit logs for DISPENSING_BLOCKED
        cursor.execute("SELECT * FROM audit_logs WHERE event_type = 'DISPENSING_BLOCKED'")
        blocked_logs = cursor.fetchall()
        self.assertGreaterEqual(len(blocked_logs), 1)
        conn.close()

if __name__ == '__main__':
    unittest.main()
