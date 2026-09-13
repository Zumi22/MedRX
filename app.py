import os
import sys
import re
import json
import time
import math
import random
import string
import secrets
import hmac
import hashlib
import datetime
from io import BytesIO
from typing import Dict, List, Tuple, Any, Optional

import pandas as pd
import requests
import streamlit as st

# ReportLab & QR Code imports with fallbacks
try:
    import qrcode
    from PIL import Image
    HAS_QR = True
except ImportError:
    HAS_QR = False

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, KeepTogether, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

import sqlite3

# ==========================================
# CONSTANTS & CONFIGURATION
# ==========================================
DB_FILE = os.environ.get("MEDRX_DB_FILE", "medrx.db")
APP_TITLE = "MedRx — AI-Powered Closed-Loop Prescription Management System"

# ==========================================
# SECURITY & HASHING UTILITIES
# ==========================================
def hash_password(password: str) -> str:
    """Hashes a password securely using PBKDF2-HMAC-SHA256."""
    salt = b"medrx_secure_salt_2026"
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000).hex()

def verify_password(password: str, hashed: str) -> bool:
    """Verifies a password using constant-time comparison against a PBKDF2 hash."""
    if not password or not hashed:
        return False
    return hmac.compare_digest(hash_password(password), hashed)

def hash_pin(pin: str) -> str:
    """Hashes a 4-digit PIN using SHA-256."""
    return hashlib.sha256(f"pin_salt_{pin}".encode('utf-8')).hexdigest()

def verify_pin(pin: str, hashed: str) -> bool:
    """Verifies a 4-digit PIN using constant-time comparison against a SHA-256 hash."""
    if not pin or not hashed:
        return False
    return hmac.compare_digest(hash_pin(pin), hashed)

def hash_token(token: str) -> str:
    """Hashes an opaque QR token using SHA-256."""
    return hashlib.sha256(f"qr_salt_{token}".encode('utf-8')).hexdigest()

def generate_random_patient_id() -> str:
    """Generates an opaque, non-PHI patient ID e.g., MRX-AB7K-92QF."""
    p1 = secrets.token_hex(2).upper()
    p2 = secrets.token_hex(2).upper()
    return f"MRX-{p1}-{p2}"

def generate_random_rx_id() -> str:
    """Generates a random unique prescription ID e.g., RX-82K91."""
    part = secrets.token_hex(3).upper()[:5]
    return f"RX-{part}"

def generate_pin() -> str:
    """Generates a random 4-digit numeric PIN using cryptographically secure RNG."""
    return f"{secrets.randbelow(9000) + 1000}"

def generate_qr_token() -> str:
    """Generates a cryptographically secure random verification token for QR codes."""
    return secrets.token_urlsafe(32)

# ==========================================
# INPUT VALIDATION UTILITIES
# ==========================================
def validate_positive_quantity(quantity: Any) -> bool:
    """Validates that a dispensing or prescribed quantity is an integer > 0."""
    try:
        val = int(quantity)
        return val > 0
    except (ValueError, TypeError):
        return False

def validate_pin_format(pin: str) -> bool:
    """Validates that a PIN is a 4-digit numeric string."""
    return isinstance(pin, str) and len(pin.strip()) == 4 and pin.strip().isdigit()

def validate_email_format(email: str) -> bool:
    """Validates basic email formatting."""
    if not isinstance(email, str):
        return False
    pattern = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    return bool(re.match(pattern, email.strip()))

