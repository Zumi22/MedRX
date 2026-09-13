# 💊 MedRx: An AI-Powered Closed-Loop Prescription Management System

[![Streamlit Cloud](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://streamlit.io/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

> **HEC–NCEAC & PEC Generative & Agentic AI Training — Cohort 11 Mid-Term Hackathon Project**

MedRx is an intelligent, privacy-preserving prescription lifecycle platform connecting physicians, pharmacies, and patients through a secure, auditable medication workflow. 

Traditional prescription workflows suffer from fragmented communication: once a physician issues a prescription, the patient leaves and the pharmacy operates independently. If a drug is out of stock, substitutions are often handled informally. MedRx closes the prescription loop through agentic AI verification, strict dispensing math, privacy-preserving QR authentication, and physician-in-the-loop substitution approvals.

---

## 🎯 The Core Concept: "MedRx Closes the Loop"

```
Prescribe (Doctor) ──> Verify (AI Verification Agent) ──> Authorize (Hospital Admin)
          │
          ▼
Dispense (Pharmacist Terminal) ──> Track Remaining Quantity (Dispensing Guard)
          │
          ▼ [If Out of Stock]
AI Alternative Agent ──> Human Physician Approval ──> Version Update (v1 -> v2)
          │
          ▼
Audit Everything (Immutable Event Log)
```

### Key Innovation
**Medication substitution is no longer an informal pharmacy decision.** It becomes a controlled, AI-assisted, physician-approved workflow. AI suggests clinical similarity and safety flags, but **AI NEVER autonomously approves a substitution** — explicit clinician authorization is strictly mandatory.

---

## 🚀 Key Features & Portal Roles

### 1. 👑 Master Admin Portal
- System-wide operational dashboard and real-time metrics.
- Register & activate/suspend hospitals.
- Create Hospital Admin accounts.
- View system users, active pharmacies, and global audit logs.
- One-click **"Reset / Initialize Demo Data"** for instant judging demonstration.

### 2. 🏥 Hospital Admin Portal
- Manage hospital profile and pharmacy allocations.
- Toggle **"Allow All Active Pharmacies"** or assign specific allocated pharmacies.
- Review self-registered doctors: **Approve, Reject, or Suspend**.
- Configure designated **On-Call Physician** for automated substitution approval routing.

### 3. 🩺 Doctor Portal
- **Canonical Patient Generation**: Creates privacy-preserving random Patient IDs (e.g. `MRX-AB7K-92QF`) with zero PHI.
- **Encounter Studio**: Log clinical observations, symptoms, history, and diagnosis.
- **Prescription Studio**: Select drugs from a curated 35+ item catalog, configure dose/frequency/qty, and trigger the **AI Prescription Verification Agent**.
- **ReportLab PDF Generator**: Downloads clinical prescription PDFs with embedded opaque QR code and 4-digit PIN.
- **Substitution Approval Inbox**: Review pharmacist substitution requests, inspect AI similarity score (`0.85 / 1.00`), and perform explicit **Approve / Reject** actions.

### 4. 💊 Pharmacist Portal
- **Secure Verification Terminal**: Authenticate prescriptions using `Prescription ID` + `4-Digit Security PIN`.
- **Hospital Authorization Guard**: Ensures pharmacy is authorized by the issuing hospital before granting access.
- **Dispensing Guard Agent**: Tracks prescribed, dispensed, and remaining quantities. Strictly blocks over-dispensing attempts with visual warnings.
- **Report Unavailable Workflow**: Propose alternative medicines and trigger the **AI Alternative Medicine Agent** to compute similarity and route substitution requests.

### 5. 🌐 Public QR Verification Route (`?verify=TOKEN`)
- Accessible via QR scan or URL parameter.
- **Zero-PHI Privacy Guarantee**: Displays minimal necessary read-only dispensing status (Prescription ID, hospital name, medicine, prescribed/dispensed/remaining units).
- Strips patient names, dates of birth, CNICs, and clinical diagnoses to preserve patient privacy.

---

## 🔑 Fast Demo Login Credentials

The application is pre-seeded with clean demo data. Use these credentials to test the full 3–5 minute hackathon workflow:

| Portal Role | Email Address | Password | Key Demo Task |
| :--- | :--- | :--- | :--- |
| **Master Admin** | `master@medrx.demo` | `DemoPass123!` | View hospitals, users, reset demo data |
| **Hospital Admin** | `admin@riphah-demo.medrx` | `DemoPass123!` | Approve pending doctors, manage pharmacy access |
| **Doctor** | `doctor@medrx.demo` | `DemoPass123!` | Issue prescriptions, approve substitution requests |
| **Pharmacist** | `pharmacy@medrx.demo` | `DemoPass123!` | Verify `MRX-RX-82K91` (PIN: `1234`), dispense, propose sub |

---

## 🤖 AI Agent Architecture & Deterministic Fallback

MedRx implements explicit agent orchestration:

1. **Prescription Verification Agent**: Checks required fields, dosage sanity, duplicate medication entries, catalog rules, and known drug-drug interactions.
2. **Medication Safety Agent**: Evaluates therapeutic classes and known clinical risk flags.
3. **Alternative Medicine Agent**: Calculates clinical similarity scores (`0.00` to `1.00`), compares dosage forms/routes, and generates structured recommendations (`APPROVE_REVIEW`, `REVIEW`, `REJECT`).
4. **Agent Trace Component**: Renders step-by-step visual progress traces in the Streamlit UI.
5. **Deterministic Fallback Engine**: **No LLM API key required to run.** If no external API key is provided, MedRx seamlessly executes intelligent deterministic rule logic without crashing.

---

## 🛠️ Local Installation & Running Guide

### Prerequisites
- Python 3.10, 3.11, 3.12, 3.13, or 3.14

### Steps
1. Clone the repository:
   ```bash
   git clone https://github.com/YOUR_USERNAME/medrx.git
   cd medrx
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the Streamlit application:
   ```bash
   streamlit run app.py
   ```

4. Open your browser at `http://localhost:8501`.

---

## ☁️ Streamlit Cloud Deployment Instructions

1. Push your repository to GitHub (ensure `app.py` and `requirements.txt` are at the repository root).
2. Go to [share.streamlit.io](https://share.streamlit.io/).
3. Connect your GitHub account and select your `medrx` repository.
4. Set Main File Path to `app.py`.
5. *(Optional)* Add optional LLM secrets in Streamlit Cloud Settings -> Secrets:
   ```toml
   LLM_PROVIDER = "openai"
   LLM_API_KEY = "your-api-key-here"
   LLM_MODEL = "gpt-4o-mini"
   ```
6. Click **Deploy**.

---

## 🛡️ Security & Privacy Standards

- **Password Hashing**: PBKDF2-HMAC-SHA256 with unique salting.
- **PIN & Token Hashing**: 4-digit PINs and QR tokens are stored as SHA-256 hashes in SQLite. Plaintext PINs are never logged.
- **SQL Injection Protection**: 100% parameterized SQL queries.
- **Privacy-by-Design**: QR codes contain secure opaque tokens (`http://YOUR-APP/?verify=TOKEN`), never PHI.
- **Database Architecture Notice**: SQLite is used for this hackathon prototype. Persistent multi-user clinical production deployment would require a managed database such as PostgreSQL.

---

## ⚠️ Prototype Notice & Disclaimer

> **PROTOTYPE — NOT FOR CLINICAL USE**  
> MedRx is a hackathon prototype developed for educational and demonstration purposes. It is a HIPAA-aligned privacy-by-design prototype and is not a certified medical device or clinical decision support system.
