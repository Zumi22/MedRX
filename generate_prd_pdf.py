import os
import sys
import datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, PageBreak, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch

def create_prd_pdf(filename="MedRx_Product_Requirement_Document.pdf"):
    doc = SimpleDocTemplate(
        filename,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()

    # Custom styles
    primary_color = colors.HexColor('#0284C7')
    dark_slate = colors.HexColor('#0F172A')
    text_dark = colors.HexColor('#1E293B')
    light_bg = colors.HexColor('#F8FAFC')
    border_color = colors.HexColor('#E2E8F0')

    title_style = ParagraphStyle(
        'PRDTitle',
        parent=styles['Heading1'],
        fontSize=22,
        leading=26,
        textColor=dark_slate,
        fontName='Helvetica-Bold',
        alignment=0
    )

    subtitle_style = ParagraphStyle(
        'PRDSubTitle',
        parent=styles['Normal'],
        fontSize=12,
        leading=16,
        textColor=primary_color,
        fontName='Helvetica-Bold'
    )

    h1_style = ParagraphStyle(
        'PRDH1',
        parent=styles['Heading2'],
        fontSize=14,
        leading=18,
        textColor=dark_slate,
        fontName='Helvetica-Bold',
        spaceBefore=14,
        spaceAfter=6
    )

    h2_style = ParagraphStyle(
        'PRDH2',
        parent=styles['Heading3'],
        fontSize=11,
        leading=14,
        textColor=primary_color,
        fontName='Helvetica-Bold',
        spaceBefore=8,
        spaceAfter=4
    )

    body_style = ParagraphStyle(
        'PRDBody',
        parent=styles['Normal'],
        fontSize=9.5,
        leading=13.5,
        textColor=text_dark,
        fontName='Helvetica',
        spaceAfter=6
    )

    meta_label = ParagraphStyle('MetaLabel', parent=styles['Normal'], fontSize=9, fontName='Helvetica-Bold', textColor=colors.HexColor('#475569'))
    meta_val = ParagraphStyle('MetaVal', parent=styles['Normal'], fontSize=9, fontName='Helvetica', textColor=dark_slate)

    story = []

    # ---------------------------------------------------------
    # HEADER & TITLE BLOCK
    # ---------------------------------------------------------
    header_data = [
        [
            Paragraph("<b>MedRx Platform</b><br/><font color='#0284C7'>Closed-Loop AI Prescription Management Network</font>", title_style),
            Paragraph("<b>Product Requirement Document (PRD)</b><br/>Official Engineering Specification v1.1", ParagraphStyle('RightHead', parent=subtitle_style, alignment=2))
        ]
    ]
    header_table = Table(header_data, colWidths=[4.0*inch, 3.0*inch])
    header_table.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP')]))
    story.append(header_table)
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=2, color=primary_color, spaceAfter=12))

    # Metadata Card Table
    meta_table_data = [
        [Paragraph("Project Title:", meta_label), Paragraph("<b>MedRx</b> — Agentic AI Prescription Management & Safety Platform", meta_val)],
        [Paragraph("Document Version:", meta_label), Paragraph("1.1 (Final Production Build & Architecture)", meta_val)],
        [Paragraph("Date:", meta_label), Paragraph(datetime.date.today().strftime("%B %d, %Y"), meta_val)],
        [Paragraph("Team Lead:", meta_label), Paragraph("<b>Engr. Zumair Ali</b>", meta_val)],
        [Paragraph("Development Team:", meta_label), Paragraph("<b>Dr. Muhammad Zahid</b>, <b>Engr. Mufeez Ilyas</b>, <b>Ramsha Fatima</b>", meta_val)],
        [Paragraph("Verification Status:", meta_label), Paragraph("<font color='#059669'><b>100% Automated Test Pass (98 / 98 Test Modules Verified)</b></font>", meta_val)]
    ]
    meta_table = Table(meta_table_data, colWidths=[1.6*inch, 5.4*inch])
    meta_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), light_bg),
        ('BOX', (0,0), (-1,-1), 0.5, border_color),
        ('GRID', (0,0), (-1,-1), 0.5, border_color),
        ('PADDING', (0,0), (-1,-1), 5),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 12))

    # ---------------------------------------------------------
    # 1. EXECUTIVE SUMMARY
    # ---------------------------------------------------------
    story.append(Paragraph("1. Executive Summary", h1_style))
    story.append(Paragraph(
        "<b>MedRx</b> is an enterprise-grade, agentic AI-powered healthcare platform engineered to eliminate prescription errors, prevent unauthorized medication dispensing, automate drug substitution approvals, and enforce strict regulatory audit compliance across modern healthcare networks. By unifying electronic health record (EHR) encounter logging, multi-agent artificial intelligence decision support, cryptographic QR code verification, 4-digit PIN security, and human-in-the-loop physician workflows, MedRx bridges the critical operational gap between hospital prescribing doctors and outpatient dispensing pharmacists.",
        body_style
    ))

    # ---------------------------------------------------------
    # 2. COMPREHENSIVE PROBLEM STATEMENT
    # ---------------------------------------------------------
    story.append(Paragraph("2. Comprehensive Problem Statement", h1_style))
    story.append(Paragraph(
        "In modern clinical healthcare systems, the transition of a prescription from a physician's desk to a patient's hands is fraught with critical failure points:",
        body_style
    ))

    prob_data = [
        [Paragraph("<b>Problem Domain</b>", meta_label), Paragraph("<b>Clinical Impact & Failure Mechanism</b>", meta_label)],
        [
            Paragraph("<b>Preventable Adverse Drug Events (ADEs)</b>", meta_val),
            Paragraph("Illegible paper prescriptions and fragmented EHR systems lack real-time allergen cross-referencing. Busy clinicians frequently miss underlying patient allergies (e.g., Penicillin) or drug-drug interactions, leading to preventable ADEs.", body_style)
        ],
        [
            Paragraph("<b>Dispensing Fraud & Over-Dispensing</b>", meta_val),
            Paragraph("Traditional prescription workflows rely on weak single-factor patient identification. Outpatient pharmacies lack real-time remaining-quantity tracking, allowing over-dispensing or duplicate refilling beyond prescribed limits.", body_style)
        ],
        [
            Paragraph("<b>Stockout Delays & Unregulated Substitutions</b>", meta_val),
            Paragraph("Pharmacy stockouts cause critical treatment delays. Pharmacists attempting informal substitutions risk selecting alternatives with differing bioavailability or incompatible dosage forms without standardized therapeutic similarity scoring.", body_style)
        ],
        [
            Paragraph("<b>Audit Gaps & AI Black-Box Risks</b>", meta_val),
            Paragraph("Standard hospital databases store actions in mutable tables subject to tampering. Modern AI tools fail to record step-by-step reasoning traces or metadata redactions required by HIPAA, ISO 27001, and GxP standards.", body_style)
        ]
    ]
    prob_table = Table(prob_data, colWidths=[2.2*inch, 4.8*inch])
    prob_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F1F5F9')),
        ('GRID', (0,0), (-1,-1), 0.5, border_color),
        ('PADDING', (0,0), (-1,-1), 6),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(prob_table)
    story.append(Spacer(1, 10))

    # ---------------------------------------------------------
    # 3. MULTI-AGENT AI ECOSYSTEM ARCHITECTURE
    # ---------------------------------------------------------
    story.append(Paragraph("3. Multi-Agent AI Ecosystem Architecture", h1_style))
    story.append(Paragraph(
        "MedRx implements a closed-loop multi-agent architecture where four specialized AI agents collaborate across clinical and operational boundaries. Human physician sign-off remains strictly enforced before any prescription modification takes effect.",
        body_style
    ))

    agent_data = [
        [Paragraph("<b>Agent Name</b>", meta_label), Paragraph("<b>Target Scope & Trigger Mechanism</b>", meta_label), Paragraph("<b>Core Clinical Responsibilities</b>", meta_label)],
        [
            Paragraph("<b>Agent 1:<br/>Prescription Verification</b>", meta_val),
            Paragraph("Automatic upon prescription creation in Doctor Studio", body_style),
            Paragraph("Inspects medication dosing units, strength specifications, and route compatibility (Oral vs. IV). Renders visual clinical pass/fail status.", body_style)
        ],
        [
            Paragraph("<b>Agent 2:<br/>Medication Safety</b>", meta_val),
            Paragraph("Parallel execution with Verification Agent", body_style),
            Paragraph("Cross-references patient allergy profile arrays and active chronic conditions against prescribed active pharmaceutical ingredients (APIs).", body_style)
        ],
        [
            Paragraph("<b>Agent 3:<br/>Alternative Medicine</b>", meta_val),
            Paragraph("Invoked on out-of-stock pharmacy reports", body_style),
            Paragraph("Calculates therapeutic similarity scores (0-100%), evaluates generic API equivalence, and packages structured proposals for doctor review.", body_style)
        ],
        [
            Paragraph("<b>Agent 4:<br/>Audit & Anomaly Detection</b>", meta_val),
            Paragraph("Background log scan execution", body_style),
            Paragraph("Evaluates audit logs against rule engines (repeated failed PINs, abnormal dispensing rates, unauthorized hospital access) to alert admins.", body_style)
        ]
    ]
    agent_table = Table(agent_data, colWidths=[1.8*inch, 2.0*inch, 3.2*inch])
    agent_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F1F5F9')),
        ('GRID', (0,0), (-1,-1), 0.5, border_color),
        ('PADDING', (0,0), (-1,-1), 6),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(agent_table)
    story.append(Spacer(1, 10))

    # ---------------------------------------------------------
    # 4. SYSTEM ROLES & FUNCTIONAL REQUIREMENTS
    # ---------------------------------------------------------
    story.append(Paragraph("4. System Roles & Functional Requirements", h1_style))

    roles_text = """
    <b>• Master Admin:</b> Hospital onboarding, administrator creation, pharmacy network registration, system-wide audit monitoring.<br/>
    <b>• Hospital Admin:</b> Doctor account approvals, hospital-pharmacy allocations, on-call doctor assignment, patient registry oversight.<br/>
    <b>• Prescribing Doctor:</b> Patient encounters (vitals, ICD-10 diagnostic coding, treatment plans), digital prescription issuance, PDF downloads, substitution inbox review.<br/>
    <b>• Dispensing Pharmacist:</b> Dual-credential authentication (Rx ID + 4-digit PIN), partial/full dispensing guard, out-of-stock alternative proposal.
    """
    story.append(Paragraph(roles_text, body_style))
    story.append(Spacer(1, 8))

    # ---------------------------------------------------------
    # 5. TECHNICAL & DATABASE ARCHITECTURE
    # ---------------------------------------------------------
    story.append(Paragraph("5. Technical Architecture & Security Specifications", h1_style))

    tech_data = [
        [Paragraph("<b>Layer / Component</b>", meta_label), Paragraph("<b>Technology Stack</b>", meta_label), Paragraph("<b>Security & Compliance Safeguards</b>", meta_label)],
        [Paragraph("Frontend UI", meta_val), Paragraph("Streamlit (Python)", body_style), Paragraph("Custom clinical theme (Teal #0284c7, Slate #f8fafc), dynamic widgets.", body_style)],
        [Paragraph("Database Engine", meta_val), Paragraph("SQLite3 (WAL Mode)", body_style), Paragraph("Write-Ahead Logging (busy_timeout=5000) for safe concurrent operations.", body_style)],
        [Paragraph("PDF & QR Engine", meta_val), Paragraph("ReportLab & QRCode", body_style), Paragraph("High-entropy zero-PHI QR tokens, SHA-256 token hash storage.", body_style)],
        [Paragraph("Security Cryptography", meta_val), Paragraph("Python hashlib & hmac", body_style), Paragraph("PBKDF2-HMAC-SHA256 password hashing, hmac.compare_digest PIN checks.", body_style)],
        [Paragraph("AI Engine & Fallback", meta_val), Paragraph("Google Gemini API SDK", body_style), Paragraph("Deterministic rule engine fallback ensures 100% uptime without API keys.", body_style)]
    ]
    tech_table = Table(tech_data, colWidths=[1.6*inch, 2.0*inch, 3.4*inch])
    tech_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F1F5F9')),
        ('GRID', (0,0), (-1,-1), 0.5, border_color),
        ('PADDING', (0,0), (-1,-1), 5),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    story.append(tech_table)
    story.append(Spacer(1, 10))

    # ---------------------------------------------------------
    # 6. QA VERIFICATION MATRIX
    # ---------------------------------------------------------
    story.append(Paragraph("6. Quality Assurance & Automated Verification Matrix", h1_style))

    qa_data = [
        [Paragraph("<b>Test Suite Module</b>", meta_label), Paragraph("<b>Target Scope</b>", meta_label), Paragraph("<b>Passed / Total</b>", meta_label), Paragraph("<b>Result</b>", meta_label)],
        [Paragraph("test_app.py", meta_val), Paragraph("Core DB, RBAC, Encounters, Prescriptions & PDF", body_style), Paragraph("40 / 40", body_style), Paragraph("<font color='#059669'><b>PASSED</b></font>", body_style)],
        [Paragraph("test_phase7.py", meta_val), Paragraph("Substitution Engine & Doctor Approval Routing", body_style), Paragraph("7 / 7", body_style), Paragraph("<font color='#059669'><b>PASSED</b></font>", body_style)],
        [Paragraph("test_phase8.py", meta_val), Paragraph("Audit Logging & Anomaly Detection Rules", body_style), Paragraph("15 / 15", body_style), Paragraph("<font color='#059669'><b>PASSED</b></font>", body_style)],
        [Paragraph("test_phase9.py", meta_val), Paragraph("QR Verification, Security & Zero-PHI Audit", body_style), Paragraph("16 / 16", body_style), Paragraph("<font color='#059669'><b>PASSED</b></font>", body_style)],
        [Paragraph("test_phase10.py", meta_val), Paragraph("Version Idempotency, Concurrency & UI Handover", body_style), Paragraph("20 / 20", body_style), Paragraph("<font color='#059669'><b>PASSED</b></font>", body_style)],
        [Paragraph("<b>TOTAL UNIFIED SUITE</b>", meta_val), Paragraph("<b>Comprehensive End-to-End System Verification</b>", meta_val), Paragraph("<b>98 / 98</b>", meta_val), Paragraph("<font color='#059669'><b>100% PASSED</b></font>", meta_val)]
    ]
    qa_table = Table(qa_data, colWidths=[1.5*inch, 3.5*inch, 1.0*inch, 1.0*inch])
    qa_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#F1F5F9')),
        ('GRID', (0,0), (-1,-1), 0.5, border_color),
        ('PADDING', (0,0), (-1,-1), 5),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(qa_table)
    story.append(Spacer(1, 14))

    # ---------------------------------------------------------
    # 7. SIGN-OFF BLOCK
    # ---------------------------------------------------------
    story.append(Paragraph("7. Project Approval & Author Sign-Off", h1_style))
    story.append(Spacer(1, 10))

    sig_data = [
        [
            Paragraph("<b>Team Lead:</b><br/><br/><br/>_______________________________<br/><b>Engr. Zumair Ali</b><br/>Lead Systems Architect", meta_val),
            Paragraph("<b>Development Team:</b><br/><br/><br/>_______________________________<br/><b>Dr. Muhammad Zahid</b><br/>Clinical & Domain Expert", meta_val),
            Paragraph("<b>Development Team:</b><br/><br/><br/>_______________________________<br/><b>Engr. Mufeez Ilyas</b><br/>Backend & AI Engineer", meta_val),
            Paragraph("<b>Development Team:</b><br/><br/><br/>_______________________________<br/><b>Ramsha Fatima</b><br/>QA & Security Analyst", meta_val)
        ]
    ]
    sig_table = Table(sig_data, colWidths=[1.75*inch, 1.75*inch, 1.75*inch, 1.75*inch])
    sig_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('PADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(sig_table)
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.5, color=border_color, spaceAfter=6))
    story.append(Paragraph("<font color='#64748B'><b>MedRx Healthcare Platform</b> | Product Requirement Document (PRD) v1.1 | Confidential & Proprietary</font>", ParagraphStyle('Foot', parent=body_style, fontSize=8, alignment=1)))

    doc.build(story)
    print(f"Successfully generated PRD PDF: {filename}")

if __name__ == "__main__":
    create_prd_pdf()
