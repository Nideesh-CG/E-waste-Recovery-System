"""Report generation module for Circular E-Waste Recovery platform.
Provides PDF generation via ReportLab and CSV data exports using database records.
All user and database inputs are XML/HTML escaped before rendering in ReportLab.
"""

import html
import io
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def esc(value: Any) -> str:
    """Escape untrusted text to prevent XML/HTML injection in ReportLab Paragraphs."""
    if value is None:
        return "N/A"
    return html.escape(str(value))


def generate_assessment_pdf(device: Any, assessment: Optional[Dict[str, Any]]) -> bytes:
    """Generate a formal technical assessment PDF for a device from real assessment data.
    
    Raises ValueError if assessment data is missing (prevents fabricating fake assessment PDFs).
    """
    if not assessment:
        raise ValueError("Assessment record is required to generate assessment report.")

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40,
        pageCompression=0,
    )
    styles = getSampleStyleSheet()

    primary_color = colors.HexColor("#1b4332")
    secondary_color = colors.HexColor("#2d6a4f")
    text_color = colors.HexColor("#212529")
    accent_bg = colors.HexColor("#edf4e8")

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=primary_color,
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#52796f"),
    )
    section_style = ParagraphStyle(
        "SectionHeading",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=secondary_color,
        spaceBefore=12,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "BodyDark",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=text_color,
    )
    bold_style = ParagraphStyle(
        "BodyDarkBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=12,
        textColor=text_color,
    )

    story = []

    story.append(Paragraph("CIRCULAR E-WASTE RECOVERY PLATFORM", title_style))
    story.append(
        Paragraph(
            "Technical Assessment & Refurbishment Intelligence Report",
            subtitle_style,
        )
    )
    story.append(Spacer(1, 10))
    story.append(
        HRFlowable(
            width="100%",
            thickness=2,
            color=primary_color,
            spaceBefore=4,
            spaceAfter=14,
        )
    )

    # 1. Device Metadata Table
    story.append(
        Paragraph("1. Device Identification & Registration Data", section_style)
    )
    dev_data = [
        [
            Paragraph("Device ID:", bold_style),
            Paragraph(esc(device["id"]), body_style),
            Paragraph("Registered Date:", bold_style),
            Paragraph(esc(device.get("date") or "N/A"), body_style),
        ],
        [
            Paragraph("Device Name:", bold_style),
            Paragraph(esc(device["name"]), body_style),
            Paragraph("Category / Type:", bold_style),
            Paragraph(esc(device["type"]), body_style),
        ],
        [
            Paragraph("Brand & Model:", bold_style),
            Paragraph(f"{esc(device['brand'])} {esc(device['model'])}", body_style),
            Paragraph("Purchase Year:", bold_style),
            Paragraph(esc(device["year"]), body_style),
        ],
        [
            Paragraph("Physical Condition:", bold_style),
            Paragraph(esc(device["condition"]), body_style),
            Paragraph("Weight (kg):", bold_style),
            Paragraph(f"{esc(device['weight'])} kg", body_style),
        ],
        [
            Paragraph("Power / Screen:", bold_style),
            Paragraph(
                f"{esc(device.get('power', 'N/A'))} / {esc(device.get('screen', 'N/A'))}",
                body_style,
            ),
            Paragraph("Current Status:", bold_style),
            Paragraph(esc(device["status"]), body_style),
        ],
    ]
    meta_table = Table(dev_data, colWidths=[100, 165, 100, 165])
    meta_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8faf9")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d8e2dc")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e8f0ec")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(meta_table)
    story.append(Spacer(1, 12))

    # 2. Assessment Intelligence from real database record
    story.append(Paragraph("2. Partner Technical Assessment & Metrics", section_style))

    rep_score = assessment.get("repairability_score", 0)
    circ_score = assessment.get("circular_recovery_score", 0)
    rep_cost = assessment.get("repair_cost", 0.0)
    comp_val = assessment.get("component_value", 0.0)
    action = assessment.get("recommendation", "UNKNOWN")
    co2 = assessment.get("co2_avoided_kg", 0.0)
    assessor = assessment.get("assessed_by", "Refurbishment Partner")

    metrics_data = [
        [
            Paragraph("Metric / Indicator", bold_style),
            Paragraph("Evaluated Value", bold_style),
            Paragraph("Interpretation / Analysis", bold_style),
        ],
        [
            Paragraph("Repairability Score", body_style),
            Paragraph(f"<b>{rep_score} / 100</b>", body_style),
            Paragraph(
                "High second-life refurbishment viability"
                if rep_score >= 70
                else "Moderate repairability; component harvesting indicated"
                if rep_score >= 40
                else "Low repairability; material recycling recommended",
                body_style,
            ),
        ],
        [
            Paragraph("Circular Recovery Score", body_style),
            Paragraph(f"<b>{circ_score} / 100</b>", body_style),
            Paragraph("Overall resource recovery potential", body_style),
        ],
        [
            Paragraph("Estimated Repair Cost", body_style),
            Paragraph(f"<b>₹{rep_cost:,.0f}</b>", body_style),
            Paragraph("Labor and component replacement required", body_style),
        ],
        [
            Paragraph("Component Recovery Value", body_style),
            Paragraph(f"<b>₹{comp_val:,.0f}</b>", body_style),
            Paragraph("Market valuation of recoverable modules", body_style),
        ],
        [
            Paragraph("Environmental Benefit", body_style),
            Paragraph(f"<b>{co2:g} kg CO₂ avoided</b>", body_style),
            Paragraph("Averted emissions vs virgin manufacturing", body_style),
        ],
        [
            Paragraph("Recommended Action", bold_style),
            Paragraph(f"<b>{esc(action)}</b>", bold_style),
            Paragraph("Optimal circular pathway based on cost/yield matrix", bold_style),
        ],
    ]
    metrics_table = Table(metrics_data, colWidths=[160, 140, 230])
    metrics_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), accent_bg),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#c8d6cf")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e8f0ec")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(metrics_table)
    story.append(Spacer(1, 12))

    # 3. Decision Factors & Technical Notes
    story.append(Paragraph("3. Technical Assessment Notes & Factors", section_style))
    notes = assessment.get("notes")
    if notes:
        story.append(Paragraph(f"<b>Notes:</b> {esc(notes)}", body_style))
        story.append(Spacer(1, 4))

    factors = assessment.get("factors")
    if factors:
        factor_list = []
        if isinstance(factors, list):
            factor_list = factors
        elif isinstance(factors, str):
            try:
                factor_list = json.loads(factors)
            except Exception:
                factor_list = [factors]
        for factor in factor_list:
            story.append(Paragraph(f"• {esc(factor)}", body_style))
            story.append(Spacer(1, 2))
    elif not notes:
        story.append(Paragraph("No additional technical factors noted by assessor.", body_style))

    story.append(Spacer(1, 14))
    story.append(
        HRFlowable(
            width="100%",
            thickness=0.5,
            color=colors.HexColor("#b7c9be"),
            spaceBefore=6,
            spaceAfter=8,
        )
    )

    assessed_time = assessment.get("assessed_at") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    footer_text = (
        f"Assessed by: {esc(assessor)} | Timestamp: {esc(assessed_time)} | "
        f"Report ID: RPT-{esc(device['id'])} | Verified Database Record · Circular E-Waste Platform"
    )
    story.append(
        Paragraph(
            footer_text,
            ParagraphStyle(
                "Footer",
                fontName="Helvetica",
                fontSize=7,
                textColor=colors.HexColor("#7a8f83"),
            ),
        )
    )

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def generate_summary_pdf(metrics: Dict[str, Any], devices: List[Dict[str, Any]]) -> bytes:
    """Generate an executive summary PDF of e-waste collection and recovery from database records."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40,
        pageCompression=0,
    )
    styles = getSampleStyleSheet()

    primary_color = colors.HexColor("#1b4332")
    secondary_color = colors.HexColor("#2d6a4f")

    title_style = ParagraphStyle(
        "Title",
        fontName="Helvetica-Bold",
        fontSize=18,
        textColor=primary_color,
        leading=22,
    )
    section_style = ParagraphStyle(
        "Section",
        fontName="Helvetica-Bold",
        fontSize=12,
        textColor=secondary_color,
        spaceBefore=10,
        spaceAfter=6,
    )
    body_style = ParagraphStyle("Body", fontName="Helvetica", fontSize=9, leading=12)
    bold_style = ParagraphStyle("BodyBold", fontName="Helvetica-Bold", fontSize=9, leading=12)

    story = [
        Paragraph("CIRCULAR E-WASTE RECOVERY PLATFORM", title_style),
        Paragraph(
            f"Portfolio Recovery Summary Report — {datetime.now(timezone.utc).strftime('%B %Y')}",
            ParagraphStyle(
                "Sub",
                fontName="Helvetica",
                fontSize=10,
                textColor=colors.HexColor("#52796f"),
            ),
        ),
        HRFlowable(
            width="100%",
            thickness=2,
            color=primary_color,
            spaceBefore=6,
            spaceAfter=14,
        ),
        Paragraph("1. Core Portfolio Metrics (Database-Backed)", section_style),
    ]

    metric_rows = [
        [
            Paragraph("Metric", bold_style),
            Paragraph("Portfolio Count / Value", bold_style),
        ],
        [
            Paragraph("Total E-Waste Items Registered", body_style),
            Paragraph(str(metrics.get("total_devices", 0)), body_style),
        ],
        [
            Paragraph("In Refurbishment Stream", body_style),
            Paragraph(str(metrics.get("refurbishment_candidates", 0)), body_style),
        ],
        [
            Paragraph("In Parts Recovery Stream", body_style),
            Paragraph(str(metrics.get("parts_recovery", 0)), body_style),
        ],
        [
            Paragraph("Pending Assessment / Processing", body_style),
            Paragraph(str(metrics.get("pending_count", 0)), body_style),
        ],
        [
            Paragraph("Completed Recovery / Recycling", body_style),
            Paragraph(str(metrics.get("completed_count", 0)), body_style),
        ],
        [
            Paragraph("Total Verified Recovery Value (INR)", body_style),
            Paragraph(f"₹{metrics.get('total_recovery_value', 0):,.0f}", body_style),
        ],
        [
            Paragraph("Total Estimated Repair Costs (INR)", body_style),
            Paragraph(f"₹{metrics.get('total_repair_cost', 0):,.0f}", body_style),
        ],
        [
            Paragraph("Total CO2 Emissions Diverted", body_style),
            Paragraph(f"{metrics.get('total_co2_kg', 0):,.1f} kg CO₂", body_style),
        ],
        [
            Paragraph("Circular Recovery Rate", body_style),
            Paragraph(f"{metrics.get('circular_recovery_rate', 0)}%", body_style),
        ],
    ]
    table = Table(metric_rows, colWidths=[260, 270])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf4e8")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#c8d6cf")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e8f0ec")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 14))

    story.append(Paragraph("2. Registered Device Inventory", section_style))
    dev_header = [
        Paragraph("Device ID", bold_style),
        Paragraph("Name / Model", bold_style),
        Paragraph("Type", bold_style),
        Paragraph("Status", bold_style),
        Paragraph("Registration Date", bold_style),
    ]
    dev_rows = [dev_header]
    for d in devices[:25]:
        dev_rows.append(
            [
                Paragraph(esc(d["id"]), body_style),
                Paragraph(f"{esc(d['brand'])} {esc(d['model'])}", body_style),
                Paragraph(esc(d["type"]), body_style),
                Paragraph(esc(d["status"]), body_style),
                Paragraph(esc(d.get("date", "")), body_style),
            ]
        )

    if len(devices) == 0:
        dev_rows.append(
            [
                Paragraph("No devices registered in database.", body_style),
                Paragraph("", body_style),
                Paragraph("", body_style),
                Paragraph("", body_style),
                Paragraph("", body_style),
            ]
        )

    dev_table = Table(dev_rows, colWidths=[95, 160, 85, 105, 85])
    dev_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf4e8")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#c8d6cf")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e8f0ec")),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(dev_table)
    story.append(Spacer(1, 14))

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()