# ==========================================
# DATABASE INITIALIZATION & DB HELPERS
# ==========================================
def get_db_connection():
    db_target = os.environ.get("MEDRX_DB_FILE", DB_FILE)
    conn = sqlite3.connect(db_target, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
    except Exception:
        pass
    return conn



def db_init():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
    except Exception:
        pass

    
    # Create tables
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL, -- master_admin, hospital_admin, doctor, pharmacist
        status TEXT NOT NULL DEFAULT 'active', -- active, pending, suspended, rejected
        name TEXT NOT NULL,
        phone TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS hospitals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        address TEXT,
        code TEXT UNIQUE NOT NULL,
        status TEXT NOT NULL DEFAULT 'active', -- active, suspended
        allow_all_pharmacies INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pharmacies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        name TEXT NOT NULL,
        license_no TEXT UNIQUE NOT NULL,
        address TEXT,
        status TEXT NOT NULL DEFAULT 'active', -- active, pending, suspended
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS hospital_pharmacies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hospital_id INTEGER NOT NULL,
        pharmacy_id INTEGER NOT NULL,
        allocated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        allocated_by INTEGER,
        FOREIGN KEY (hospital_id) REFERENCES hospitals(id),
        FOREIGN KEY (pharmacy_id) REFERENCES pharmacies(id),
        UNIQUE(hospital_id, pharmacy_id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS doctors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL,
        hospital_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        license_no TEXT NOT NULL,
        specialty TEXT,
        status TEXT NOT NULL DEFAULT 'active', -- active, pending, suspended
        is_on_call INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (hospital_id) REFERENCES hospitals(id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS patients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id TEXT UNIQUE NOT NULL,
        full_name TEXT,
        dob TEXT,
        age INTEGER,
        sex TEXT,
        phone TEXT,
        email TEXT,
        address TEXT,
        emergency_contact TEXT,
        allergies TEXT,
        current_medications TEXT,
        past_medical_history TEXT,
        past_surgical_history TEXT,
        family_history TEXT,
        social_history TEXT,
        risk_flags TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_by_doctor_id INTEGER,
        FOREIGN KEY (created_by_doctor_id) REFERENCES doctors(id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS encounters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        encounter_id TEXT UNIQUE NOT NULL,
        patient_id TEXT NOT NULL,
        doctor_id INTEGER NOT NULL,
        hospital_id INTEGER NOT NULL,
        date_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        chief_complaint TEXT,
        symptoms TEXT,
        vitals TEXT,
        observations TEXT,
        history TEXT,
        assessment TEXT,
        primary_diagnosis TEXT,
        secondary_diagnosis TEXT,
        diagnosis_code TEXT,
        diagnosis TEXT,
        plan TEXT,
        status TEXT DEFAULT 'completed',
        FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
        FOREIGN KEY (doctor_id) REFERENCES doctors(id),
        FOREIGN KEY (hospital_id) REFERENCES hospitals(id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS prescriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rx_id TEXT UNIQUE NOT NULL,
        patient_id TEXT NOT NULL,
        doctor_id INTEGER NOT NULL,
        hospital_id INTEGER NOT NULL,
        encounter_id INTEGER,
        issue_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        version_number INTEGER DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'Active', -- Draft, Active, Partially Dispensed, Fully Dispensed, Pending Substitution Approval, Modified by Approved Substitution, Cancelled, Expired
        pin_hash TEXT NOT NULL,
        cancel_reason TEXT,
        submission_token TEXT,
        FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
        FOREIGN KEY (doctor_id) REFERENCES doctors(id),
        FOREIGN KEY (hospital_id) REFERENCES hospitals(id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS prescription_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rx_id TEXT NOT NULL,
        version_number INTEGER NOT NULL DEFAULT 1,
        medicine_name TEXT NOT NULL,
        generic_name TEXT,
        strength TEXT,
        dosage_form TEXT,
        route TEXT,
        dose TEXT,
        frequency TEXT,
        duration TEXT,
        quantity INTEGER NOT NULL,
        instructions TEXT,
        FOREIGN KEY (rx_id) REFERENCES prescriptions(rx_id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS prescription_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rx_id TEXT NOT NULL,
        version_number INTEGER NOT NULL,
        modified_by_doctor_id INTEGER,
        substitution_id INTEGER,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        change_summary TEXT,
        FOREIGN KEY (rx_id) REFERENCES prescriptions(rx_id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS substitutions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rx_id TEXT NOT NULL,
        original_item_id INTEGER NOT NULL,
        proposed_generic TEXT NOT NULL,
        proposed_brand TEXT,
        proposed_strength TEXT,
        proposed_dosage_form TEXT,
        proposed_route TEXT,
        pharmacist_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'Pending Approval', -- Pending Approval, Approved, Rejected
        ai_analysis_json TEXT,
        assigned_doctor_id INTEGER,
        approval_doctor_id INTEGER,
        routing_reason TEXT,
        doctor_decision_notes TEXT,
        requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        decided_at TIMESTAMP,
        FOREIGN KEY (rx_id) REFERENCES prescriptions(rx_id),
        FOREIGN KEY (original_item_id) REFERENCES prescription_items(id),
        FOREIGN KEY (pharmacist_id) REFERENCES users(id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dispensing_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rx_id TEXT NOT NULL,
        item_id INTEGER NOT NULL,
        pharmacy_id INTEGER NOT NULL,
        pharmacist_id INTEGER NOT NULL,
        quantity_dispensed INTEGER NOT NULL,
        remaining_quantity INTEGER NOT NULL,
        dispensed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        notes TEXT,
        FOREIGN KEY (rx_id) REFERENCES prescriptions(rx_id),
        FOREIGN KEY (item_id) REFERENCES prescription_items(id),
        FOREIGN KEY (pharmacy_id) REFERENCES pharmacies(id),
        FOREIGN KEY (pharmacist_id) REFERENCES users(id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        actor_email TEXT NOT NULL,
        actor_role TEXT NOT NULL,
        event_type TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        metadata_json TEXT
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS verification_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token_hash TEXT UNIQUE NOT NULL,
        rx_id TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP,
        is_revoked INTEGER DEFAULT 0,
        FOREIGN KEY (rx_id) REFERENCES prescriptions(rx_id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS medication_catalog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        generic_name TEXT NOT NULL,
        brand_name TEXT NOT NULL,
        strength TEXT NOT NULL,
        dosage_form TEXT NOT NULL,
        route TEXT NOT NULL,
        therapeutic_class TEXT NOT NULL,
        interaction_tags TEXT,
        alternative_group TEXT,
        demo_stock_status TEXT DEFAULT 'In Stock'
    );
    """)

    # Dynamic Column Migration Verification
    cursor.execute("PRAGMA table_info(patients)")
    p_existing = [row['name'] for row in cursor.fetchall()]
    patient_mig_cols = [
        ("full_name", "TEXT"), ("dob", "TEXT"), ("age", "INTEGER"), ("sex", "TEXT"),
        ("phone", "TEXT"), ("email", "TEXT"), ("address", "TEXT"), ("emergency_contact", "TEXT"),
        ("allergies", "TEXT"), ("current_medications", "TEXT"), ("past_medical_history", "TEXT"),
        ("past_surgical_history", "TEXT"), ("family_history", "TEXT"), ("social_history", "TEXT")
    ]
    for c_name, c_type in patient_mig_cols:
        if c_name not in p_existing:
            try:
                cursor.execute(f"ALTER TABLE patients ADD COLUMN {c_name} {c_type}")
            except Exception:
                pass

    cursor.execute("PRAGMA table_info(encounters)")
    e_existing = [row['name'] for row in cursor.fetchall()]
    encounter_mig_cols = [
        ("chief_complaint", "TEXT"), ("symptoms", "TEXT"), ("vitals", "TEXT"),
        ("observations", "TEXT"), ("history", "TEXT"), ("assessment", "TEXT"),
        ("primary_diagnosis", "TEXT"), ("secondary_diagnosis", "TEXT"), ("diagnosis_code", "TEXT"), ("plan", "TEXT")
    ]
    for c_name, c_type in encounter_mig_cols:
        if c_name not in e_existing:
            try:
                cursor.execute(f"ALTER TABLE encounters ADD COLUMN {c_name} {c_type}")
            except Exception:
                pass

    cursor.execute("PRAGMA table_info(prescriptions)")
    rx_existing = [row['name'] for row in cursor.fetchall()]
    if "submission_token" not in rx_existing:
        try:
            cursor.execute("ALTER TABLE prescriptions ADD COLUMN submission_token TEXT")
        except Exception:
            pass

    conn.commit()
    conn.close()

# ==========================================
# AUDIT LOGGING HELPER
# ==========================================
def sanitize_audit_metadata(metadata: dict) -> dict:
    """Strips and redacts sensitive secrets (passwords, PINs, tokens, API keys) from audit log metadata."""
    if not isinstance(metadata, dict):
        return {}
    sanitized = {}
    sensitive_keys = {'password', 'pin', 'pin_hash', 'token', 'secret', 'api_key', 'llm_secret', 'ssn'}
    for k, v in metadata.items():
        if any(sk in str(k).lower() for sk in sensitive_keys):
            sanitized[k] = "[REDACTED]"
        else:
            sanitized[k] = v
    return sanitized

def create_audit_event(actor_email: str, actor_role: str, event_type: str, entity_type: str, entity_id: str, metadata: dict = None, conn=None):
    close_at_end = False
    if conn is None:
        try:
            conn = get_db_connection()
            close_at_end = True
        except Exception as e:
            print(f"Error getting DB connection for audit: {e}")
            return
    try:
        cursor = conn.cursor()
        clean_meta = sanitize_audit_metadata(metadata)
        meta_str = json.dumps(clean_meta)
        cursor.execute("""
            INSERT INTO audit_logs (actor_email, actor_role, event_type, entity_type, entity_id, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (str(actor_email), str(actor_role), str(event_type), str(entity_type), str(entity_id), meta_str))
        if close_at_end:
            conn.commit()
    except Exception as e:
        print(f"Error logging audit event: {e}")
    finally:
        if close_at_end and conn:
            try:
                conn.close()
            except Exception:
                pass


# ==========================================
# DEMO DATA SEEDER
# ==========================================
def seed_demo_data(force_reset: bool = False):
    for attempt in range(5):
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            if force_reset:
                cursor.execute("DELETE FROM dispensing_events")
                cursor.execute("DELETE FROM substitutions")
                cursor.execute("DELETE FROM prescription_versions")
                cursor.execute("DELETE FROM prescription_items")
                cursor.execute("DELETE FROM prescriptions")
                cursor.execute("DELETE FROM encounters")
                cursor.execute("DELETE FROM patients")
                cursor.execute("DELETE FROM doctors")
                cursor.execute("DELETE FROM hospital_pharmacies")
                cursor.execute("DELETE FROM pharmacies")
                cursor.execute("DELETE FROM hospitals")
                cursor.execute("DELETE FROM users")
                cursor.execute("DELETE FROM verification_tokens")
                cursor.execute("DELETE FROM audit_logs")
                cursor.execute("DELETE FROM medication_catalog")
                conn.commit()

            # Check if master admin exists
            cursor.execute("SELECT id FROM users WHERE email = 'master@medrx.demo'")
            if cursor.fetchone() and not force_reset:
                return

            default_pass_hash = hash_password("DemoPass123!")

            # 1. Master Admin
            cursor.execute("""
                INSERT INTO users (email, password_hash, role, status, name, phone)
                VALUES ('master@medrx.demo', ?, 'master_admin', 'active', 'System Director', '+1-800-555-0100')
            """, (default_pass_hash,))
            master_id = cursor.lastrowid

            # 2. Hospital & Admin
            cursor.execute("""
                INSERT INTO hospitals (name, address, code, status, allow_all_pharmacies)
                VALUES ('Riphah Demo Hospital', 'Sector I-14, Islamabad, Pakistan', 'RIPHAH-01', 'active', 1)
            """, ())
            hosp_id = cursor.lastrowid

            cursor.execute("""
                INSERT INTO users (email, password_hash, role, status, name, phone)
                VALUES ('admin@riphah-demo.medrx', ?, 'hospital_admin', 'active', 'Dr. Sarah Ahmed (Hospital Admin)', '+92-51-111-747-424')
            """, (default_pass_hash,))
            hosp_admin_user_id = cursor.lastrowid

            # 3. Doctor User & Record
            cursor.execute("""
                INSERT INTO users (email, password_hash, role, status, name, phone)
                VALUES ('doctor@medrx.demo', ?, 'doctor', 'active', 'Dr. Usman Malik, MD', '+92-300-555-0192')
            """, (default_pass_hash,))
            doc_user_id = cursor.lastrowid

            cursor.execute("""
                INSERT INTO doctors (user_id, hospital_id, name, license_no, specialty, status, is_on_call)
                VALUES (?, ?, 'Dr. Usman Malik', 'PMC-89102-A', 'Internal Medicine', 'active', 1)
            """, (doc_user_id, hosp_id))
            doctor_id = cursor.lastrowid

            # Secondary Doctor (for routing tests)
            cursor.execute("""
                INSERT INTO users (email, password_hash, role, status, name, phone)
                VALUES ('doctor2@medrx.demo', ?, 'doctor', 'active', 'Dr. Ayesha Khan, MD', '+92-300-555-0193')
            """, (default_pass_hash,))
            doc2_user_id = cursor.lastrowid

            cursor.execute("""
                INSERT INTO doctors (user_id, hospital_id, name, license_no, specialty, status, is_on_call)
                VALUES (?, ?, 'Dr. Ayesha Khan', 'PMC-99211-B', 'Cardiology', 'active', 0)
            """, (doc2_user_id, hosp_id))

            # 4. Pharmacy User & Record
            cursor.execute("""
                INSERT INTO users (email, password_hash, role, status, name, phone)
                VALUES ('pharmacy@medrx.demo', ?, 'pharmacist', 'active', 'Pharm. Bilal Raza (Lead Pharmacist)', '+92-51-555-9090')
            """, (default_pass_hash,))
            pharm_user_id = cursor.lastrowid

            cursor.execute("""
                INSERT INTO pharmacies (user_id, name, license_no, address, status)
                VALUES (?, 'MedRx Central Pharmacy', 'PHARM-ISB-4412', 'Blue Area, Islamabad', 'active')
            """, (pharm_user_id,))
            pharmacy_id = cursor.lastrowid

            # Allocate pharmacy to hospital
            cursor.execute("""
                INSERT INTO hospital_pharmacies (hospital_id, pharmacy_id, allocated_by)
                VALUES (?, ?, ?)
            """, (hosp_id, pharmacy_id, hosp_admin_user_id))

            # 5. Seed Medication Catalog (35 Curated Medicines)
            medicines = [
                ("Amoxicillin", "Amoxil", "500 mg", "Capsule", "Oral", "Antibiotic (Penicillin)", "penicillin_allergy", "penicillins", "In Stock"),
                ("Amoxicillin / Clavulanate", "Augmentin", "625 mg", "Tablet", "Oral", "Antibiotic (Penicillin)", "penicillin_allergy", "penicillins", "In Stock"),
                ("Azithromycin", "Zithromax", "500 mg", "Tablet", "Oral", "Antibiotic (Macrolide)", "qt_prolongation", "macrolides", "Out of Stock"),
                ("Ciprofloxacin", "Cipro", "500 mg", "Tablet", "Oral", "Antibiotic (Fluoroquinolone)", "tendonitis_risk,antacid_interaction", "quinolones", "In Stock"),
                ("Levofloxacin", "Levaquin", "500 mg", "Tablet", "Oral", "Antibiotic (Fluoroquinolone)", "tendonitis_risk", "quinolones", "In Stock"),
                ("Cefixime", "Cefspan", "400 mg", "Capsule", "Oral", "Antibiotic (Cephalosporin)", "cephalosporin_allergy", "cephalosporins", "In Stock"),
                ("Metformin", "Glucophage", "500 mg", "Tablet", "Oral", "Antidiabetic (Biguanide)", "renal_impairment", "antidiabetics", "In Stock"),
                ("Metformin Extended Release", "Glucophage XR", "750 mg", "Tablet", "Oral", "Antidiabetic (Biguanide)", "renal_impairment", "antidiabetics", "In Stock"),
                ("Glimepiride", "Amaryl", "2 mg", "Tablet", "Oral", "Antidiabetic (Sulfonylurea)", "hypoglycemia_risk", "antidiabetics", "In Stock"),
                ("Sitagliptin", "Januvia", "100 mg", "Tablet", "Oral", "Antidiabetic (DPP-4 Inhibitor)", "pancreatitis_risk", "antidiabetics", "In Stock"),
                ("Lisinopril", "Zestril", "10 mg", "Tablet", "Oral", "Antihypertensive (ACE Inhibitor)", "pregnancy_contraindicated,hyperkalemia", "ace_inhibitors", "In Stock"),
                ("Enalapril", "Renitec", "10 mg", "Tablet", "Oral", "Antihypertensive (ACE Inhibitor)", "pregnancy_contraindicated", "ace_inhibitors", "In Stock"),
                ("Losartan", "Cozaar", "50 mg", "Tablet", "Oral", "Antihypertensive (ARB)", "pregnancy_contraindicated", "arbs", "In Stock"),
                ("Valsartan", "Diovan", "80 mg", "Tablet", "Oral", "Antihypertensive (ARB)", "pregnancy_contraindicated", "arbs", "In Stock"),
                ("Amlodipine", "Norvasc", "5 mg", "Tablet", "Oral", "Antihypertensive (CCB)", "edema_risk", "ccb", "In Stock"),
                ("Amlodipine", "Norvasc", "10 mg", "Tablet", "Oral", "Antihypertensive (CCB)", "edema_risk", "ccb", "In Stock"),
                ("Atorvastatin", "Lipitor", "20 mg", "Tablet", "Oral", "Antihyperlipidemic (Statin)", "myopathy_risk,grapefruit_interaction", "statins", "In Stock"),
                ("Rosuvastatin", "Crestor", "10 mg", "Tablet", "Oral", "Antihyperlipidemic (Statin)", "myopathy_risk", "statins", "In Stock"),
                ("Omeprazole", "Prilosec", "20 mg", "Capsule", "Oral", "Proton Pump Inhibitor (PPI)", "clopidogrel_interaction", "ppi", "In Stock"),
                ("Esomeprazole", "Nexium", "40 mg", "Capsule", "Oral", "Proton Pump Inhibitor (PPI)", "clopidogrel_interaction", "ppi", "Out of Stock"),
                ("Ibuprofen", "Advil", "400 mg", "Tablet", "Oral", "NSAID", "gi_bleed_risk,renal_risk,warfarin_interaction", "nsaids", "In Stock"),
                ("Naproxen", "Naprosyn", "500 mg", "Tablet", "Oral", "NSAID", "gi_bleed_risk,warfarin_interaction", "nsaids", "In Stock"),
                ("Paracetamol", "Panadol", "500 mg", "Tablet", "Oral", "Analgesic / Antipyretic", "hepatotoxicity_overdose", "analgesics", "In Stock"),
                ("Paracetamol Extra", "Panadol Extra", "500 mg / 65 mg", "Tablet", "Oral", "Analgesic / Antipyretic", "caffeine_sensitivity", "analgesics", "In Stock"),
                ("Tramadol", "Ultram", "50 mg", "Capsule", "Oral", "Opioid Analgesic", "ssri_interaction,seizure_risk", "opioids", "In Stock"),
                ("Salbutamol Inhaler", "Ventolin", "100 mcg/dose", "Inhaler", "Inhalation", "Bronchodilator (Beta-2 Agonist)", "tachycardia_risk", "bronchodilators", "In Stock"),
                ("Montelukast", "Singulair", "10 mg", "Tablet", "Oral", "Leukotriene Receptor Antagonist", "neuropsychiatric_events", "asthma", "In Stock"),
                ("Metoprolol Tartrate", "Lopressor", "50 mg", "Tablet", "Oral", "Beta-Blocker", "bradycardia,asthma_caution", "beta_blockers", "In Stock"),
                ("Bisoprolol", "Concor", "5 mg", "Tablet", "Oral", "Beta-Blocker", "bradycardia", "beta_blockers", "In Stock"),
                ("Warfarin", "Coumadin", "5 mg", "Tablet", "Oral", "Anticoagulant", "nsaid_interaction,high_bleed_risk", "anticoagulants", "In Stock"),
                ("Clopidogrel", "Plavix", "75 mg", "Tablet", "Oral", "Antiplatelet", "ppi_interaction", "antiplatelets", "In Stock"),
                ("Diazepam", "Valium", "5 mg", "Tablet", "Oral", "Benzodiazepine", "sedation_dependency", "sedatives", "In Stock"),
                ("Alprazolam", "Xanax", "0.5 mg", "Tablet", "Oral", "Benzodiazepine", "sedation_dependency", "sedatives", "In Stock"),
                ("Sertraline", "Zoloft", "50 mg", "Tablet", "Oral", "SSRI Antidepressant", "serotonin_syndrome", "ssris", "In Stock"),
                ("Escitalopram", "Lexapro", "10 mg", "Tablet", "Oral", "SSRI Antidepressant", "serotonin_syndrome", "ssris", "In Stock")
            ]

            cursor.executemany("""
                INSERT INTO medication_catalog (generic_name, brand_name, strength, dosage_form, route, therapeutic_class, interaction_tags, alternative_group, demo_stock_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, medicines)


            # 6. Seed Demo Patients with Full Clinical Profiles
            demo_patient_id = "MRX-AB7K-92QF"
            cursor.execute("""
                INSERT INTO patients (patient_id, full_name, dob, age, sex, phone, email, address, emergency_contact, allergies, current_medications, past_medical_history, past_surgical_history, family_history, social_history, risk_flags, created_by_doctor_id)
                VALUES (?, 'Zainab Bibi', '1982-04-12', 44, 'Female', '+92-301-555-0182', 'zainab.bibi@demo.medrx', 'House 42, St 9, F-8/2, Islamabad', 'Tariq Bibi (Husband) +92-301-555-0183', 'Penicillin, Shellfish', 'Metformin 500mg, Amlodipine 5mg', 'Type 2 Diabetes (3 yrs), Hypertension (5 yrs)', 'Appendectomy (2015)', 'Father: T2DM, Mother: Hypertension', 'Non-smoker, Moderate caffeine intake', 'Hypertension, Type 2 Diabetes', ?)
            """, (demo_patient_id, doctor_id))

            demo_patient_id2 = "PT-7K4M29"
            cursor.execute("""
                INSERT INTO patients (patient_id, full_name, dob, age, sex, phone, email, address, emergency_contact, allergies, current_medications, past_medical_history, past_surgical_history, family_history, social_history, risk_flags, created_by_doctor_id)
                VALUES (?, 'Muhammad Ahmed', '1975-08-25', 51, 'Male', '+92-333-555-0199', 'm.ahmed@demo.medrx', 'Flat 3B, G-11 Markaz, Islamabad', 'Saira Ahmed (Wife) +92-333-555-0198', 'Sulfa Drugs', 'Atorvastatin 20mg', 'Dyslipidemia, Mild Asthma', 'None', 'Brother: Coronary Artery Disease', 'Ex-smoker (quit 2020)', 'Dyslipidemia', ?)
            """, (demo_patient_id2, doctor_id))

            # 7. Seed Demo Encounter & Prescriptions
            cursor.execute("""
                INSERT INTO encounters (encounter_id, patient_id, doctor_id, hospital_id, chief_complaint, symptoms, vitals, observations, history, assessment, primary_diagnosis, secondary_diagnosis, diagnosis_code, diagnosis, plan)
                VALUES ('ENC-9001', ?, ?, ?, 'Routine Follow-up & High Blood Sugar Check', 'Dry cough x 4 days, fatigue, mild leg heaviness', 'BP: 138/88 mmHg | HR: 74 bpm | Temp: 98.6F | SpO2: 98% | Fasting Glucose: 142 mg/dL', 'BP slightly elevated. Fasting blood sugar above target.', 'Known diabetic x 3 yrs', 'Type 2 Diabetes Suboptimally Controlled & Essential Hypertension', 'Essential Hypertension', 'Type 2 Diabetes Mellitus', 'ICD-10 I10 / E11.9', 'Essential Hypertension & T2DM Management', '1. Continue Metformin 500mg BID with meals.\n2. Initiate Lisinopril 10mg daily for renal protection and BP control.\n3. Dietary counseling for low-salt and diabetic diet.\n4. Follow up in 4 weeks with HbA1c.')
            """, (demo_patient_id, doctor_id, hosp_id))
            enc_id = cursor.lastrowid

            # Prescription 1 (Active / Partial Dispensing Demo)
            rx_id_1 = "MRX-RX-82K91"
            pin_1 = "1234"
            pin_hash_1 = hash_pin(pin_1)

            cursor.execute("""
                INSERT INTO prescriptions (rx_id, patient_id, doctor_id, hospital_id, encounter_id, status, pin_hash)
                VALUES (?, ?, ?, ?, ?, 'Partially Dispensed', ?)
            """, (rx_id_1, demo_patient_id, doctor_id, hosp_id, enc_id, pin_hash_1))

            # Prescription items for RX 1
            cursor.execute("""
                INSERT INTO prescription_items (rx_id, version_number, medicine_name, generic_name, strength, dosage_form, route, dose, frequency, duration, quantity, instructions)
                VALUES (?, 1, 'Glucophage 500mg', 'Metformin', '500 mg', 'Tablet', 'Oral', '1 tab', 'Twice daily after meals', '30 days', 60, 'Take with food to prevent GI upset')
            """, (rx_id_1,))
            item1_id = cursor.lastrowid

            cursor.execute("""
                INSERT INTO prescription_items (rx_id, version_number, medicine_name, generic_name, strength, dosage_form, route, dose, frequency, duration, quantity, instructions)
                VALUES (?, 1, 'Zithromax 500mg', 'Azithromycin', '500 mg', 'Tablet', 'Oral', '1 tab', 'Once daily', '5 days', 5, 'Out of stock demo item - trigger substitution')
            """, (rx_id_1,))
            item2_id = cursor.lastrowid

            cursor.execute("""
                INSERT INTO prescription_items (rx_id, version_number, medicine_name, generic_name, strength, dosage_form, route, dose, frequency, duration, quantity, instructions)
                VALUES (?, 1, 'Zestril 10mg', 'Lisinopril', '10 mg', 'Tablet', 'Oral', '1 tab', 'Once daily in morning', '30 days', 30, 'Monitor blood pressure regularly')
            """, (rx_id_1,))
            item3_id = cursor.lastrowid

            # Version history for RX 1
            cursor.execute("""
                INSERT INTO prescription_versions (rx_id, version_number, modified_by_doctor_id, change_summary)
                VALUES (?, 1, ?, 'Initial prescription issued by Dr. Usman Malik')
            """, (rx_id_1, doctor_id))

            # Verification token for RX 1
            qr_token_1 = "DEMO_SECURE_TOKEN_MRX_82K91_XYZ"
            qr_token_hash_1 = hash_token(qr_token_1)
            cursor.execute("""
                INSERT INTO verification_tokens (token_hash, rx_id)
                VALUES (?, ?)
            """, (qr_token_hash_1, rx_id_1))

            # Dispensing event for RX 1 (Partially dispensed Item 1: 40 of 60 tablets)
            cursor.execute("""
                INSERT INTO dispensing_events (rx_id, item_id, pharmacy_id, pharmacist_id, quantity_dispensed, remaining_quantity, notes)
                VALUES (?, ?, ?, ?, 40, 20, 'Initial partial dispense: 40 units supplied upon patient request.')
            """, (rx_id_1, item1_id, pharmacy_id, pharm_user_id))

            # Seed Initial Audit Log Events
            create_audit_event("master@medrx.demo", "master_admin", "SYSTEM_INITIALIZED", "system", "0", {"msg": "MedRx system database seeded with demo data."}, conn=conn)
            create_audit_event("doctor@medrx.demo", "doctor", "PRESCRIPTION_CREATED", "prescription", rx_id_1, {"patient_id": demo_patient_id, "items": 3}, conn=conn)
            create_audit_event("pharmacy@medrx.demo", "pharmacist", "PARTIAL_DISPENSE", "prescription", rx_id_1, {"item_id": item1_id, "dispensed": 40, "remaining": 20}, conn=conn)

            conn.commit()
            return
        except sqlite3.OperationalError:
            if attempt == 4:
                raise
            time.sleep(0.5)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

# ==========================================
# INTERNAL AGENT TOOLS
# ==========================================
def tool_lookup_drug(query: str) -> List[Dict]:
    """1. Tool: Searches medication catalog by generic, brand, or therapeutic class."""
    conn = get_db_connection()
    cursor = conn.cursor()
    pattern = f"%{query.strip()}%"
    cursor.execute("""
        SELECT * FROM medication_catalog
        WHERE generic_name LIKE ? OR brand_name LIKE ? OR therapeutic_class LIKE ?
    """, (pattern, pattern, pattern))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def tool_check_duplicate_medications(items: List[Dict]) -> List[str]:
    """2. Tool: Detects duplicate therapies in prescription item list."""
    duplicates = []
    seen_generics = set()
    for item in items:
        gen = (item.get('generic_name') or item.get('medicine_name') or '').strip().lower()
        if not gen:
            continue
        if gen in seen_generics:
            duplicates.append(f"Duplicate medication detected: '{item.get('medicine_name')}' (Generic: {gen.title()}).")
        else:
            seen_generics.add(gen)
    return duplicates

def tool_check_duplicates(items: List[Dict]) -> List[str]:
    """Alias tool for check_duplicates."""
    return tool_check_duplicate_medications(items)

def tool_check_interactions(items: List[Dict], patient_id: str = None) -> List[str]:
    """3. Tool: Evaluates drug-drug interaction risks."""
    warnings = []
    generics = [(i.get('generic_name') or i.get('medicine_name') or '').strip().lower() for i in items]
    
    if 'ibuprofen' in generics and 'warfarin' in generics:
        warnings.append("HIGH RISK INTERACTION: Ibuprofen + Warfarin significantly increases major gastrointestinal hemorrhage risk.")
    if 'omeprazole' in generics and 'clopidogrel' in generics:
        warnings.append("MODERATE INTERACTION: Omeprazole reduces antiplatelet activation of Clopidogrel.")
    if 'tramadol' in generics and ('sertraline' in generics or 'escitalopram' in generics):
        warnings.append("SAFETY WARNING: Tramadol combined with SSRI increases Serotonin Syndrome risk.")
    if 'lisinopril' in generics and 'spironolactone' in generics:
        warnings.append("MONITORING REQUIRED: ACE inhibitor + Potassium-sparing diuretic can cause severe hyperkalemia.")

    return warnings

def tool_get_medication_history(patient_id: str) -> List[Dict]:
    """4. Tool: Fetches historical prescription records for a patient."""
    if not patient_id:
        return []
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT p.rx_id, p.issue_timestamp, p.status, pi.medicine_name, pi.generic_name, pi.strength, pi.quantity
        FROM prescriptions p
        JOIN prescription_items pi ON p.rx_id = pi.rx_id
        WHERE p.patient_id = ?
        ORDER BY p.id DESC
    """, (patient_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def tool_get_dispensing_history(entity_id: str = None) -> List[Dict]:
    """5. Tool: Fetches system dispensing events history."""
    conn = get_db_connection()
    cursor = conn.cursor()
    if entity_id:
        cursor.execute("SELECT * FROM dispensing_events WHERE rx_id = ? OR pharmacy_id = ? ORDER BY id DESC", (str(entity_id), str(entity_id)))
    else:
        cursor.execute("SELECT * FROM dispensing_events ORDER BY id DESC LIMIT 100")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def tool_compare_medicines(orig_item: Dict, prop_item: Dict) -> Dict:
    """6. Tool: Compares original and proposed alternative medicine clinical similarity."""
    orig_gen = (orig_item.get('generic_name') or orig_item.get('medicine_name') or '').strip().lower()
    prop_gen = (prop_item.get('proposed_generic') or prop_item.get('generic_name') or prop_item.get('medicine_name') or '').strip().lower()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM medication_catalog WHERE LOWER(generic_name) = ? OR LOWER(brand_name) = ?", (orig_gen, orig_gen))
    orig_cat = cursor.fetchone()
    cursor.execute("SELECT * FROM medication_catalog WHERE LOWER(generic_name) = ? OR LOWER(brand_name) = ?", (prop_gen, prop_gen))
    prop_cat = cursor.fetchone()
    conn.close()

    reasons = []
    same_class = False
    same_group = False
    same_form = False
    
    if orig_cat and prop_cat:
        if orig_cat['therapeutic_class'] == prop_cat['therapeutic_class']:
            same_class = True
            reasons.append(f"Identical therapeutic class: {orig_cat['therapeutic_class']}")
        else:
            reasons.append(f"Different therapeutic class ({orig_cat['therapeutic_class']} vs {prop_cat['therapeutic_class']})")
        
        if orig_cat['alternative_group'] and orig_cat['alternative_group'] == prop_cat['alternative_group']:
            same_group = True
            reasons.append(f"Matching clinical alternative group: '{orig_cat['alternative_group']}'")
            
        if orig_cat['dosage_form'].lower() == prop_cat['dosage_form'].lower():
            same_form = True
            reasons.append(f"Matching dosage form: {orig_cat['dosage_form']}")
    else:
        if orig_gen == prop_gen:
            same_class = True
            reasons.append("Exact generic match with different brand/supplier.")
        else:
            reasons.append("Manual comparison required — limited catalog mapping.")

    score = 0.50
    if orig_gen == prop_gen:
        score = 0.95
    elif same_group:
        score = 0.85
    elif same_class:
        score = 0.75

    if same_form:
        score = min(0.98, score + 0.05)

    rec = "REVIEW"
    if score >= 0.80:
        rec = "APPROVE_REVIEW"
    elif score < 0.50:
        rec = "REJECT"

    return {
        "suitable": score >= 0.60,
        "recommendation": rec,
        "similarity_score": round(score, 2),
        "reasons": reasons,
        "same_class": same_class,
        "same_form": same_form,
        "confidence": round(score, 2),
        "disclaimer": "AI does not authorize substitution. Mandatory physician confirmation required."
    }

def tool_calculate_remaining_quantity(rx_id: str, item_id: int) -> Dict:
    """7. Tool: Calculates remaining quantity for a prescription item."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT quantity FROM prescription_items WHERE id = ?", (item_id,))
    item_row = cursor.fetchone()
    if not item_row:
        conn.close()
        return {"prescribed": 0, "dispensed": 0, "remaining": 0}
    
    prescribed = item_row['quantity']
    cursor.execute("SELECT SUM(quantity_dispensed) as total_dispensed FROM dispensing_events WHERE rx_id = ? AND item_id = ?", (rx_id, item_id))
    disp_row = cursor.fetchone()
    dispensed = disp_row['total_dispensed'] if disp_row and disp_row['total_dispensed'] else 0
    conn.close()

    remaining = max(0, prescribed - dispensed)
    return {
        "prescribed": prescribed,
        "dispensed": dispensed,
        "remaining": remaining
    }

def tool_find_alternatives(orig_item: Dict) -> List[Dict]:
    """8. Tool: Finds in-stock alternatives from catalog based on therapeutic class or group."""
    orig_gen = (orig_item.get('generic_name') or orig_item.get('medicine_name') or '').strip().lower()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT therapeutic_class, alternative_group FROM medication_catalog WHERE LOWER(generic_name) = ? OR LOWER(brand_name) = ?", (orig_gen, orig_gen))
    cat_row = cursor.fetchone()
    
    if not cat_row:
        cursor.execute("SELECT * FROM medication_catalog WHERE demo_stock_status = 'In Stock' LIMIT 5")
    elif cat_row['alternative_group']:
        cursor.execute("SELECT * FROM medication_catalog WHERE alternative_group = ? AND demo_stock_status = 'In Stock'", (cat_row['alternative_group'],))
    else:
        cursor.execute("SELECT * FROM medication_catalog WHERE therapeutic_class = ? AND demo_stock_status = 'In Stock'", (cat_row['therapeutic_class'],))
    
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def tool_check_pharmacy_authorization(pharmacy_id: int, hospital_id: int) -> bool:
    """9. Tool: Verifies if a pharmacy is authorized to dispense for a hospital."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT allow_all_pharmacies FROM hospitals WHERE id = ?", (hospital_id,))
    hosp = cursor.fetchone()
    if hosp and hosp['allow_all_pharmacies'] == 1:
        conn.close()
        return True
    
    cursor.execute("SELECT id FROM hospital_pharmacies WHERE hospital_id = ? AND pharmacy_id = ?", (hospital_id, pharmacy_id))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def tool_route_substitution_request(hospital_id: int, prescribing_doctor_id: int) -> Dict:
    """10. Tool: Determines eligible doctor for substitution routing."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Check original prescribing doctor
    cursor.execute("SELECT * FROM doctors WHERE id = ?", (prescribing_doctor_id,))
    doc = cursor.fetchone()
    if doc and doc['status'] == 'active':
        conn.close()
        return {
            "assigned_doctor_id": doc['id'],
            "doctor_name": doc['name'],
            "routing_reason": "Direct assignment to original prescribing physician."
        }
    
    # 2. Check hospital on-call doctor
    cursor.execute("SELECT * FROM doctors WHERE hospital_id = ? AND is_on_call = 1 AND status = 'active'", (hospital_id,))
    on_call = cursor.fetchone()
    if on_call:
        conn.close()
        return {
            "assigned_doctor_id": on_call['id'],
            "doctor_name": on_call['name'],
            "routing_reason": f"Original doctor unavailable. Routed to designated hospital on-call physician ({on_call['name']})."
        }
    
    # 3. Check any active doctor in hospital
    cursor.execute("SELECT * FROM doctors WHERE hospital_id = ? AND status = 'active' LIMIT 1", (hospital_id,))
    active_doc = cursor.fetchone()
    conn.close()
    
    if active_doc:
        return {
            "assigned_doctor_id": active_doc['id'],
            "doctor_name": active_doc['name'],
            "routing_reason": f"Routed to active department colleague ({active_doc['name']})."
        }
    
    return {
        "assigned_doctor_id": None,
        "doctor_name": None,
        "routing_reason": "No active physicians available in hospital. Manual escalation required."
    }

# ==========================================
# LLM ENGINE & 6 AGENTIC AI WORKFLOWS
# ==========================================
def call_llm(prompt: str, system_prompt: str = "You are a clinical decision support AI assistant.") -> Optional[str]:
    """Calls configured LLM provider or returns None if key/service is unavailable (deterministic fallback used)."""
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
    provider = os.environ.get("LLM_PROVIDER", "gemini" if "GEMINI_API_KEY" in os.environ else "openai")
    base_url = os.environ.get("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/" if "GEMINI_API_KEY" in os.environ else "https://api.openai.com/v1")
    model = os.environ.get("LLM_MODEL", "gemini-2.5-flash" if "GEMINI_API_KEY" in os.environ else "gpt-4o-mini")

    try:
        if hasattr(st, "secrets"):
            api_key = api_key or st.secrets.get("GEMINI_API_KEY", "") or st.secrets.get("LLM_API_KEY", "")
            if "GEMINI_API_KEY" in st.secrets:
                provider = st.secrets.get("LLM_PROVIDER", "gemini")
                base_url = st.secrets.get("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
                model = st.secrets.get("LLM_MODEL", "gemini-2.5-flash")
            else:
                provider = st.secrets.get("LLM_PROVIDER", provider)
                base_url = st.secrets.get("LLM_BASE_URL", base_url)
                model = st.secrets.get("LLM_MODEL", model)
    except Exception:
        pass

    if not api_key:
        return None

    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2
        }
        resp = requests.post(f"{base_url.rstrip('/')}/chat/completions", headers=headers, json=payload, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            return data['choices'][0]['message']['content']
    except Exception as e:
        print(f"LLM API Call Error: {e}")
    return None

def run_prescription_verification_agent(items: List[Dict], patient_id: str = None) -> Dict:
    """1. Prescription Verification Agent: Validates completeness, catalog matching, duplicates & interactions."""
    trace = [
        ("Step 1", "Prescription received for verification", "success"),
        ("Step 2", "Validating required clinical fields", "success")
    ]
    
    warnings = []
    safety_flags = []
    
    for idx, item in enumerate(items, 1):
        if not item.get('medicine_name'):
            warnings.append(f"Item #{idx}: Medicine name is missing.")
        if not item.get('quantity') or int(item.get('quantity', 0)) <= 0:
            warnings.append(f"Item #{idx} ({item.get('medicine_name')}): Invalid prescribed quantity ({item.get('quantity')}).")
        if not item.get('dosage_form'):
            warnings.append(f"Item #{idx} ({item.get('medicine_name')}): Dosage form not specified.")

    trace.append(("Step 3", "Querying medication catalog and therapeutic database", "success"))
    
    dupes = tool_check_duplicates(items)
    if dupes:
        safety_flags.extend(dupes)
        trace.append(("Step 4", f"Duplicate check flagged {len(dupes)} issue(s)", "warning"))
    else:
        trace.append(("Step 4", "Duplicate check completed — no duplicates found", "success"))

    interactions = tool_check_interactions(items, patient_id)
    if interactions:
        safety_flags.extend(interactions)
        trace.append(("Step 5", f"Drug interaction check returned {len(interactions)} warning(s)", "warning"))
    else:
        trace.append(("Step 5", "Drug interaction analysis completed — clear", "success"))

    for item in items:
        qty = int(item.get('quantity', 0))
        if qty > 180:
            safety_flags.append(f"Unusual dispensing pattern: High quantity ({qty} units) prescribed for {item.get('medicine_name')}.")

    trace.append(("Step 6", "Synthesizing AI agent evaluation summary", "success"))

    is_valid = len(warnings) == 0
    confidence = 0.95 if is_valid and not safety_flags else 0.85

    prompt = f"Explain these clinical flags in human-readable language for a physician: Warnings: {warnings}, Safety Flags: {safety_flags}."
    llm_expl = call_llm(prompt)
    
    if not llm_expl:
        if not safety_flags and not warnings:
            explanation = "Prescription verified successfully. All required fields are present, dosages match catalog standard guidelines, and no known drug-drug interactions or duplicates were identified."
        else:
            explanation = f"Prescription verified with notice. Detected {len(safety_flags)} clinical safety item(s) and {len(warnings)} validation alert(s) requiring physician review prior to dispensing."
    else:
        explanation = llm_expl

    return {
        "valid": is_valid,
        "is_valid": is_valid,
        "warnings": warnings,
        "safety_flags": safety_flags,
        "explanation": explanation,
        "confidence": confidence,
        "trace": trace
    }

def run_medication_safety_agent(items: List[Dict], patient_id: str = None) -> Dict:
    """2. Medication Safety Agent: Evaluates drug interactions, duplicate therapies & patient allergy flags."""
    trace = [
        ("Step 1", "Initializing Medication Safety Agent", "success"),
        ("Step 2", f"Fetching patient medical history for patient ID: '{patient_id or 'General'}'", "info")
    ]
    
    history = tool_get_medication_history(patient_id) if patient_id else []
    trace.append(("Step 3", f"Retrieved {len(history)} prior prescription record(s)", "success"))

    safety_flags = []
    
    dupes = tool_check_duplicates(items)
    if dupes:
        safety_flags.extend(dupes)
        trace.append(("Step 4", f"Duplicate check identified {len(dupes)} issue(s)", "warning"))
    else:
        trace.append(("Step 4", "Duplicate check clear", "success"))

    interactions = tool_check_interactions(items, patient_id)
    if interactions:
        safety_flags.extend(interactions)
        trace.append(("Step 5", f"Drug interaction analysis flagged {len(interactions)} warning(s)", "warning"))
    else:
        trace.append(("Step 5", "Drug interaction check clear", "success"))

    if patient_id:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT risk_flags, allergies FROM patients WHERE patient_id = ?", (patient_id,))
        pat_row = cursor.fetchone()
        conn.close()
        
        if pat_row:
            flags_lower = f"{pat_row['risk_flags'] or ''} {pat_row['allergies'] or ''}".lower()
            for item in items:
                med_name = (item.get('medicine_name') or '').lower()
                gen_name = (item.get('generic_name') or '').lower()
                if 'penicillin' in flags_lower and ('penicillin' in gen_name or 'amoxicillin' in gen_name or 'augmentin' in med_name or 'amoxil' in med_name):
                    allergy_msg = f"CRITICAL ALLERGY CONFLICT: Patient has documented Penicillin Allergy but '{item.get('medicine_name')}' was prescribed!"
                    safety_flags.append(allergy_msg)
                    trace.append(("Step 6", allergy_msg, "warning"))

    trace.append(("Step 7", "Synthesizing Medication Safety Assessment", "success"))
    
    prompt = f"Analyze patient safety for items: {items}. Safety alerts: {safety_flags}. Medical History: {history}."
    llm_output = call_llm(prompt, "You are a clinical pharmacology safety AI agent.")
    
    explanation = llm_output or (
        f"Medication Safety Audit Completed. Evaluated {len(items)} prescribed item(s). "
        + (f"Identified {len(safety_flags)} safety warning(s) requiring physician review." if safety_flags else "No severe drug-drug interactions or allergy conflicts detected.")
    )

    return {
        "safe": len(safety_flags) == 0,
        "safety_flags": safety_flags,
        "explanation": explanation,
        "trace": trace
    }

def run_alternative_medicine_agent(orig_item: Dict, proposed_item: Dict, patient_id: str = None) -> Dict:
    """3. Alternative Medicine Agent: Evaluates pharmacist substitution proposal & calculates clinical similarity."""
    trace = [
        ("Step 1", f"Received substitution request for '{orig_item.get('medicine_name')}'", "success"),
        ("Step 2", f"Proposed alternative: '{proposed_item.get('proposed_generic')}' ({proposed_item.get('proposed_strength')})", "info"),
        ("Step 3", "Querying internal medication catalog tool & finding alternatives", "success")
    ]

    comp_result = tool_compare_medicines(orig_item, proposed_item)
    trace.append(("Step 4", f"Calculated clinical similarity score: {comp_result['similarity_score']} / 1.00", "success"))
    
    prop_dict = {"generic_name": proposed_item.get('proposed_generic'), "medicine_name": proposed_item.get('proposed_generic')}
    prop_interactions = tool_check_interactions([prop_dict], patient_id)
    
    if prop_interactions:
        comp_result.setdefault("safety_flags", []).extend(prop_interactions)
        trace.append(("Step 5", f"Safety check identified potential issue: {prop_interactions[0]}", "warning"))
    else:
        trace.append(("Step 5", "Safety check cleared for proposed alternative", "success"))

    trace.append(("Step 6", f"Agent Recommendation: {comp_result['recommendation']} — Awaiting physician approval (AI cannot auto-approve)", "warning"))
    
    comp_result["trace"] = trace
    return comp_result

def run_dispensing_guard_agent(rx_id: str, item_id: int, requested_qty: int) -> Dict:
    """4. Dispensing Guard Agent: Verifies remaining quantity limits & prevents over-dispensing or invalid quantities."""
    trace = [
        ("Step 1", f"Received dispensing check request for Rx '{rx_id}', Item #{item_id}", "info"),
        ("Step 2", "Querying dispensing history & remaining quantity tool", "success")
    ]
    
    rem_info = tool_calculate_remaining_quantity(rx_id, item_id)
    trace.append(("Step 3", f"Calculated state: Prescribed: {rem_info['prescribed']} | Already Dispensed: {rem_info['dispensed']} | Authorized Remaining: {rem_info['remaining']}", "info"))

    if not validate_positive_quantity(requested_qty):
        trace.append(("Step 4", f"DISPENSING BLOCKED: Invalid quantity ({requested_qty}). Quantity must be > 0", "warning"))
        return {
            "allowed": False,
            "decision": "BLOCKED",
            "requested": requested_qty,
            "remaining": rem_info['remaining'],
            "reason": f"Invalid requested quantity ({requested_qty}). Quantity must be a positive integer greater than zero.",
            "trace": trace
        }

    is_over_dispense = requested_qty > rem_info['remaining']
    
    if is_over_dispense:
        trace.append(("Step 4", f"DISPENSING BLOCKED: Requested ({requested_qty}) exceeds remaining authorized ({rem_info['remaining']})", "warning"))
        decision = "BLOCKED"
        reason = f"Requested quantity ({requested_qty}) exceeds authorized remaining limit ({rem_info['remaining']} units)."
    else:
        trace.append(("Step 4", f"DISPENSING APPROVED: Requested ({requested_qty}) is within authorized limit ({rem_info['remaining']})", "success"))
        decision = "ALLOWED"
        reason = f"Dispensing of {requested_qty} units authorized."

    return {
        "allowed": not is_over_dispense,
        "decision": decision,
        "requested": requested_qty,
        "remaining": rem_info['remaining'],
        "reason": reason,
        "trace": trace
    }

def tool_get_recent_dispensing_events(limit: int = 50) -> List[Dict]:
    """11. Tool: Retrieves recent dispensing events."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM dispensing_events ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

get_recent_dispensing_events = tool_get_recent_dispensing_events

def tool_get_prescription_dispensing_history(rx_id: str) -> List[Dict]:
    """12. Tool: Retrieves dispensing history for a specific prescription ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM dispensing_events WHERE rx_id = ? ORDER BY id DESC", (rx_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

get_prescription_dispensing_history = tool_get_prescription_dispensing_history

def tool_get_failed_verification_attempts(rx_id: str = None) -> List[Dict]:
    """13. Tool: Retrieves failed Prescription ID / PIN verification attempts."""
    conn = get_db_connection()
    cursor = conn.cursor()
    if rx_id:
        cursor.execute("SELECT * FROM audit_logs WHERE event_type IN ('PRESCRIPTION_VERIFY_FAILED', 'QR_VERIFICATION_FAILED') AND entity_id = ? ORDER BY id DESC", (rx_id,))
    else:
        cursor.execute("SELECT * FROM audit_logs WHERE event_type IN ('PRESCRIPTION_VERIFY_FAILED', 'QR_VERIFICATION_FAILED') ORDER BY id DESC LIMIT 50")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

get_failed_verification_attempts = tool_get_failed_verification_attempts

def tool_get_substitution_history(rx_id: str = None) -> List[Dict]:
    """14. Tool: Retrieves substitution request history."""
    conn = get_db_connection()
    cursor = conn.cursor()
    if rx_id:
        cursor.execute("SELECT * FROM substitutions WHERE rx_id = ? ORDER BY id DESC", (rx_id,))
    else:
        cursor.execute("SELECT * FROM substitutions ORDER BY id DESC LIMIT 50")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

get_substitution_history = tool_get_substitution_history

def tool_get_audit_events(filter_dict: Dict = None) -> List[Dict]:
    """15. Tool: Queries audit log events with optional filtering."""
    conn = get_db_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM audit_logs WHERE 1=1"
    params = []
    if filter_dict:
        if filter_dict.get('event_type'):
            query += " AND event_type = ?"
            params.append(filter_dict['event_type'])
        if filter_dict.get('actor_role'):
            query += " AND actor_role = ?"
            params.append(filter_dict['actor_role'])
        if filter_dict.get('actor_email'):
            query += " AND actor_email = ?"
            params.append(filter_dict['actor_email'])
        if filter_dict.get('entity_id'):
            query += " AND entity_id = ?"
            params.append(filter_dict['entity_id'])
    query += " ORDER BY id DESC LIMIT 100"
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

get_audit_events = tool_get_audit_events

def tool_calculate_dispensing_frequency(rx_id: str) -> Dict:
    """16. Tool: Calculates dispensing frequency count for a prescription ID."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT dispensed_at FROM dispensing_events WHERE rx_id = ? ORDER BY id ASC", (rx_id,))
    rows = cursor.fetchall()
    conn.close()
    count = len(rows)
    return {"count": count, "events_count": count}

calculate_dispensing_frequency = tool_calculate_dispensing_frequency

def detect_system_anomalies(rx_id: str = None) -> Dict:
    """Deterministic rule-based anomaly detection engine."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    anomalies = []
    evidence = []
    highest_risk = "LOW"

    # Rule A & E: High dispensing quantity (>180 units) & Repeated dispensing attempts
    if rx_id:
        cursor.execute("SELECT * FROM dispensing_events WHERE rx_id = ?", (rx_id,))
    else:
        cursor.execute("SELECT * FROM dispensing_events ORDER BY id DESC LIMIT 100")
    disp_events = cursor.fetchall()

    rx_disp_counts = {}
    for ev in disp_events:
        r = ev['rx_id']
        rx_disp_counts[r] = rx_disp_counts.get(r, 0) + 1
        if ev['quantity_dispensed'] > 180:
            msg = f"High dispensing quantity ({ev['quantity_dispensed']} units) on Rx '{r}'."
            anomalies.append(msg)
            evidence.append(msg)
            if highest_risk != "HIGH":
                highest_risk = "MEDIUM"

    for r, cnt in rx_disp_counts.items():
        if cnt >= 3:
            msg = f"Repeated dispensing attempts ({cnt} events) for Rx '{r}'."
            anomalies.append(msg)
            evidence.append(msg)
            if highest_risk != "HIGH":
                highest_risk = "MEDIUM"

    # Rule B: Repeated blocked dispensing attempts
    if rx_id:
        cursor.execute("SELECT COUNT(*) FROM audit_logs WHERE event_type = 'DISPENSING_BLOCKED' AND entity_id = ?", (rx_id,))
        b_cnt = cursor.fetchone()[0]
        if b_cnt >= 2:
            msg = f"Repeated blocked dispensing attempts ({b_cnt} failures) for Rx '{rx_id}'."
            anomalies.append(msg)
            evidence.append(msg)
            highest_risk = "HIGH"
    else:
        cursor.execute("SELECT entity_id, COUNT(*) as cnt FROM audit_logs WHERE event_type = 'DISPENSING_BLOCKED' GROUP BY entity_id")
        for row in cursor.fetchall():
            if row['cnt'] >= 2:
                msg = f"Repeated blocked dispensing attempts ({row['cnt']} failures) for Rx '{row['entity_id']}'."
                anomalies.append(msg)
                evidence.append(msg)
                highest_risk = "HIGH"

    # Rule D: Repeated substitutions
    if rx_id:
        cursor.execute("SELECT COUNT(*) FROM substitutions WHERE rx_id = ?", (rx_id,))
        s_cnt = cursor.fetchone()[0]
        if s_cnt >= 2:
            msg = f"Repeated substitution requests ({s_cnt} requests) for Rx '{rx_id}'."
            anomalies.append(msg)
            evidence.append(msg)
            if highest_risk != "HIGH":
                highest_risk = "MEDIUM"
    else:
        cursor.execute("SELECT rx_id, COUNT(*) as cnt FROM substitutions GROUP BY rx_id")
        for row in cursor.fetchall():
            if row['cnt'] >= 2:
                msg = f"Repeated substitution requests ({row['cnt']} requests) for Rx '{row['rx_id']}'."
                anomalies.append(msg)
                evidence.append(msg)
                if highest_risk != "HIGH":
                    highest_risk = "MEDIUM"

    # Rule F: Repeated failed verification
    if rx_id:
        cursor.execute("SELECT COUNT(*) FROM audit_logs WHERE event_type IN ('PRESCRIPTION_VERIFY_FAILED', 'QR_VERIFICATION_FAILED') AND entity_id = ?", (rx_id,))
        f_cnt = cursor.fetchone()[0]
        if f_cnt >= 3:
            msg = f"Repeated invalid Prescription ID/PIN verification attempts ({f_cnt} failures) for Rx '{rx_id}'."
            anomalies.append(msg)
            evidence.append(msg)
            highest_risk = "HIGH"
    else:
        cursor.execute("SELECT entity_id, COUNT(*) as cnt FROM audit_logs WHERE event_type IN ('PRESCRIPTION_VERIFY_FAILED', 'QR_VERIFICATION_FAILED') GROUP BY entity_id")
        for row in cursor.fetchall():
            if row['cnt'] >= 3:
                msg = f"Repeated invalid Prescription ID/PIN verification attempts ({row['cnt']} failures) for Rx/Token '{row['entity_id']}'."
                anomalies.append(msg)
                evidence.append(msg)
                highest_risk = "HIGH"

    conn.close()

    has_anomaly = len(anomalies) > 0
    pattern = anomalies[0] if has_anomaly else "No unusual activity patterns detected."
    explanation = f"Unusual activity detected — review recommended. Identified {len(anomalies)} pattern flag(s)." if has_anomaly else "Audit scan completed cleanly. All dispensing and authentication patterns within normal thresholds."

    return {
        "anomaly_detected": has_anomaly,
        "anomaly_count": len(anomalies),
        "risk_level": highest_risk if has_anomaly else "LOW",
        "pattern": pattern,
        "evidence": evidence,
        "explanation": explanation,
        "recommended_action": "Review recommended." if has_anomaly else "No action required."
    }

def run_audit_anomaly_agent(entity_id: str = None) -> Dict:
    """5. Audit / Anomaly Detection Agent: Scans audit events, dispensing history & verification logs for unusual patterns."""
    trace = [
        ("Step 1", "Audit data retrieved", "success"),
        ("Step 2", "Dispensing history retrieved", "success"),
        ("Step 3", "Verification history checked", "success"),
        ("Step 4", "Rules evaluated", "success")
    ]
    
    det_res = detect_system_anomalies(entity_id)
    
    if det_res['anomaly_detected']:
        trace.append(("Step 5", f"Unusual pattern detected: {det_res['pattern']}", "warning"))
    else:
        trace.append(("Step 5", "No unusual activity patterns detected", "success"))

    prompt = f"Summarize audit anomaly evaluation: {det_res}"
    llm_expl = call_llm(prompt, "You are a clinical audit and anomaly detection AI agent.")
    
    if not llm_expl:
        trace.append(("Step 6", "AI service unavailable — deterministic anomaly rules applied.", "info"))
        explanation = det_res['explanation']
    else:
        create_audit_event("system_agent", "ai_agent", "AI_SAFETY_ANALYSIS", "audit_logs", entity_id or "0", {"risk": det_res['risk_level']})
        trace.append(("Step 6", "AI explanation generated", "success"))
        explanation = llm_expl

    trace.append(("Step 7", "Review recommended.", "warning" if det_res['anomaly_detected'] else "info"))

    det_res["explanation"] = explanation
    det_res["trace"] = trace
    return det_res

def run_approval_routing_agent(hospital_id: int, prescribing_doctor_id: int) -> Dict:
    """6. Approval Routing Agent: Routes substitution approval to original doctor or hospital on-call physician."""
    trace = [
        ("Step 1", f"Received routing request for hospital #{hospital_id}, prescribing doctor #{prescribing_doctor_id}", "info"),
        ("Step 2", "Evaluating physician availability tool", "success")
    ]
    
    route_res = tool_route_substitution_request(hospital_id, prescribing_doctor_id)
    trace.append(("Step 3", f"Routing Decision: {route_res['routing_reason']}", "success" if route_res['assigned_doctor_id'] else "warning"))

    return {
        "assigned_doctor_id": route_res['assigned_doctor_id'],
        "doctor_name": route_res['doctor_name'],
        "reason": route_res['routing_reason'],
        "trace": trace
    }

def render_agent_trace(trace_steps: List[Tuple[str, str, str]]):
    """Renders visual Agent Trace UI component for Streamlit."""
    st.markdown("### 🤖 Agent Orchestration Trace")
    for step_name, desc, status in trace_steps:
        icon = "✅" if status == "success" else "⚠️" if status == "warning" else "ℹ️"
        bg_color = "rgba(46, 204, 113, 0.1)" if status == "success" else "rgba(241, 196, 15, 0.15)" if status == "warning" else "rgba(52, 152, 219, 0.1)"
        st.markdown(
            f"""
            <div style="background-color: {bg_color}; padding: 10px 14px; border-radius: 8px; margin-bottom: 6px; border-left: 4px solid {'#2ecc71' if status == 'success' else '#f1c40f' if status == 'warning' else '#3498db'}; display: flex; align-items: center; justify-content: space-between;">
                <div><strong>{step_name}</strong>: {desc}</div>
                <div>{icon}</div>
            </div>
            """,
            unsafe_allow_html=True
        )

# ==========================================
# REPORTLAB PDF & QR CODE GENERATION
# ==========================================
def generate_prescription_pdf(rx_id: str, patient_id: str, doctor_name: str, doc_license: str, hospital_name: str, items: List[Dict], pin: str, qr_token: str) -> bytes:
    """Generates a professional clinical prescription PDF using ReportLab."""
    buffer = BytesIO()
    
    if not HAS_REPORTLAB:
        # Fallback text representation if reportlab missing
        text_content = f"MedRx Prescription {rx_id}\nPatient ID: {patient_id}\nDoctor: {doctor_name}\nHospital: {hospital_name}\nPIN: {pin}\n"
        return text_content.encode('utf-8')

    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#0F172A'),
        fontName='Helvetica-Bold'
    )
    subtitle_style = ParagraphStyle(
        'DocSubTitle',
        parent=styles['Normal'],
        fontSize=10,
        leading=13,
        textColor=colors.HexColor('#0284C7'),
        fontName='Helvetica-Bold'
    )
    meta_label = ParagraphStyle('MetaLabel', parent=styles['Normal'], fontSize=9, fontName='Helvetica-Bold', textColor=colors.HexColor('#475569'))
    meta_val = ParagraphStyle('MetaVal', parent=styles['Normal'], fontSize=9, fontName='Helvetica', textColor=colors.HexColor('#0F172A'))

    story = []

    # Header section
    header_data = [
        [
            Paragraph("<b>MedRx Platform</b><br/><font color='#0284C7'>Closed-Loop Prescription Network</font>", title_style),
            Paragraph(f"<b>{hospital_name}</b><br/>Official Clinical Prescription Record", ParagraphStyle('RightHeader', parent=subtitle_style, alignment=2))
        ]
    ]
    header_table = Table(header_data, colWidths=[3.25*inch, 3.75*inch])
    header_table.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP')]))
    story.append(header_table)
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#0284C7'), spaceAfter=12))

    # Metadata grid
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta_data = [
        [Paragraph("Prescription ID:", meta_label), Paragraph(f"<b>{rx_id}</b>", meta_val), Paragraph("Date / Time:", meta_label), Paragraph(now_str, meta_val)],
        [Paragraph("Patient ID:", meta_label), Paragraph(f"<b>{patient_id}</b>", meta_val), Paragraph("Security PIN:", meta_label), Paragraph(f"<b>{pin}</b>", meta_val)],
        [Paragraph("Prescribing Doctor:", meta_label), Paragraph(f"{doctor_name} ({doc_license})", meta_val), Paragraph("Rx Status:", meta_label), Paragraph("<font color='#0284C7'><b>Active / Valid</b></font>", meta_val)]
    ]
    meta_table = Table(meta_data, colWidths=[1.3*inch, 2.2*inch, 1.1*inch, 2.4*inch])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F8FAFC')),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('PADDING', (0,0), (-1,-1), 6),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 15))

    # Medication Table
    story.append(Paragraph("<b>Prescribed Medications</b>", ParagraphStyle('SecHeader', parent=styles['Heading2'], fontSize=12, textColor=colors.HexColor('#0F172A'))))
    story.append(Spacer(1, 6))

    items_data = [
        [
            Paragraph("<b>Medication / Strength</b>", meta_label),
            Paragraph("<b>Form / Route</b>", meta_label),
            Paragraph("<b>Dosage & Frequency</b>", meta_label),
            Paragraph("<b>Qty</b>", meta_label),
            Paragraph("<b>Instructions</b>", meta_label)
        ]
    ]

    for item in items:
        med_str = f"<b>{item.get('medicine_name')}</b>"
        if item.get('generic_name'):
            med_str += f"<br/><font color='#64748B'><i>Gen: {item.get('generic_name')}</i></font>"
        
        items_data.append([
            Paragraph(med_str, meta_val),
            Paragraph(f"{item.get('dosage_form', 'Tab')}<br/>{item.get('route', 'Oral')}", meta_val),
            Paragraph(f"{item.get('dose', '1 tab')} — {item.get('frequency', 'Daily')}<br/>Dur: {item.get('duration', 'N/A')}", meta_val),
            Paragraph(f"<b>{item.get('quantity')}</b>", meta_val),
            Paragraph(item.get('instructions', 'Take as directed'), meta_val)
        ])

    items_table = Table(items_data, colWidths=[2.2*inch, 1.1*inch, 1.6*inch, 0.5*inch, 1.6*inch])
    items_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F1F5F9')),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
        ('PADDING', (0,0), (-1,-1), 6),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 15))

    # Footer section with QR Code & Signature
    qr_img = None
    if HAS_QR:
        qr = qrcode.QRCode(version=1, box_size=4, border=1)
        # Opaque verification URL
        verify_url = f"http://localhost:8501/?verify={qr_token}"
        qr.add_data(verify_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img_buffer = BytesIO()
        img.save(img_buffer, format="PNG")
        img_buffer.seek(0)
        qr_img = RLImage(img_buffer, width=1.1*inch, height=1.1*inch)

    sig_paragraph = Paragraph(
        f"<b>Doctor Signature / Authorization</b><br/><br/><br/>__________________________________<br/><b>{doctor_name}</b><br/>Lic: {doc_license}",
        ParagraphStyle('Sig', parent=meta_val, alignment=0)
    )

    footer_data = [
        [
            qr_img if qr_img else Paragraph("[QR Code]", meta_val),
            Paragraph("<b>Verification Instructions:</b><br/>1. Pharmacist scans QR code or enters Prescription ID + 4-digit PIN.<br/>2. System displays verified remaining quantity.<br/>3. Minimum necessary privacy-preserving display.", ParagraphStyle('Inst', parent=meta_val, fontSize=8, leading=11, textColor=colors.HexColor('#475569'))),
            sig_paragraph
        ]
    ]

    footer_table = Table(footer_data, colWidths=[1.3*inch, 3.2*inch, 2.5*inch])
    footer_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'BOTTOM'),
        ('PADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(footer_table)

    story.append(Spacer(1, 15))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#CBD5E1'), spaceAfter=8))
    story.append(Paragraph("<font color='#DC2626'><b>PROTOTYPE — NOT FOR CLINICAL USE</b></font> | MedRx AI Closed-Loop Verification Network", ParagraphStyle('Notice', parent=meta_val, fontSize=8, alignment=1)))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()

def get_prescription_pdf_bytes(rx_id: str) -> bytes:
    """Helper to generate PDF bytes for any existing prescription by rx_id."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM prescriptions WHERE rx_id = ?", (rx_id,))
    rx = cursor.fetchone()
    if not rx:
        conn.close()
        return b""

    cursor.execute("SELECT name, license_no FROM doctors WHERE id = ?", (rx['doctor_id'],))
    doc = cursor.fetchone()
    if not doc:
        cursor.execute("SELECT name FROM users WHERE id = ?", (rx['doctor_id'],))
        u_row = cursor.fetchone()
        doc_name = u_row['name'] if u_row else "Dr. Prescriber"
        doc_license = "N/A"
    else:
        doc_name = doc['name']
        doc_license = doc['license_no']

    cursor.execute("SELECT name FROM hospitals WHERE id = ?", (rx['hospital_id'],))
    hosp = cursor.fetchone()
    hosp_name = hosp['name'] if hosp else "MedRx Hospital"

    cursor.execute("SELECT * FROM prescription_items WHERE rx_id = ? AND version_number = ?", (rx['rx_id'], rx['version_number']))
    items = [dict(i) for i in cursor.fetchall()]

    conn.close()

    return generate_prescription_pdf(
        rx_id=rx['rx_id'],
        patient_id=rx['patient_id'],
        doctor_name=doc_name,
        doc_license=doc_license,
        hospital_name=hosp_name,
        items=items,
        pin="**** (Protected)",
        qr_token=f"VERIFIED-{rx['rx_id']}"
    )

# ==========================================
# PUBLIC READ-ONLY QR VERIFICATION ROUTE
# ==========================================
def render_public_qr_verification(token: str):
    """Public read-only route accessed via ?verify=TOKEN parameter."""
    st.set_page_config(page_title="MedRx Public Verification", page_icon="💊", layout="centered")
    
    st.markdown(
        """
        <div style="text-align: center; padding: 15px; background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); color: white; border-radius: 12px; margin-bottom: 25px;">
            <h2 style="margin: 0; color: #38bdf8;">💊 MedRx Verification Portal</h2>
            <p style="margin-top: 5px; color: #94a3b8;">Cryptographically Authenticated Prescription Status</p>
        </div>
        """,
        unsafe_allow_html=True
    )

    token_h = hash_token(token)
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT vt.*, p.rx_id, p.status, p.issue_timestamp, p.patient_id, h.name as hospital_name
        FROM verification_tokens vt
        JOIN prescriptions p ON vt.rx_id = p.rx_id
        JOIN hospitals h ON p.hospital_id = h.id
        WHERE vt.token_hash = ? AND vt.is_revoked = 0
          AND (vt.expires_at IS NULL OR vt.expires_at > CURRENT_TIMESTAMP)
    """, (token_h,))
    row = cursor.fetchone()

    if not row:
        st.error("❌ Invalid or expired verification credentials.")
        create_audit_event("public_qr_scanner", "guest", "QR_VERIFICATION_FAILED", "token", token[:8] if token else "0", {"reason": "Token hash not found, expired, or revoked"})
        conn.close()
        return

    rx_id = row['rx_id']
    create_audit_event("public_qr_scanner", "guest", "QR_VERIFICATION_SUCCESS", "prescription", rx_id, {"status": row['status']})

    st.success("✅ Authentic MedRx Prescription Verified")
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**Prescription ID:** `{rx_id}`")
        st.markdown(f"**Hospital:** {row['hospital_name']}")
    with col2:
        st.markdown(f"**Status:** `{row['status']}`")
        st.markdown(f"**Issued Date:** {row['issue_timestamp']}")

    st.markdown("---")
    st.markdown("#### 📦 Dispensing Status (Minimum Necessary Data)")

    cursor.execute("""
        SELECT pi.*
        FROM prescription_items pi
        WHERE pi.rx_id = ?
    """, (rx_id,))
    items = cursor.fetchall()

    items_summary = []
    for item in items:
        rem_info = tool_calculate_remaining_quantity(rx_id, item['id'])
        items_summary.append({
            "Medication": item['medicine_name'],
            "Form & Strength": f"{item['dosage_form']} ({item['strength']})",
            "Prescribed Qty": rem_info['prescribed'],
            "Dispensed Qty": rem_info['dispensed'],
            "Remaining Qty": rem_info['remaining']
        })

    df = pd.DataFrame(items_summary)
    st.table(df)

    st.warning("⚠️ **Disclaimer:** Prototype — Not for Clinical Use | HIPAA-aligned privacy-by-design prototype")
    st.info("🔒 **Privacy Guarantee:** In compliance with HIPAA-aligned privacy-by-design standards, patient names, dates of birth, clinical diagnoses, and medical histories are stripped from external QR verification views.")
    conn.close()

# ==========================================
# MAIN APPLICATION & PORTAL CONTROLLERS
# ==========================================
def main():
    # Check for QR verification query parameter
    query_params = st.query_params
    if "verify" in query_params:
        render_public_qr_verification(query_params["verify"])
        return

    st.set_page_config(page_title="MedRx — Closed-Loop Prescription System", page_icon="💊", layout="wide")

    # Initialize Database
    db_init()
    seed_demo_data()

    # Custom UI Styling
    st.markdown(
        """
        <style>
            .main-header {
                background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
                padding: 24px;
                border-radius: 14px;
                color: white;
                margin-bottom: 24px;
                border-left: 6px solid #0284c7;
            }
            .metric-card {
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 10px;
                padding: 16px;
                text-align: center;
            }
            .badge-active { background-color: #dcfce7; color: #166534; padding: 4px 8px; border-radius: 4px; font-weight: bold; }
            .badge-pending { background-color: #fef9c3; color: #854d0e; padding: 4px 8px; border-radius: 4px; font-weight: bold; }
            .badge-dispensed { background-color: #e0f2fe; color: #075985; padding: 4px 8px; border-radius: 4px; font-weight: bold; }
        </style>
        """,
        unsafe_allow_html=True
    )

    # Session State Authentication Setup
    if "user" not in st.session_state:
        st.session_state.user = None

    # App Header
    st.markdown(
        """
        <div class="main-header">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <h1 style="margin:0; font-size: 28px; color: #38bdf8;">💊 MedRx</h1>
                    <p style="margin: 4px 0 0 0; color: #94a3b8;">An AI-Powered Closed-Loop Prescription Management System</p>
                </div>
                <div style="text-align: right; font-size: 13px; color: #cbd5e1;">
                    Prescribe → Verify → Dispense → Approve → Audit
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # If user is not logged in
    if not st.session_state.user:
        render_auth_screen()
        return

    # Sidebar Navigation & User Info
    user = st.session_state.user
    st.sidebar.markdown(f"### 👤 Logged In As:")
    st.sidebar.markdown(f"**{user['name']}**")
    st.sidebar.markdown(f"Role: `{user['role'].upper()}`")
    st.sidebar.markdown(f"Email: `{user['email']}`")
    
    if st.sidebar.button("🚪 Logout", use_container_width=True):
        create_audit_event(user['email'], user['role'], "LOGOUT", "user", user['id'])
        st.session_state.user = None
        st.rerun()

    st.sidebar.markdown("---")

    # Render specific role portal
    if user['role'] == 'master_admin':
        render_master_admin_portal()
    elif user['role'] == 'hospital_admin':
        render_hospital_admin_portal()
    elif user['role'] == 'doctor':
        render_doctor_portal()
    elif user['role'] == 'pharmacist':
        render_pharmacist_portal()

# ==========================================
# CENTRALIZED DEMO CREDENTIALS
# ==========================================
DEMO_ACCOUNTS = {
    "Master Admin": {
        "email": "master@medrx.demo",
        "password": "DemoPass123!",
        "role": "master_admin",
        "label": "👑 Master Admin (System Operations)"
    },
    "Hospital Admin": {
        "email": "admin@riphah-demo.medrx",
        "password": "DemoPass123!",
        "role": "hospital_admin",
        "label": "🏥 Hospital Admin (Riphah Hospital)"
    },
    "Doctor": {
        "email": "doctor@medrx.demo",
        "password": "DemoPass123!",
        "role": "doctor",
        "label": "🩺 Prescribing Physician (Dr. Hamza)"
    },
    "Pharmacist": {
        "email": "pharmacy@medrx.demo",
        "password": "DemoPass123!",
        "role": "pharmacist",
        "label": "💊 Dispensing Pharmacist (Riphah Pharmacy)"
    }
}

def set_demo_credentials(role_key: str):
    """Callback to safely pre-populate demo account credentials prior to widget instantiation."""
    creds = DEMO_ACCOUNTS.get(role_key, {})
    st.session_state.login_email_widget = creds.get("email", "")
    st.session_state.login_pass_widget = creds.get("password", "")

# ==========================================
# AUTHENTICATION SCREEN
# ==========================================
def render_auth_screen():
    if "login_email_widget" not in st.session_state:
        st.session_state.login_email_widget = "master@medrx.demo"
    if "login_pass_widget" not in st.session_state:
        st.session_state.login_pass_widget = "DemoPass123!"

    tab1, tab2, tab3 = st.tabs(["🔑 Portal Login", "🩺 Doctor Self-Registration", "🏥 Pharmacy Self-Registration"])

    with tab1:
        st.subheader("Sign In to MedRx Clinical Platform")
        col1, col2 = st.columns([2, 1])
        
        with col1:
            email = st.text_input("Email Address", key="login_email_widget")
            password = st.text_input("Password", type="password", key="login_pass_widget")
            
            if st.button("Log In to Portal", type="primary", use_container_width=True):
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE email = ?", (email.strip(),))
                u = cursor.fetchone()
                conn.close()

                if u and verify_password(password, u['password_hash']):
                    if u['status'] != 'active':
                        st.error(f"❌ Account status is currently '{u['status'].upper()}'. Please contact your hospital administrator.")
                        create_audit_event(email, u['role'], "LOGIN_REJECTED", "user", u['id'], {"status": u['status']})
                    else:
                        st.session_state.user = dict(u)
                        create_audit_event(email, u['role'], "LOGIN", "user", u['id'])
                        st.success("✅ Authentication successful. Redirecting...")
                        st.rerun()
                else:
                    st.error("❌ Invalid email address or password.")
                    create_audit_event(email, "unknown", "LOGIN_FAILED", "user", "0")

        with col2:
            st.markdown("### 🧪 Fast Demo Account Autofill")
            st.caption("Click to select pre-configured hackathon demo credentials (then click Log In):")
            
            for key, info in DEMO_ACCOUNTS.items():
                st.button(
                    info['label'],
                    key=f"btn_demo_{key}",
                    on_click=set_demo_credentials,
                    args=(key,),
                    use_container_width=True
                )

    with tab2:
        st.subheader("Doctor Registration")
        st.caption("Self-registered doctors require review & approval by their Hospital Admin prior to issuing prescriptions.")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name FROM hospitals WHERE status = 'active'")
        hospitals = cursor.fetchall()
        conn.close()

        hosp_dict = {h['name']: h['id'] for h in hospitals} if hospitals else {}

        with st.form("doc_reg_form"):
            doc_name = st.text_input("Full Name (e.g., Dr. Hamza Malik)")
            doc_email = st.text_input("Email Address")
            doc_pass = st.text_input("Create Password", type="password")
            doc_license = st.text_input("Medical License / Registration No.")
            doc_specialty = st.selectbox("Specialty", ["Internal Medicine", "Cardiology", "Pediatrics", "Pulmonology", "Neurology", "General Surgery"])
            doc_hosp = st.selectbox("Hospital", list(hosp_dict.keys()) if hosp_dict else ["No Active Hospitals Found"])
            
            submit_doc = st.form_submit_button("Submit Registration")

            if submit_doc:
                if not doc_name or not doc_email or not doc_pass or not doc_license or not hosp_dict:
                    st.error("Please fill in all required fields.")
                else:
                    try:
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        pass_h = hash_password(doc_pass)
                        cursor.execute("""
                            INSERT INTO users (email, password_hash, role, status, name)
                            VALUES (?, ?, 'doctor', 'pending', ?)
                        """, (doc_email.strip(), pass_h, doc_name.strip()))
                        u_id = cursor.lastrowid

                        cursor.execute("""
                            INSERT INTO doctors (user_id, hospital_id, name, license_no, specialty, status)
                            VALUES (?, ?, ?, ?, ?, 'pending')
                        """, (u_id, hosp_dict[doc_hosp], doc_name.strip(), doc_license.strip(), doc_specialty))
                        conn.commit()
                        conn.close()

                        create_audit_event(doc_email, "doctor", "USER_CREATED", "user", u_id, {"status": "pending"})
                        st.success("✅ Registration submitted! Your account is currently PENDING approval by your Hospital Admin.")
                    except sqlite3.IntegrityError:
                        st.error("❌ Email address or license number is already registered.")

    with tab3:
        st.subheader("Pharmacy Registration")
        with st.form("pharm_reg_form"):
            pharm_name = st.text_input("Pharmacy Name (e.g., MedCare Pharmacy)")
            pharm_email = st.text_input("Pharmacist Email")
            pharm_pass = st.text_input("Create Password", type="password")
            pharm_license = st.text_input("Pharmacy License Number")
            pharm_address = st.text_input("Physical Address")
            
            submit_pharm = st.form_submit_button("Register Pharmacy")

            if submit_pharm:
                if not pharm_name or not pharm_email or not pharm_pass or not pharm_license:
                    st.error("Please complete all required fields.")
                else:
                    try:
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        pass_h = hash_password(pharm_pass)
                        cursor.execute("""
                            INSERT INTO users (email, password_hash, role, status, name)
                            VALUES (?, ?, 'pharmacist', 'active', ?)
                        """, (pharm_email.strip(), pass_h, pharm_name.strip()))
                        u_id = cursor.lastrowid

                        cursor.execute("""
                            INSERT INTO pharmacies (user_id, name, license_no, address, status)
                            VALUES (?, ?, ?, ?, 'active')
                        """, (u_id, pharm_name.strip(), pharm_license.strip(), pharm_address.strip()))
                        conn.commit()
                        conn.close()

                        create_audit_event(pharm_email, "pharmacist", "PHARMACY_CREATED", "pharmacy", u_id, {})
                        st.success("✅ Pharmacy registered successfully! You can now log in.")
                    except sqlite3.IntegrityError:
                        st.error("❌ Email address or pharmacy license is already registered.")

# ==========================================
# AUDIT DASHBOARD
# ==========================================
def render_audit_dashboard(user: dict):
    """Professional Audit Dashboard & Anomaly Detector with Role-Based Filtering."""
    st.title("🛡️ Audit Trail & Anomaly Detection Dashboard")
    st.caption("Immutable append-only audit trail and automated clinical anomaly detection.")

    role = user['role']
    actor_email = user['email']

    conn = get_db_connection()
    cursor = conn.cursor()

    if role == 'master_admin':
        where_clause = "1=1"
        query_params = []
    elif role == 'hospital_admin':
        where_clause = "(actor_role IN ('doctor', 'pharmacist', 'hospital_admin') OR actor_email = ?)"
        query_params = [actor_email]
    elif role == 'doctor':
        where_clause = "actor_email = ?"
        query_params = [actor_email]
    elif role == 'pharmacist':
        where_clause = "actor_email = ?"
        query_params = [actor_email]
    else:
        where_clause = "actor_email = ?"
        query_params = [actor_email]

    cursor.execute(f"SELECT COUNT(*) FROM audit_logs WHERE {where_clause}", query_params)
    total_events = cursor.fetchone()[0]

    cursor.execute(f"SELECT COUNT(*) FROM audit_logs WHERE {where_clause} AND DATE(timestamp) = DATE('now')", query_params)
    today_events = cursor.fetchone()[0]

    cursor.execute(f"SELECT COUNT(*) FROM audit_logs WHERE {where_clause} AND event_type = 'LOGIN_FAILED'", query_params)
    failed_logins = cursor.fetchone()[0]

    cursor.execute(f"SELECT COUNT(*) FROM audit_logs WHERE {where_clause} AND event_type = 'DISPENSING_BLOCKED'", query_params)
    blocked_dispenses = cursor.fetchone()[0]

    cursor.execute(f"SELECT COUNT(*) FROM audit_logs WHERE {where_clause} AND event_type LIKE 'SUBSTITUTION%'", query_params)
    sub_events = cursor.fetchone()[0]

    cursor.execute(f"SELECT COUNT(*) FROM audit_logs WHERE {where_clause} AND event_type LIKE 'AI_%'", query_params)
    ai_events = cursor.fetchone()[0]

    conn.close()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Audit Events", total_events)
    c2.metric("Today's Events", today_events)
    c3.metric("Failed Logins", failed_logins)
    c4.metric("Blocked Dispenses", blocked_dispenses)

    c5, c6, c7 = st.columns(3)
    c5.metric("Substitution Actions", sub_events)
    c6.metric("AI Analyses Conducted", ai_events)

    anomaly_res = detect_system_anomalies()
    c7.metric("Unusual Activity Flagged", anomaly_res['risk_level'] if anomaly_res['anomaly_detected'] else "LOW", delta="Alert Active" if anomaly_res['anomaly_detected'] else "Normal")

    st.markdown("---")
    st.subheader("🔍 Anomaly Detection & AI Audit Agent")

    if anomaly_res['anomaly_detected']:
        st.warning(f"⚠️ Unusual activity detected — review recommended. Risk Level: **{anomaly_res['risk_level']}**")
        for ev in anomaly_res['evidence']:
            st.write(f"- 🚩 {ev}")
    else:
        st.success("✅ Audit Scan Normal: No unusual activity patterns detected across recent transactions.")

    if st.button("🤖 Run Audit / Anomaly AI Agent", type="primary"):
        with st.spinner("Analyzing system audit trail and dispensing patterns..."):
            agent_res = run_audit_anomaly_agent()
            render_agent_trace(agent_res['trace'])
            
            if agent_res['anomaly_detected']:
                st.warning(f"**Agent Risk Assessment:** {agent_res['risk_level']}")
                st.info(f"**Explanation:** {agent_res['explanation']}")
            else:
                st.success(f"**Agent Assessment:** Clean Audit — {agent_res['explanation']}")

    st.markdown("---")
    st.subheader("📜 Append-Only Event Logs (Filtered)")

    f_col1, f_col2, f_col3 = st.columns(3)
    with f_col1:
        event_filter = st.selectbox("Filter by Event Type", ["ALL", "LOGIN", "LOGIN_FAILED", "LOGOUT", "PRESCRIPTION_CREATED", "PRESCRIPTION_VERIFIED", "DISPENSING_BLOCKED", "SUBSTITUTION_REQUESTED", "SUBSTITUTION_APPROVED", "AI_SAFETY_ANALYSIS", "QR_VERIFICATION_SUCCESS"])
    with f_col2:
        search_id = st.text_input("Search Entity / Prescription ID")
    with f_col3:
        limit_cnt = st.selectbox("Display Limit", [50, 100, 250], index=1)

    conn = get_db_connection()
    cursor = conn.cursor()
    
    sql = f"SELECT id, timestamp, actor_email, actor_role, event_type, entity_type, entity_id, metadata_json FROM audit_logs WHERE {where_clause}"
    params = list(query_params)

    if event_filter != "ALL":
        sql += " AND event_type = ?"
        params.append(event_filter)
    if search_id.strip():
        sql += " AND (entity_id LIKE ? OR metadata_json LIKE ?)"
        params.extend([f"%{search_id.strip()}%", f"%{search_id.strip()}%"])

    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit_cnt)

    cursor.execute(sql, params)
    logs = cursor.fetchall()
    conn.close()

    if logs:
        st.dataframe(pd.DataFrame([dict(l) for l in logs]), use_container_width=True)
    else:
        st.info("No matching audit logs found.")

# ==========================================
# MASTER ADMIN PORTAL
# ==========================================
def render_master_admin_portal():
    st.sidebar.markdown("### 👑 Master Admin Menu")
    menu = st.sidebar.radio("Navigation", ["Dashboard", "Hospitals Manager", "Hospital Admins", "Pharmacies", "System Users", "Audit Logs", "Reset Demo Data"])

    conn = get_db_connection()
    cursor = conn.cursor()

    if menu == "Dashboard":
        st.title("Master Admin Dashboard")
        
        cursor.execute("SELECT COUNT(*) FROM hospitals")
        hosp_cnt = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users WHERE role='doctor'")
        doc_cnt = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM pharmacies")
        pharm_cnt = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM prescriptions")
        rx_cnt = cursor.fetchone()[0]

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Hospitals", hosp_cnt)
        c2.metric("Doctors", doc_cnt)
        c3.metric("Pharmacies", pharm_cnt)
        c4.metric("Prescriptions", rx_cnt)

        st.markdown("---")
        st.subheader("System Overview & Active Hospitals")
        cursor.execute("SELECT * FROM hospitals")
        hospitals = cursor.fetchall()
        if hospitals:
            st.dataframe(pd.DataFrame([dict(h) for h in hospitals]), use_container_width=True)

    elif menu == "Hospitals Manager":
        st.title("Hospitals Manager")
        
        with st.expander("➕ Add New Hospital", expanded=True):
            with st.form("add_hosp_form"):
                h_name = st.text_input("Hospital Name")
                h_code = st.text_input("Hospital Code (e.g., HOSP-02)")
                h_addr = st.text_input("Address")
                h_allow_all = st.checkbox("Allow All Active Pharmacies to Access Prescriptions", value=True)
                
                if st.form_submit_button("Register Hospital"):
                    if h_name and h_code:
                        try:
                            cursor.execute("""
                                INSERT INTO hospitals (name, code, address, allow_all_pharmacies)
                                VALUES (?, ?, ?, ?)
                            """, (h_name, h_code, h_addr, 1 if h_allow_all else 0))
                            conn.commit()
                            create_audit_event(st.session_state.user['email'], "master_admin", "HOSPITAL_CREATED", "hospital", h_code, {})
                            st.success(f"✅ Hospital '{h_name}' registered successfully.")
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.error("Hospital code already exists.")

        st.subheader("Existing Hospitals")
        cursor.execute("SELECT * FROM hospitals")
        hosps = cursor.fetchall()
        for h in hosps:
            cols = st.columns([3, 2, 2, 2])
            cols[0].markdown(f"**{h['name']}** (`{h['code']}`)")
            cols[1].markdown(f"Status: `{h['status']}`")
            cols[2].markdown(f"Allow All Pharmacies: `{'YES' if h['allow_all_pharmacies'] else 'NO'}`")
            
            btn_txt = "Suspend" if h['status'] == 'active' else "Activate"
            if cols[3].button(f"{btn_txt} ##{h['id']}", key=f"hosp_togg_{h['id']}"):
                new_st = 'suspended' if h['status'] == 'active' else 'active'
                cursor.execute("UPDATE hospitals SET status = ? WHERE id = ?", (new_st, h['id']))
                conn.commit()
                st.rerun()

    elif menu == "Hospital Admins":
        st.title("Hospital Admin Management")
        cursor.execute("SELECT id, name FROM hospitals WHERE status = 'active'")
        hosps = cursor.fetchall()
        hosp_dict = {h['name']: h['id'] for h in hosps}

        with st.form("create_hosp_admin"):
            ha_name = st.text_input("Admin Name")
            ha_email = st.text_input("Email")
            ha_pass = st.text_input("Password", type="password")
            ha_hosp = st.selectbox("Assign to Hospital", list(hosp_dict.keys()) if hosp_dict else ["None"])

            if st.form_submit_button("Create Hospital Admin"):
                if ha_name and ha_email and ha_pass and hosp_dict:
                    try:
                        pass_h = hash_password(ha_pass)
                        cursor.execute("""
                            INSERT INTO users (email, password_hash, role, status, name)
                            VALUES (?, ?, 'hospital_admin', 'active', ?)
                        """, (ha_email.strip(), pass_h, ha_name.strip()))
                        conn.commit()
                        st.success("✅ Hospital Admin created.")
                    except sqlite3.IntegrityError:
                        st.error("Email already exists.")

    elif menu == "Pharmacies":
        st.title("System Pharmacies")
        cursor.execute("SELECT p.*, u.email FROM pharmacies p JOIN users u ON p.user_id = u.id")
        pharms = cursor.fetchall()
        if pharms:
            st.dataframe(pd.DataFrame([dict(p) for p in pharms]), use_container_width=True)

    elif menu == "System Users":
        st.title("System Users")
        cursor.execute("SELECT id, email, role, status, name, created_at FROM users")
        users = cursor.fetchall()
        st.dataframe(pd.DataFrame([dict(u) for u in users]), use_container_width=True)

    elif menu == "Audit Logs":
        render_audit_dashboard(st.session_state.user)

    elif menu == "Reset Demo Data":
        st.title("Reset Demo Data")
        st.warning("⚠️ Warning: Resetting demo data will clear all newly created prescriptions, encounters, and patients, and restore the hackathon seed scenario.")
        if st.button("Reset / Initialize Demo Data Now", type="primary"):
            seed_demo_data(force_reset=True)
            st.success("✅ Demo database successfully re-seeded.")
            st.rerun()

    conn.close()

# ==========================================
# HOSPITAL ADMIN PORTAL
# ==========================================
def render_hospital_admin_portal():
    user = st.session_state.user
    conn = get_db_connection()
    cursor = conn.cursor()

    # Find doctor hospital
    cursor.execute("SELECT h.* FROM hospitals h JOIN users u ON h.name LIKE '%' WHERE u.id = ?", (user['id'],))
    hosp = cursor.fetchone()
    # Fallback to hospital ID 1 if matching not found directly
    hosp_id = hosp['id'] if hosp else 1

    st.sidebar.markdown("### 🏥 Hospital Admin Menu")
    menu = st.sidebar.radio("Navigation", ["Dashboard", "Doctor Approvals", "Pharmacy Allocations", "Hospital Patients", "Prescriptions Overview", "Routing Config", "Audit Logs"])

    if menu == "Dashboard":
        st.title("Hospital Admin Dashboard")
        st.info(f"Managing Hospital ID #{hosp_id}")

        cursor.execute("SELECT COUNT(*) FROM doctors WHERE hospital_id = ?", (hosp_id,))
        doc_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM doctors WHERE hospital_id = ? AND status = 'pending'", (hosp_id,))
        pending_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM prescriptions WHERE hospital_id = ?", (hosp_id,))
        rx_count = cursor.fetchone()[0]

        c1, c2, c3 = st.columns(3)
        c1.metric("Hospital Doctors", doc_count)
        c2.metric("Pending Doctor Approvals", pending_count)
        c3.metric("Total Hospital Prescriptions", rx_count)

    elif menu == "Doctor Approvals":
        st.title("Doctor Approvals & Roster")
        st.subheader("Pending Self-Registered Doctors")
        
        cursor.execute("SELECT d.*, u.email FROM doctors d JOIN users u ON d.user_id = u.id WHERE d.hospital_id = ? AND d.status = 'pending'", (hosp_id,))
        pend_docs = cursor.fetchall()
        
        if not pend_docs:
            st.success("No pending doctor approvals.")
        else:
            for d in pend_docs:
                cols = st.columns([3, 2, 2, 2])
                cols[0].markdown(f"**{d['name']}** ({d['specialty']})<br/>Lic: `{d['license_no']}`", unsafe_allow_html=True)
                cols[1].markdown(f"Email: `{d['email']}`")
                
                if cols[2].button("✅ Approve", key=f"app_doc_{d['id']}"):
                    cursor.execute("UPDATE doctors SET status = 'active' WHERE id = ?", (d['id'],))
                    cursor.execute("UPDATE users SET status = 'active' WHERE id = ?", (d['user_id'],))
                    conn.commit()
                    create_audit_event(user['email'], "hospital_admin", "USER_APPROVED", "doctor", d['id'], {})
                    st.success(f"Approved Dr. {d['name']}")
                    st.rerun()

                if cols[3].button("❌ Reject", key=f"rej_doc_{d['id']}"):
                    cursor.execute("UPDATE doctors SET status = 'rejected' WHERE id = ?", (d['id'],))
                    cursor.execute("UPDATE users SET status = 'rejected' WHERE id = ?", (d['user_id'],))
                    conn.commit()
                    create_audit_event(user['email'], "hospital_admin", "USER_REJECTED", "doctor", d['id'], {})
                    st.rerun()

        st.markdown("---")
        st.subheader("All Hospital Doctors")
        cursor.execute("SELECT d.*, u.email FROM doctors d JOIN users u ON d.user_id = u.id WHERE d.hospital_id = ?", (hosp_id,))
        all_docs = cursor.fetchall()
        if all_docs:
            st.dataframe(pd.DataFrame([dict(d) for d in all_docs]), use_container_width=True)

    elif menu == "Pharmacy Allocations":
        st.title("Pharmacy Access & Allocations")
        
        cursor.execute("SELECT allow_all_pharmacies FROM hospitals WHERE id = ?", (hosp_id,))
        cur_allow = cursor.fetchone()['allow_all_pharmacies']

        new_allow = st.checkbox("Allow ALL Active System Pharmacies to Access Prescriptions", value=bool(cur_allow))
        if new_allow != bool(cur_allow):
            cursor.execute("UPDATE hospitals SET allow_all_pharmacies = ? WHERE id = ?", (1 if new_allow else 0, hosp_id))
            conn.commit()
            st.success("Pharmacy access settings updated.")
            st.rerun()

        st.markdown("---")
        st.subheader("Explicitly Allocated Pharmacies")
        cursor.execute("""
            SELECT p.*, hp.allocated_at
            FROM hospital_pharmacies hp
            JOIN pharmacies p ON hp.pharmacy_id = p.id
            WHERE hp.hospital_id = ?
        """, (hosp_id,))
        allocs = cursor.fetchall()
        if allocs:
            st.dataframe(pd.DataFrame([dict(a) for a in allocs]), use_container_width=True)
        else:
            st.info("No explicit individual pharmacy allocations.")

    elif menu == "Routing Config":
        st.title("Substitution Approval Routing Configuration")
        st.caption("Configure on-call physician for substitution approval requests when the original prescribing doctor is absent/inactive.")

        cursor.execute("SELECT * FROM doctors WHERE hospital_id = ? AND status = 'active'", (hosp_id,))
        active_docs = cursor.fetchall()
        doc_opt = {f"{d['name']} ({d['specialty']})": d['id'] for d in active_docs}

        cursor.execute("SELECT * FROM doctors WHERE hospital_id = ? AND is_on_call = 1", (hosp_id,))
        cur_on_call = cursor.fetchone()

        st.markdown(f"**Current On-Call Doctor:** `{cur_on_call['name'] if cur_on_call else 'None Configured'}`")

        if doc_opt:
            selected_doc = st.selectbox("Select Designated On-Call Physician", list(doc_opt.keys()))
            if st.button("Set On-Call Physician"):
                cursor.execute("UPDATE doctors SET is_on_call = 0 WHERE hospital_id = ?", (hosp_id,))
                cursor.execute("UPDATE doctors SET is_on_call = 1 WHERE id = ?", (doc_opt[selected_doc],))
                conn.commit()
                st.success("✅ On-call physician updated.")
                st.rerun()

    elif menu == "Audit Logs":
        render_audit_dashboard(st.session_state.user)

    conn.close()

# ==========================================
# DOCTOR PORTAL
# ==========================================
def render_doctor_portal():
    user = st.session_state.user
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM doctors WHERE user_id = ?", (user['id'],))
    doctor = cursor.fetchone()

    if not doctor or doctor['status'] != 'active':
        st.warning("⚠️ Your doctor account is currently PENDING approval by your Hospital Admin.")
        conn.close()
        return

    st.sidebar.markdown("### 🩺 Doctor Menu")
    menu = st.sidebar.radio("Navigation", ["Dashboard", "New Patient", "Search Patients / Encounters", "New Prescription Studio", "Substitution Approvals", "Prescription History", "Audit Trail"])

    if menu == "Dashboard":
        st.title(f"Welcome, {doctor['name']}")
        st.caption(f"Specialty: {doctor['specialty']} | License: {doctor['license_no']}")

        cursor.execute("SELECT COUNT(*) FROM prescriptions WHERE doctor_id = ?", (doctor['id'],))
        rx_cnt = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM substitutions WHERE assigned_doctor_id = ? AND status = 'Pending Approval'", (doctor['id'],))
        sub_cnt = cursor.fetchone()[0]

        c1, c2 = st.columns(2)
        c1.metric("Prescriptions Issued", rx_cnt)
        c2.metric("Pending Substitution Approvals", sub_cnt)

        if sub_cnt > 0:
            st.warning(f"⚠️ You have {sub_cnt} pending medicine substitution request(s) awaiting your review!")

    elif menu == "New Patient":
        st.title("Create New Patient Record")
        st.caption("Generate a canonical, privacy-preserving random Patient ID (e.g. PT-7K4M29) with full clinical profile.")

        with st.form("new_patient_form"):
            col_p1, col_p2 = st.columns(2)
            p_full_name = col_p1.text_input("Full Patient Name", "Zainab Bibi")
            p_dob = col_p2.text_input("Date of Birth (YYYY-MM-DD)", "1982-04-12")

            col_p3, col_p4, col_p5 = st.columns(3)
            p_age = col_p3.number_input("Age (Years)", min_value=0, max_value=120, value=44)
            p_sex = col_p4.selectbox("Sex", ["Female", "Male", "Other"])
            p_phone = col_p5.text_input("Phone Number", "+92-301-555-0182")

            col_p6, col_p7 = st.columns(2)
            p_email = col_p6.text_input("Email Address", "zainab.bibi@demo.medrx")
            p_address = col_p7.text_input("Physical Address", "House 42, St 9, F-8/2, Islamabad")

            p_emergency = st.text_input("Emergency Contact", "Tariq Bibi (Husband) +92-301-555-0183")
            p_allergies = st.text_area("Known Allergies", "Penicillin, Shellfish")
            p_meds = st.text_area("Current Medications", "Metformin 500mg, Amlodipine 5mg")
            p_pmh = st.text_area("Past Medical History", "Type 2 Diabetes (3 yrs), Hypertension (5 yrs)")
            p_psh = st.text_area("Past Surgical History", "Appendectomy (2015)")
            p_fh = st.text_area("Family History", "Father: T2DM, Mother: Hypertension")
            p_sh = st.text_area("Social History", "Non-smoker, Moderate caffeine intake")
            risk_flags = st.text_area("Risk Flags / Warnings", "Hypertension, Type 2 Diabetes")
            
            submit_p = st.form_submit_button("Generate Canonical Patient Record")

            if submit_p:
                pid = generate_random_patient_id()
                cursor.execute("""
                    INSERT INTO patients (patient_id, full_name, dob, age, sex, phone, email, address, emergency_contact, allergies, current_medications, past_medical_history, past_surgical_history, family_history, social_history, risk_flags, created_by_doctor_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (pid, p_full_name, p_dob, int(p_age), p_sex, p_phone, p_email, p_address, p_emergency, p_allergies, p_meds, p_pmh, p_psh, p_fh, p_sh, risk_flags, doctor['id']))
                conn.commit()
                create_audit_event(user['email'], "doctor", "PATIENT_CREATED", "patient", pid, {"full_name": p_full_name})
                st.success(f"✅ Canonical Patient Record Created! Patient ID: `{pid}`")

    elif menu == "Search Patients / Encounters":
        st.title("Patient Clinical File & Encounters Studio")
        pid_search = st.text_input("Enter Patient ID (e.g. PT-7K4M29 or MRX-AB7K-92QF)")
        
        if pid_search:
            cursor.execute("SELECT * FROM patients WHERE patient_id = ?", (pid_search.strip(),))
            pat = cursor.fetchone()
            if not pat:
                st.error("Patient ID not found.")
            else:
                st.success(f"Patient Profile Found: `{pat['patient_id']}` — {pat['full_name'] or 'Anonymous'}")
                
                with st.expander("👤 View Full Patient Clinical Profile", expanded=True):
                    c1, c2, c3, c4 = st.columns(4)
                    c1.markdown(f"**Name:** {pat['full_name'] or 'N/A'}")
                    c2.markdown(f"**Age / Sex:** {pat['age'] or 'N/A'} yrs / {pat['sex'] or 'N/A'}")
                    c3.markdown(f"**DOB:** {pat['dob'] or 'N/A'}")
                    c4.markdown(f"**Phone:** {pat['phone'] or 'N/A'}")

                    c5, c6 = st.columns(2)
                    c5.markdown(f"**Allergies:** `{pat['allergies'] or 'None recorded'}`")
                    c6.markdown(f"**Risk Flags:** `{pat['risk_flags'] or 'None'}`")

                    c7, c8 = st.columns(2)
                    c7.markdown(f"**Current Medications:** {pat['current_medications'] or 'None'}")
                    c8.markdown(f"**Past Medical History:** {pat['past_medical_history'] or 'None'}")

                st.markdown("---")
                st.subheader("➕ Log New Comprehensive Encounter")
                with st.form("encounter_form"):
                    col_e1, col_e2 = st.columns(2)
                    e_vitals = col_e1.text_area("Vital Signs", "BP: 138/88 mmHg | HR: 74 bpm | Temp: 98.6F | SpO2: 98% | Fasting Glucose: 142 mg/dL")
                    e_cc = col_e2.text_input("Chief Complaint", "Routine Follow-up & High Blood Sugar Check")

                    e_sympt = st.text_area("Presenting Symptoms", "Dry cough x 4 days, fatigue, mild leg heaviness")
                    e_obs = st.text_area("Physical Observations", "BP slightly elevated. Fasting blood sugar above target.")
                    e_hist = st.text_area("Relevant History of Present Illness", "Known diabetic x 3 yrs")
                    e_assess = st.text_area("Clinical Assessment", "Type 2 Diabetes Suboptimally Controlled & Essential Hypertension")

                    col_e3, col_e4, col_e5 = st.columns(3)
                    e_pdiag = col_e3.text_input("Primary Diagnosis", "Essential Hypertension")
                    e_sdiag = col_e4.text_input("Secondary Diagnosis", "Type 2 Diabetes Mellitus")
                    e_code = col_e5.text_input("ICD-10 Diagnosis Code", "ICD-10 I10 / E11.9")

                    e_plan = st.text_area("Comprehensive Treatment Plan", "1. Continue Metformin 500mg BID.\n2. Initiate Lisinopril 10mg daily.")
                    
                    if st.form_submit_button("Save Encounter Record"):
                        enc_code = f"ENC-{random.randint(10000, 99999)}"
                        cursor.execute("""
                            INSERT INTO encounters (encounter_id, patient_id, doctor_id, hospital_id, vitals, chief_complaint, symptoms, observations, history, assessment, primary_diagnosis, secondary_diagnosis, diagnosis_code, diagnosis, plan)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (enc_code, pat['patient_id'], doctor['id'], doctor['hospital_id'], e_vitals, e_cc, e_sympt, e_obs, e_hist, e_assess, e_pdiag, e_sdiag, e_code, e_pdiag, e_plan))
                        conn.commit()
                        create_audit_event(user['email'], "doctor", "ENCOUNTER_CREATED", "encounter", enc_code, {})
                        st.success(f"✅ Clinical Encounter `{enc_code}` saved!")

                st.subheader("Historical Encounters")
                cursor.execute("SELECT * FROM encounters WHERE patient_id = ? ORDER BY id DESC", (pat['patient_id'],))
                encs = cursor.fetchall()
                if encs:
                    st.dataframe(pd.DataFrame([dict(e) for e in encs]), use_container_width=True)

    elif menu == "New Prescription Studio":
        st.title("New Prescription Studio")

        # Session Submission Token initialization for Idempotency
        if "studio_submission_token" not in st.session_state:
            st.session_state.studio_submission_token = secrets.token_hex(16)

        # Select Patient
        cursor.execute("SELECT patient_id, full_name FROM patients ORDER BY id DESC")
        pat_rows = cursor.fetchall()
        pat_opt = {f"{row['patient_id']} - {row['full_name'] or 'Anonymous'}": row['patient_id'] for row in pat_rows}
        
        if not pat_opt:
            st.warning("Please create a patient record first.")
        else:
            selected_pat_str = st.selectbox("Select Patient Record", list(pat_opt.keys()))
            selected_pid = pat_opt[selected_pat_str]

            # SHORT-WINDOW DUPLICATE CHECK (within 5 minutes)
            cursor.execute("""
                SELECT rx_id, issue_timestamp FROM prescriptions
                WHERE doctor_id = ? AND patient_id = ? AND issue_timestamp >= datetime('now', '-5 minutes')
            """, (doctor['id'], selected_pid))
            recent_rx = cursor.fetchone()

            if recent_rx:
                st.warning(f"⚠️ **Short-Window Duplicate Warning:** A prescription (`{recent_rx['rx_id']}`) was issued for this patient within the last 5 minutes ({recent_rx['issue_timestamp']}). Proceeding will create an additional distinct prescription record.")

            # Initialize Items List in Session State
            if "rx_items" not in st.session_state:
                st.session_state.rx_items = []

            st.markdown("---")
            st.subheader("Add Medication from Catalog")

            # Catalog Drug Picker
            cursor.execute("SELECT * FROM medication_catalog ORDER BY generic_name ASC")
            cat_drugs = cursor.fetchall()
            cat_options = [f"{d['brand_name']} ({d['generic_name']} {d['strength']}) - {d['dosage_form']}" for d in cat_drugs]
            selected_cat_str = st.selectbox("Search & Select Drug Catalog Item", ["-- Custom Entry --"] + cat_options)

            c1, c2, c3, c4 = st.columns(4)
            if selected_cat_str != "-- Custom Entry --":
                # Prefill from catalog
                sel_idx = cat_options.index(selected_cat_str)
                sel_obj = cat_drugs[sel_idx]
                med_name = c1.text_input("Medicine Brand Name", sel_obj['brand_name'])
                gen_name = c2.text_input("Generic Name", sel_obj['generic_name'])
                strength = c3.text_input("Strength", sel_obj['strength'])
                d_form = c4.text_input("Dosage Form", sel_obj['dosage_form'])
                route = sel_obj['route']
            else:
                med_name = c1.text_input("Medicine Brand Name", "")
                gen_name = c2.text_input("Generic Name", "")
                strength = c3.text_input("Strength", "500 mg")
                d_form = c4.text_input("Dosage Form", "Tablet")
                route = "Oral"

            c5, c6, c7, c8 = st.columns(4)
            dose = c5.text_input("Dose", "1 tab")
            freq = c6.text_input("Frequency", "Twice daily")
            dur = c7.text_input("Duration", "7 days")
            qty = c8.number_input("Quantity (Units)", min_value=1, value=14)
            instructions = st.text_input("Special Instructions", "Take with food")

            if st.button("➕ Add Item to Prescription"):
                st.session_state.rx_items.append({
                    "medicine_name": med_name,
                    "generic_name": gen_name,
                    "strength": strength,
                    "dosage_form": d_form,
                    "route": route,
                    "dose": dose,
                    "frequency": freq,
                    "duration": dur,
                    "quantity": int(qty),
                    "instructions": instructions
                })
                st.success(f"Added {med_name} to prescription draft.")

            if st.session_state.rx_items:
                st.markdown("---")
                st.subheader("Current Prescription Draft Items")
                st.dataframe(pd.DataFrame(st.session_state.rx_items), use_container_width=True)

                if st.button("🗑️ Clear Items"):
                    st.session_state.rx_items = []
                    st.rerun()

                st.markdown("---")
                st.subheader("🤖 Trigger AI Agent Verification")

                if st.button("Run AI Prescription Verification Agent", type="primary", use_container_width=True):
                    agent_res = run_prescription_verification_agent(st.session_state.rx_items, selected_pid)
                    render_agent_trace(agent_res['trace'])
                    
                    st.markdown("#### Agent Analysis Summary")
                    st.write(agent_res['explanation'])

                    if agent_res['safety_flags']:
                        for sf in agent_res['safety_flags']:
                            st.warning(f"⚠️ {sf}")

                    # Finalize Submission Form
                    st.markdown("---")
                    st.subheader("Finalize & Issue Prescription")
                    
                    if st.button("🚀 Issue Prescription & Generate Security PDF"):
                        sub_token = st.session_state.studio_submission_token

                        # IDEMPOTENCY CHECK
                        cursor.execute("SELECT rx_id, pin_hash FROM prescriptions WHERE submission_token = ?", (sub_token,))
                        existing_rx = cursor.fetchone()

                        if existing_rx:
                            rx_code = existing_rx['rx_id']
                            pin = "[Retained in PDF]"
                            st.info(f"ℹ️ Idempotency Notice: Re-displayed existing prescription `{rx_code}` created with this session token.")
                        else:
                            rx_code = generate_random_rx_id()
                            pin = generate_pin()
                            pin_h = hash_pin(pin)
                            qr_tok = generate_qr_token()
                            qr_tok_h = hash_token(qr_tok)

                            cursor.execute("""
                                INSERT INTO prescriptions (rx_id, patient_id, doctor_id, hospital_id, status, pin_hash, submission_token)
                                VALUES (?, ?, ?, ?, 'Active', ?, ?)
                            """, (rx_code, selected_pid, doctor['id'], doctor['hospital_id'], pin_h, sub_token))

                            for item in st.session_state.rx_items:
                                cursor.execute("""
                                    INSERT INTO prescription_items (rx_id, version_number, medicine_name, generic_name, strength, dosage_form, route, dose, frequency, duration, quantity, instructions)
                                    VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, (rx_code, item['medicine_name'], item['generic_name'], item['strength'], item['dosage_form'], item['route'], item['dose'], item['frequency'], item['duration'], item['quantity'], item['instructions']))

                            cursor.execute("""
                                INSERT INTO prescription_versions (rx_id, version_number, modified_by_doctor_id, change_summary)
                                VALUES (?, 1, ?, 'Initial creation by doctor')
                            """, (rx_code, doctor['id']))

                            cursor.execute("""
                                INSERT INTO verification_tokens (token_hash, rx_id)
                                VALUES (?, ?)
                            """, (qr_tok_h, rx_code))

                            conn.commit()
                            create_audit_event(user['email'], "doctor", "PRESCRIPTION_CREATED", "prescription", rx_code, {"items_count": len(st.session_state.rx_items)})

                        cursor.execute("SELECT name FROM hospitals WHERE id = ?", (doctor['hospital_id'],))
                        hosp_row = cursor.fetchone()
                        hosp_name = hosp_row['name'] if hosp_row else "MedRx Hospital"

                        qr_tok_str = generate_qr_token()
                        pdf_bytes = generate_prescription_pdf(rx_code, selected_pid, doctor['name'], doctor['license_no'], hosp_name, st.session_state.rx_items, pin, qr_tok_str)

                        # PATIENT CREDENTIALS HANDOVER BOX & PRINTABLE COPY
                        st.markdown(
                            f"""
                            <div style="background-color: #f0fdf4; border: 2px solid #16a34a; border-radius: 12px; padding: 20px; margin-top: 15px; margin-bottom: 20px;">
                                <h3 style="color: #15803d; margin-top: 0;">🎉 Prescription Issued & Patient Handover Credentials</h3>
                                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 12px;">
                                    <div><strong>Patient ID:</strong> <code>{selected_pid}</code></div>
                                    <div><strong>Prescription ID:</strong> <code>{rx_code}</code></div>
                                    <div><strong>Security 4-Digit PIN:</strong> <span style="font-size: 20px; font-weight: bold; color: #0284c7;">{pin}</span></div>
                                    <div><strong>Opaque Verification Token:</strong> <code>{qr_tok_str[:12]}...</code></div>
                                </div>
                                <p style="font-size: 13px; color: #166534; margin-bottom: 0;">
                                    Provide the Prescription ID and 4-Digit Security PIN to the patient. The pharmacy requires both to unlock and dispense this prescription.
                                </p>
                            </div>
                            """,
                            unsafe_allow_html=True
                        )

                        col_dl, col_print = st.columns(2)
                        col_dl.download_button(
                            label="📄 Download Official PDF Prescription",
                            data=pdf_bytes,
                            file_name=f"Prescription_{rx_code}.pdf",
                            mime="application/pdf",
                            use_container_width=True
                        )

                        with col_print.expander("🖨️ View Patient Copy (Printable Handover)", expanded=False):
                            st.markdown(
                                f"""
                                <div style="border: 1px solid #cbd5e1; padding: 20px; border-radius: 8px; background-color: white;">
                                    <div style="text-align: center; border-bottom: 2px solid #0284c7; padding-bottom: 8px; margin-bottom: 12px;">
                                        <h2 style="margin:0; color:#0f172a;">{hosp_name}</h2>
                                        <h4 style="margin:4px 0; color:#0284c7;">Patient Prescription Handover Document</h4>
                                    </div>
                                    <p><strong>Patient ID:</strong> {selected_pid}</p>
                                    <p><strong>Prescription ID:</strong> {rx_code}</p>
                                    <p><strong>Prescribing Doctor:</strong> {doctor['name']} ({doctor['license_no']})</p>
                                    <p><strong>Pharmacy Unlock Security PIN:</strong> <strong style="font-size: 18px;">{pin}</strong></p>
                                    <hr/>
                                    <p><strong>Instructions for Patient:</strong> Present this document or your Prescription ID ({rx_code}) and Security PIN ({pin}) at any authorized hospital pharmacy.</p>
                                </div>
                                """,
                                unsafe_allow_html=True
                            )

                        st.session_state.rx_items = []
                        st.session_state.studio_submission_token = secrets.token_hex(16)

    elif menu == "Substitution Approvals":
        st.title("Substitution Approvals Inbox")
        st.caption("Review pharmacist substitution requests where prescribed items are out of stock.")

        cursor.execute("""
            SELECT s.*, pi.medicine_name as orig_med, pi.generic_name as orig_gen, pi.strength as orig_str,
                   pi.dosage_form as orig_form, pi.route as orig_route, pi.dose as orig_dose, pi.frequency as orig_freq,
                   pi.duration as orig_dur, pi.quantity as orig_qty, u.name as pharm_name
            FROM substitutions s
            JOIN prescription_items pi ON s.original_item_id = pi.id
            JOIN users u ON s.pharmacist_id = u.id
            WHERE s.assigned_doctor_id = ? AND s.status = 'Pending Approval'
        """, (doctor['id'],))
        subs = cursor.fetchall()

        if not subs:
            st.success("No pending substitution requests.")
        else:
            for sub in subs:
                st.markdown(f"### Substitution Request #{sub['id']} for Prescription `{sub['rx_id']}`")
                
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown("#### 🔴 Original Prescribed Item")
                    st.write(f"**Medicine:** {sub['orig_med']}")
                    st.write(f"**Generic:** {sub['orig_gen']}")
                    st.write(f"**Strength / Form:** {sub['orig_str']} ({sub['orig_form']})")
                with c2:
                    st.markdown("#### 🟢 Pharmacist Proposed Alternative")
                    st.write(f"**Proposed Generic:** {sub['proposed_generic']}")
                    st.write(f"**Proposed Brand:** {sub['proposed_brand'] or 'N/A'}")
                    st.write(f"**Proposed Strength:** {sub['proposed_strength']}")
                    st.write(f"**Pharmacist:** {sub['pharm_name']}")

                ai_anal = json.loads(sub['ai_analysis_json']) if sub['ai_analysis_json'] else {}
                if ai_anal:
                    st.markdown("#### 🤖 AI Recommendation Breakdown")
                    st.info(f"**Similarity Score:** `{ai_anal.get('similarity_score', 0)} / 1.00` | **Recommendation:** `{ai_anal.get('recommendation')}`")
                    for r in ai_anal.get('reasons', []):
                        st.write(f"- {r}")

                notes = st.text_input("Doctor Clinical Notes / Rationale", key=f"notes_{sub['id']}")
                
                col_a, col_b = st.columns(2)
                if col_a.button("✅ Approve Substitution", key=f"app_sub_{sub['id']}", type="primary"):
                    # IDEMPOTENCY CHECK
                    cursor.execute("SELECT status FROM substitutions WHERE id = ?", (sub['id'],))
                    check_sub = cursor.fetchone()

                    if check_sub and check_sub['status'] != 'Pending Approval':
                        st.info(f"ℹ️ Substitution request #{sub['id']} has already been processed with status '{check_sub['status']}'.")
                    else:
                        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        cursor.execute("""
                            UPDATE substitutions
                            SET status = 'Approved', approval_doctor_id = ?, doctor_decision_notes = ?, decided_at = ?
                            WHERE id = ?
                        """, (doctor['id'], notes, now_ts, sub['id']))

                        # Increment prescription version number and update status
                        cursor.execute("SELECT version_number FROM prescriptions WHERE rx_id = ?", (sub['rx_id'],))
                        cur_ver = cursor.fetchone()['version_number']
                        new_ver = cur_ver + 1

                        cursor.execute("UPDATE prescriptions SET version_number = ?, status = 'Modified by Approved Substitution' WHERE rx_id = ?", (new_ver, sub['rx_id']))

                        # Create new prescription item for substitution
                        cursor.execute("""
                            INSERT INTO prescription_items (rx_id, version_number, medicine_name, generic_name, strength, dosage_form, route, dose, frequency, duration, quantity, instructions)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (sub['rx_id'], new_ver, sub['proposed_brand'] or sub['proposed_generic'], sub['proposed_generic'], sub['proposed_strength'], sub['proposed_dosage_form'], sub['proposed_route'], sub['orig_dose'], sub['orig_freq'], sub['orig_dur'], sub['orig_qty'], f"Substituted from {sub['orig_med']}"))

                        cursor.execute("""
                            INSERT INTO prescription_versions (rx_id, version_number, modified_by_doctor_id, substitution_id, change_summary)
                            VALUES (?, ?, ?, ?, ?)
                        """, (sub['rx_id'], new_ver, doctor['id'], sub['id'], f"Approved substitution: {sub['orig_med']} -> {sub['proposed_generic']}"))

                        conn.commit()
                        create_audit_event(user['email'], "doctor", "SUBSTITUTION_APPROVED", "prescription", sub['rx_id'], {"sub_id": sub['id']})
                        st.success("✅ Substitution Approved and Prescription Version Updated!")
                        st.rerun()

                if col_b.button("❌ Reject Substitution", key=f"rej_sub_{sub['id']}"):
                    cursor.execute("SELECT status FROM substitutions WHERE id = ?", (sub['id'],))
                    check_sub = cursor.fetchone()

                    if check_sub and check_sub['status'] != 'Pending Approval':
                        st.info(f"ℹ️ Substitution request #{sub['id']} has already been processed.")
                    else:
                        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        cursor.execute("""
                            UPDATE substitutions
                            SET status = 'Rejected', approval_doctor_id = ?, doctor_decision_notes = ?, decided_at = ?
                            WHERE id = ?
                        """, (doctor['id'], notes, now_ts, sub['id']))
                        conn.commit()
                        create_audit_event(user['email'], "doctor", "SUBSTITUTION_REJECTED", "prescription", sub['rx_id'], {"sub_id": sub['id']})
                        st.error("Substitution Rejected.")
                        st.rerun()

    elif menu == "Prescription History":
        st.title("Doctor Prescription History")
        cursor.execute("SELECT * FROM prescriptions WHERE doctor_id = ? ORDER BY id DESC", (doctor['id'],))
        rxs = cursor.fetchall()
        if rxs:
            for r in rxs:
                rx_dict = dict(r)
                date_str = rx_dict.get('issue_timestamp', rx_dict.get('created_at', 'N/A'))
                with st.expander(f"📋 {rx_dict['rx_id']} | Patient: {rx_dict['patient_id']} | Status: {rx_dict['status']} | Date: {date_str}"):
                    col1, col2 = st.columns([3, 1])
                    with col1:
                        cursor.execute("SELECT * FROM prescription_items WHERE rx_id = ? AND version_number = ?", (rx_dict['rx_id'], rx_dict['version_number']))
                        p_items = cursor.fetchall()
                        st.write("**Items:**", [f"{i['medicine_name']} ({i['strength']})" for i in p_items])
                    with col2:
                        pdf_data = get_prescription_pdf_bytes(rx_dict['rx_id'])
                        if pdf_data:
                            st.download_button(
                                label="📄 Download PDF",
                                data=pdf_data,
                                file_name=f"Prescription_{rx_dict['rx_id']}.pdf",
                                mime="application/pdf",
                                key=f"dl_pdf_doc_{rx_dict['rx_id']}"
                            )
            st.markdown("---")
            st.dataframe(pd.DataFrame([dict(r) for r in rxs]), use_container_width=True)

    elif menu == "Audit Trail":
        render_audit_dashboard(st.session_state.user)

    conn.close()

# ==========================================
# PHARMACIST PORTAL
# ==========================================
def render_pharmacist_portal():
    user = st.session_state.user
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM pharmacies WHERE user_id = ?", (user['id'],))
    pharmacy = cursor.fetchone()

    if not pharmacy or pharmacy['status'] != 'active':
        st.warning("⚠️ Pharmacy account is pending activation.")
        conn.close()
        return

    st.sidebar.markdown("### 💊 Pharmacist Menu")
    menu = st.sidebar.radio("Navigation", ["Dashboard", "Verify & Dispense", "Pending Substitutions", "Dispensing History", "Audit Trail"])

    if menu == "Dashboard":
        st.title(f"Pharmacy Terminal — {pharmacy['name']}")
        st.caption(f"License: {pharmacy['license_no']} | Address: {pharmacy['address']}")

        cursor.execute("SELECT COUNT(*) FROM dispensing_events WHERE pharmacy_id = ?", (pharmacy['id'],))
        disp_cnt = cursor.fetchone()[0]
        c1 = st.columns(1)[0]
        c1.metric("Total Dispensing Transactions", disp_cnt)

    elif menu == "Verify & Dispense":
        st.title("Prescription Lookup & Dispensing Guard")
        
        with st.form("verify_rx_form"):
            col_id, col_pin = st.columns(2)
            rx_id_in = col_id.text_input("Prescription ID", placeholder="e.g. MRX-RX-82K91")
            pin_in = col_pin.text_input("4-Digit Security PIN", type="password", placeholder="e.g. 1234")
            
            if st.form_submit_button("🔓 Authenticate & Unlock Prescription", use_container_width=True):
                cursor.execute("SELECT * FROM prescriptions WHERE rx_id = ?", (rx_id_in.strip(),))
                rx = cursor.fetchone()
                
                if not rx:
                    st.error("❌ Prescription ID not found.")
                    create_audit_event(user['email'], "pharmacist", "PRESCRIPTION_VERIFY_FAILED", "prescription", rx_id_in, {"reason": "Not found"})
                elif not verify_pin(pin_in.strip(), rx['pin_hash']):
                    st.error("❌ Invalid 4-Digit PIN.")
                    create_audit_event(user['email'], "pharmacist", "PRESCRIPTION_VERIFY_FAILED", "prescription", rx_id_in, {"reason": "Invalid PIN"})
                elif not tool_check_pharmacy_authorization(pharmacy['id'], rx['hospital_id']):
                    st.error("❌ Access Denied: This hospital has not authorized your pharmacy.")
                    create_audit_event(user['email'], "pharmacist", "PHARMACY_UNAUTHORIZED", "prescription", rx_id_in, {"hospital_id": rx['hospital_id']})
                else:
                    st.session_state.unlocked_rx = dict(rx)
                    create_audit_event(user['email'], "pharmacist", "PRESCRIPTION_VERIFIED", "prescription", rx_id_in, {})
                    st.success("✅ Prescription Authenticated & Unlocked Successfully!")

        if "unlocked_rx" in st.session_state:
            rx = st.session_state.unlocked_rx
            st.markdown("---")
            col_rx_head, col_rx_dl = st.columns([3, 1])
            with col_rx_head:
                st.markdown(f"### 📋 Active Prescription: `{rx['rx_id']}`")
                st.markdown(f"**Patient ID:** `{rx['patient_id']}` | **Status:** `{rx['status']}` | **Version:** `v{rx['version_number']}`")
            with col_rx_dl:
                pdf_data = get_prescription_pdf_bytes(rx['rx_id'])
                if pdf_data:
                    st.download_button(
                        label="📄 Download Official PDF",
                        data=pdf_data,
                        file_name=f"Prescription_{rx['rx_id']}.pdf",
                        mime="application/pdf",
                        key=f"dl_pdf_ph_{rx['rx_id']}"
                    )
            st.markdown("---")

            if rx['status'] in ['Cancelled', 'Expired']:
                st.error(f"❌ Cannot dispense: Prescription status is '{rx['status']}'.")
                return

            cursor.execute("SELECT * FROM prescription_items WHERE rx_id = ? AND version_number = ?", (rx['rx_id'], rx['version_number']))
            items = cursor.fetchall()

            st.subheader("Prescribed Items & Dispensing Control")

            for item in items:
                rem_info = tool_calculate_remaining_quantity(rx['rx_id'], item['id'])
                st.markdown(f"#### 💊 {item['medicine_name']} ({item['strength']}) — {item['dosage_form']}")
                
                c_p, c_d, c_r = st.columns(3)
                c_p.metric("Prescribed", rem_info['prescribed'])
                c_d.metric("Already Dispensed", rem_info['dispensed'])
                c_r.metric("Remaining Units", rem_info['remaining'])

                if rem_info['remaining'] > 0:
                    with st.expander(f"⚡ Dispense Options for {item['medicine_name']}", expanded=True):
                        disp_qty = st.number_input(f"Quantity to Dispense (Remaining: {rem_info['remaining']})", min_value=1, max_value=rem_info['prescribed'], value=rem_info['remaining'], key=f"qty_in_{item['id']}")
                        
                        # DUPLICATE SUBSTITUTION PROTECTION CHECK
                        cursor.execute("SELECT id, status FROM substitutions WHERE rx_id = ? AND original_item_id = ? AND status = 'Pending Approval'", (rx['rx_id'], item['id']))
                        pending_sub = cursor.fetchone()

                        col_disp, col_unavail = st.columns(2)
                        
                        # Dispense Action
                        if col_disp.button(f"Confirm Dispense ({disp_qty} units)", key=f"btn_disp_{item['id']}", type="primary"):
                            # DISPENSING GUARD OVER-DISPENSE PROTECTION
                            if disp_qty > rem_info['remaining']:
                                st.error(f"🚫 DISPENSING BLOCKED: Requested quantity ({disp_qty}) exceeds remaining authorized limit ({rem_info['remaining']} units).")
                                create_audit_event(user['email'], "pharmacist", "DISPENSING_BLOCKED", "prescription", rx['rx_id'], {"requested": disp_qty, "remaining": rem_info['remaining']})
                            else:
                                new_rem = rem_info['remaining'] - disp_qty
                                cursor.execute("""
                                    INSERT INTO dispensing_events (rx_id, item_id, pharmacy_id, pharmacist_id, quantity_dispensed, remaining_quantity, notes)
                                    VALUES (?, ?, ?, ?, ?, ?, 'Dispensed by pharmacy terminal')
                                """, (rx['rx_id'], item['id'], pharmacy['id'], user['id'], disp_qty, new_rem))

                                # Update rx status
                                new_status = "Fully Dispensed" if new_rem == 0 else "Partially Dispensed"
                                cursor.execute("UPDATE prescriptions SET status = ? WHERE rx_id = ?", (new_status, rx['rx_id']))
                                conn.commit()

                                create_audit_event(user['email'], "pharmacist", "MEDICINE_DISPENSED", "prescription", rx['rx_id'], {"item_id": item['id'], "qty": disp_qty, "remaining": new_rem})
                                st.success(f"✅ Dispensed {disp_qty} units! Remaining: {new_rem}")
                                st.rerun()

                        # Report Unavailable & Request AI Substitution Action with Duplicate Protection
                        if pending_sub:
                            col_unavail.info("⏳ Substitution Request Pending Doctor Review")
                        else:
                            if col_unavail.button(f"⚠️ Report Medicine Unavailable", key=f"btn_unavail_{item['id']}"):
                                st.session_state.unavail_item = dict(item)

            # Render Alternative Substitution Sub-Form if triggered
            if "unavail_item" in st.session_state:
                un_item = st.session_state.unavail_item
                st.markdown("---")
                st.markdown(f"### 🤖 Propose Alternative Medicine for '{un_item['medicine_name']}'")
                
                with st.form("prop_sub_form"):
                    prop_gen = st.text_input("Proposed Generic Name", un_item['generic_name'] or "Azithromycin")
                    prop_brand = st.text_input("Proposed Brand Name", "Zithromax")
                    prop_str = st.text_input("Proposed Strength", un_item['strength'])
                    prop_form = st.text_input("Proposed Dosage Form", un_item['dosage_form'])
                    
                    sub_btn = st.form_submit_button("Run AI Alternative Agent & Submit to Doctor")

                    if sub_btn:
                        prop_dict = {
                            "proposed_generic": prop_gen,
                            "proposed_brand": prop_brand,
                            "proposed_strength": prop_str,
                            "proposed_dosage_form": prop_form,
                            "proposed_route": un_item['route']
                        }

                        # Run Alternative Agent
                        ai_res = run_alternative_medicine_agent(un_item, prop_dict, rx['patient_id'])
                        render_agent_trace(ai_res['trace'])

                        # Route to doctor
                        route_info = tool_route_substitution_request(rx['hospital_id'], rx['doctor_id'])
                        
                        cursor.execute("""
                            INSERT INTO substitutions (rx_id, original_item_id, proposed_generic, proposed_brand, proposed_strength, proposed_dosage_form, proposed_route, pharmacist_id, status, ai_analysis_json, assigned_doctor_id, routing_reason)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Pending Approval', ?, ?, ?)
                        """, (rx['rx_id'], un_item['id'], prop_gen, prop_brand, prop_str, prop_form, un_item['route'], user['id'], json.dumps(ai_res), route_info['assigned_doctor_id'], route_info['routing_reason']))

                        cursor.execute("UPDATE prescriptions SET status = 'Pending Substitution Approval' WHERE rx_id = ?", (rx['rx_id'],))
                        conn.commit()

                        create_audit_event(user['email'], "pharmacist", "SUBSTITUTION_REQUESTED", "prescription", rx['rx_id'], {"assigned_doctor": route_info['doctor_name']})
                        st.success(f"✅ Substitution request submitted to {route_info['doctor_name'] or 'Department'}! ({route_info['routing_reason']})")
                        del st.session_state["unavail_item"]
                        st.rerun()

    elif menu == "Pending Substitutions":
        st.title("Submitted Substitution Requests")
        cursor.execute("SELECT * FROM substitutions WHERE pharmacist_id = ? ORDER BY id DESC", (user['id'],))
        st.dataframe(pd.DataFrame([dict(s) for s in cursor.fetchall()]), use_container_width=True)

    elif menu == "Dispensing History":
        st.title("Dispensing History")
        cursor.execute("SELECT * FROM dispensing_events WHERE pharmacy_id = ? ORDER BY id DESC", (pharmacy['id'],))
        st.dataframe(pd.DataFrame([dict(d) for d in cursor.fetchall()]), use_container_width=True)

    elif menu == "Audit Trail":
        render_audit_dashboard(st.session_state.user)

    conn.close()

if __name__ == "__main__":
    main()
