"""Python-rendered Circular UI with persistent database, REST API, and partner integration.
Loads templates and static assets from frontend/ directory.
Preserves approved frontend visual hierarchy and design.
"""

import csv
import io
import json
import math
import os
import secrets
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlalchemy import inspect

import requests
from bs4 import BeautifulSoup
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_migrate import Migrate
from markupsafe import Markup

from api import api_bp
from config import Config
from metrics import get_platform_metrics
from models import AssessmentResult, Device, DeviceFile, Metadata, Transfer, db
from reports import generate_assessment_pdf, generate_summary_pdf
from storage import get_storage_service, validate_file_content, get_mime_type, MAX_FILE_SIZE
from werkzeug.exceptions import RequestEntityTooLarge

BACKEND_DIR = Path(__file__).resolve().parent
ROOT = BACKEND_DIR
FRONTEND_DIR = (BACKEND_DIR.parent / "frontend").resolve()

TEMPLATE_DIR = (
    FRONTEND_DIR / "templates"
    if (FRONTEND_DIR / "templates").exists()
    else BACKEND_DIR / "templates"
)
STATIC_DIR = (
    FRONTEND_DIR / "static"
    if (FRONTEND_DIR / "static").exists()
    else BACKEND_DIR / "static"
)

DEMO_PATH = (
    BACKEND_DIR / "demo.json"
    if (BACKEND_DIR / "demo.json").exists()
    else FRONTEND_DIR / "demo.json"
)
DEMO = json.loads(DEMO_PATH.read_text(encoding="utf-8"))

ICONS_PATH = (
    STATIC_DIR / "icons.json"
    if (STATIC_DIR / "icons.json").exists()
    else BACKEND_DIR / "static/icons.json"
)
ICONS = json.loads(ICONS_PATH.read_text(encoding="utf-8"))

migrate = Migrate()


def soup(html):
    return BeautifulSoup(str(html), "html.parser")


def replace_text(node, mapping):
    """Change content without changing the approved element structure/classes."""
    for text in list(node.find_all(string=True)):
        value = str(text)
        for old, new in mapping.items():
            value = value.replace(str(old), str(new))
        if value != str(text):
            text.replace_with(value)


def badge(node, status):
    colors = {
        "Failed": "bg-red-50 text-red-600",
        "Pending": "bg-[#fff5df] text-[#a07829]",
        "Pending assessment": "bg-[#fff5df] text-[#a07829]",
        "Parts recovery": "bg-[#eef0ff] text-[#6870ac]",
        "Processing": "bg-[#eaf5f6] text-[#33858d]",
        "Refurbishment": "bg-[#eaf5f6] text-[#33858d]",
    }
    node["class"] = (
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-md "
        "px-2 py-1 text-[10px] font-medium "
        + colors.get(status, "bg-[#edf6ef] text-[#3b8655]")
    ).split()
    for text in list(node.find_all(string=True)):
        text.extract()
    node.append(status)


def icon(node, device_type, size=19):
    replacement = soup(ICONS.get(device_type, ICONS.get("Other", ICONS.get("Cpu")))).svg
    if replacement:
        replacement["width"] = replacement["height"] = str(size)
        node.replace_with(replacement)


def link(button, href):
    button["data-href"] = href
    button["type"] = "button"


def create_app(database=None, config_class=None):
    app = Flask(
        __name__,
        template_folder=str(TEMPLATE_DIR),
        static_folder=str(STATIC_DIR),
    )
    if config_class:
        app.config.from_object(config_class)
    else:
        app.config.from_object(Config)

    if database:
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{database}"

    if "sqlite" in app.config.get("SQLALCHEMY_DATABASE_URI", ""):
        from sqlalchemy.pool import NullPool

        engine_opts = dict(app.config.get("SQLALCHEMY_ENGINE_OPTIONS") or {})
        engine_opts["poolclass"] = NullPool
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = engine_opts

    # Ensure uploads directory exists
    upload_path = (
        app.config.get("UPLOAD_STORAGE_PATH")
        or app.config.get("UPLOAD_FOLDER")
        or str(ROOT / "instance/uploads")
    )
    upload_dir = Path(upload_path).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    app.register_blueprint(api_bp)

    is_cli_db_command = any("db" in str(arg) for arg in sys.argv)
    is_production = app.config.get("IS_PRODUCTION", False)
    if is_production:
        Config.validate_production_config(app.config)

    if not is_cli_db_command and not is_production:
        with app.app_context():
            db.create_all()

    if not is_cli_db_command:
        with app.app_context():
            # Only seed initial demo data if tables exist (e.g. after migration or in testing)
            inspector = inspect(db.engine)
            if inspector.has_table("devices") and inspector.has_table("metadata"):
                should_seed = False if is_production else app.config.get("SEED_DEMO_DATA", True)
                if should_seed and not Metadata.query.filter_by(key="seeded").first():
                    for record in DEMO["devices"]:
                        dev = Device(
                            id=record["id"],
                            name=record["name"],
                            type=record["type"],
                            brand=record["brand"],
                            model=record["model"],
                            status=record["status"],
                            date=record["date"],
                            condition=record["condition"],
                            year=str(record["year"]),
                            weight=str(record["weight"]),
                        )
                        db.session.add(dev)

                    for record in DEMO["transfers"]:
                        status = record["status"]
                        http_status = {
                            "Completed": "200 OK",
                            "Processing": "202 Accepted",
                            "Failed": "503 Service Unavailable",
                            "Pending": "Not sent — awaiting dispatch",
                        }.get(status, "200 OK")
                        response_body = {
                            "Completed": "Payload received and validated successfully.",
                            "Processing": "Payload accepted. Partner processing is in progress.",
                            "Failed": "Partner service unavailable. Retry this transfer.",
                            "Pending": "Transfer queued. No HTTP response received yet.",
                        }.get(status, "Payload received.")
                        endpoint = (
                            "POST /api/v1/refurbishment/devices"
                            if record["sender"] == "Collection"
                            else "POST /api/v1/collection/assessments"
                        )
                        trf = Transfer(
                            id=record["id"],
                            file=record["file"],
                            sender=record["sender"],
                            receiver=record["receiver"],
                            status=status,
                            time=record["time"],
                            http_status=http_status,
                            response_body=response_body,
                            endpoint=endpoint,
                        )
                        db.session.add(trf)

                    asmt_127 = AssessmentResult(
                        device_id="EW-2024-00127",
                        repairability_score=86,
                        circular_recovery_score=86,
                        repair_cost=7100.0,
                        component_value=12100.0,
                        recommendation="REFURBISH",
                        co2_avoided_kg=18.0,
                        assessed_by="GreenTech Refurbishment Partner",
                        notes="Screen cracked, logic board fully operational.",
                        factors=json.dumps([
                            "Device powers on and passes diagnostic tests",
                            "Standard modular display replacement available",
                            "Repair cost substantially below component yield",
                            "High secondary market demand for refurbished unit"
                        ])
                    )
                    db.session.add(asmt_127)

                    asmt_126 = AssessmentResult(
                        device_id="EW-2024-00126",
                        repairability_score=42,
                        circular_recovery_score=68,
                        repair_cost=4500.0,
                        component_value=6200.0,
                        recommendation="PARTS_RECOVERY",
                        co2_avoided_kg=12.0,
                        assessed_by="GreenTech Refurbishment Partner",
                        notes="Defective LCD panel. Power supply board and stand reusable.",
                        factors=json.dumps([
                            "Power supply circuitry intact and tested",
                            "Display panel unviable for economical refurbishment",
                            "Harvestable logic modules for parts pipeline",
                            "Toxic substance diversion guaranteed"
                        ])
                    )
                    db.session.add(asmt_126)

                    db.session.add(Metadata(key="seeded", value="true"))
                    db.session.commit()

    def records(kind):
        """Retrieve persisted records ordered by creation date."""
        if kind == "devices":
            devs = Device.query.order_by(Device.created_at.desc()).all()
            return [d.to_dict() for d in devs]
        elif kind == "transfers":
            trfs = Transfer.query.order_by(Transfer.timestamp.desc()).all()
            return [t.to_dict() for t in trfs]
        return []

    def find_device(device_id):
        return Device.query.filter_by(id=device_id).first()

    def csrf():
        return session.setdefault("csrf", secrets.token_hex(24))

    @app.context_processor
    def demonstration_dates():
        now = datetime.now()

        def demo_date(days=0, full=True):
            return (now - timedelta(days=days)).strftime(
                "%b %d, %Y" if full else "%b %d"
            )

        def demo_time(minutes=0):
            stamp = now - timedelta(minutes=minutes)
            day = "Today" if stamp.date() == now.date() else "Yesterday"
            return f"{day}, {stamp:%I:%M %p}"

        return dict(
            demo_date=demo_date,
            demo_time=demo_time,
            demo_month=now.strftime("%B %Y"),
            current_year=now.year,
        )

    @app.before_request
    def protect_forms():
        if request.path.startswith("/api/"):
            return None
        if request.method == "POST" and not secrets.compare_digest(
            request.form.get("csrf", ""), csrf()
        ):
            abort(400, "Invalid form token. Reload the page and try again.")

    @app.errorhandler(413)
    @app.errorhandler(RequestEntityTooLarge)
    def handle_request_entity_too_large(error):
        if request.path.startswith("/api/"):
            return jsonify({
                "error": "Payload Too Large",
                "message": "Upload is too large. Each file can be up to 5 MB."
            }), 413
        flash("Upload is too large. Each file can be up to 5 MB.")
        return redirect("/register")

    def render(page, device=None):
        template = page
        period = request.args.get("period")
        metrics = get_platform_metrics(period)

        if page == "dashboard" and period == "Last 7 days":
            template = "dashboard-week"
        if page == "detail" and device and device["status"] != "Pending assessment":
            template = "detail-processing"
        if page == "assessment" and device:
            asmt = AssessmentResult.query.filter_by(device_id=device["id"]).first()
            if not asmt:
                template = "assessment-pending"
            elif request.args.get("partner") == "1":
                template = "assessment-partner"
            elif asmt.recommendation in ("PARTS_RECOVERY", "PARTS RECOVERY", "PARTS"):
                template = "assessment-parts"
            else:
                template = "assessment"

        content = render_template(template + ".html")
        document = soup(render_template("layout.html", page_content=Markup(content)))
        main = document.select_one("main")
        devices, transfers = records("devices"), records("transfers")

        # Update sidebar device count from real database metrics
        nav_count = document.select_one("nav a[href='/devices'] span")
        if nav_count:
            nav_count.string = str(metrics["total_devices"])

        # Update notification bell with real database events
        notif_btn = document.select_one("button[aria-label='Notifications']")
        if notif_btn:
            notif_items = []
            recent_trfs = Transfer.query.order_by(Transfer.timestamp.desc()).limit(3).all()
            for t in recent_trfs:
                notif_items.append({
                    "title": f"Transfer {t.id} ({t.status})",
                    "detail": f"{t.sender} → {t.receiver}: {t.file}",
                })
            recent_asmts = AssessmentResult.query.order_by(AssessmentResult.assessed_at.desc()).limit(2).all()
            for a in recent_asmts:
                notif_items.append({
                    "title": f"Assessment received for {a.device_id}",
                    "detail": f"Outcome: {a.recommendation} (Score: {a.circular_recovery_score}/100)",
                })
            notif_btn["data-notifications"] = json.dumps(notif_items)

        active_path = {
            "detail": "/devices",
            "assessment": "/devices",
            "dashboard": "/",
        }.get(page, "/" + page)
        for anchor in document.select("nav a"):
            anchor["class"] = (
                "flex items-center gap-3 rounded-lg px-3 py-3 text-[12px] transition "
                + (
                    "bg-[#2b5e47] text-white"
                    if anchor["href"] == active_path
                    else "text-[#b5cabd] hover:bg-[#204d38]"
                )
            ).split()

        if page in ("devices", "dashboard"):
            tbody = main.select_one("table tbody")
            if tbody and tbody.select_one("tr"):
                prototype = deepcopy(tbody.select_one("tr"))
                tbody.clear()
                query = request.args.get("q", "").lower()
                matching = [
                    d
                    for d in devices
                    if query in f"{d['name']} {d['id']} {d['type']}".lower()
                ]
                for d in (matching[:4] if page == "dashboard" else matching):
                    row = deepcopy(prototype)
                    orig_name = row.select_one("div.font-medium")
                    if orig_name:
                        type_div = orig_name.select_one("div")
                        if type_div:
                            type_div.string = d["type"]
                        for text in list(orig_name.find_all(string=True)):
                            if type_div and text in type_div.strings:
                                continue
                            text.replace_with(d["name"])
                            break

                    # Device ID
                    cells = row.select("td")
                    if len(cells) > 1:
                        cells[1].string = d["id"]
                    if len(cells) > 3:
                        cells[3].string = d.get("date", "")

                    badge_el = row.select_one("td:nth-child(3) > span")
                    if badge_el:
                        badge(badge_el, d["status"])
                    icon_box = row.select_one("div.size-9")
                    svg_el = row.select_one("svg")
                    if d.get("image"):
                        img_el = document.new_tag("img", src=d["image"], alt=d["name"])
                        img_el["class"] = "size-full rounded-lg object-cover".split()
                        if icon_box:
                            icon_box["class"] = icon_box.get("class", []) + ["overflow-hidden"]
                        if svg_el:
                            svg_el.replace_with(img_el)
                        elif icon_box:
                            icon_box.append(img_el)
                    elif svg_el:
                        icon(svg_el, d["type"])
                    row["data-href"] = "/devices/" + d["id"]
                    tbody.append(row)

                if not matching:
                    empty = soup(
                        '<p class="p-8 text-center text-sm text-[#839087]">No devices found.</p>'
                    ).p
                    if tbody.parent and tbody.parent.parent:
                        tbody.parent.parent.append(empty)

            if page == "devices":
                field = main.select_one("input")
                if field:
                    field["value"] = request.args.get("q", "")
                    field["data-device-search"] = ""
            else:
                # Update top metric cards with REAL database counts
                metric_labels = {
                    "Total E-Waste Items": str(metrics["total_devices"]),
                    "Pending Assessment": str(metrics["pending_count"]),
                    "Refurbishment Candidates": str(metrics["refurbishment_candidates"]),
                    "Parts Recovery": str(metrics["parts_recovery"]),
                    "Recycling": str(metrics["completed_count"]),
                    "Estimated Recovery Value": f"₹{metrics['total_recovery_value']:,.0f}",
                }
                for p in main.find_all("p"):
                    text = p.get_text(strip=True)
                    if text in metric_labels:
                        val_node = p.find_next_sibling("p")
                        if val_node:
                            val_node.string = metric_labels[text]

                # Update Date range select options
                date_select = main.select_one('select[aria-label="Date range"]')
                if date_select:
                    current_period = period or "Last 30 days"
                    date_select.clear()
                    for opt_val in ["Last 30 days", "Last 7 days", "All time"]:
                        opt = document.new_tag("option")
                        opt.string = opt_val
                        if opt_val == current_period:
                            opt["selected"] = ""
                        date_select.append(opt)

                # Update Category Distribution section
                cat_section = None
                for sec in main.find_all("section"):
                    h2 = sec.find("h2")
                    if h2 and "category distribution" in h2.get_text().lower():
                        cat_section = sec
                        break

                if cat_section:
                    # Center number
                    center_num = cat_section.select_one("span.text-\\[29px\\]")
                    if center_num:
                        center_num.string = str(metrics["total_devices"])

                    # Dynamic conic-gradient
                    p_lap = metrics["category_percentages"].get("Laptop", 0.0)
                    p_phone = metrics["category_percentages"].get("Smartphone", 0.0)
                    p_mon = metrics["category_percentages"].get("Monitor", 0.0)
                    s1 = p_lap
                    s2 = s1 + p_phone
                    s3 = s2 + p_mon

                    donut = cat_section.select_one("div.size-\\[156px\\]")
                    if donut:
                        if metrics["total_devices"] > 0:
                            grad = (
                                f"conic-gradient(#2c6345 0% {s1:.1f}%, #85ad96 {s1:.1f}% {s2:.1f}%, "
                                f"#c0d5b0 {s2:.1f}% {s3:.1f}%, #e5ece0 {s3:.1f}% 100%)"
                            )
                        else:
                            grad = "conic-gradient(#e5ece0 0% 100%)"
                        donut["style"] = f"background: {grad};"

                    # Update category rows
                    cats = {
                        "Laptops": (metrics["category_counts"].get("Laptop", 0), p_lap),
                        "Smartphones": (metrics["category_counts"].get("Smartphone", 0), p_phone),
                        "Monitors": (metrics["category_counts"].get("Monitor", 0), p_mon),
                        "Others": (
                            metrics["category_counts"].get("Tablet", 0) + metrics["category_counts"].get("Other", 0),
                            metrics["category_percentages"].get("Tablet", 0.0) + metrics["category_percentages"].get("Other", 0.0)
                        ),
                    }
                    for div in cat_section.select("div.space-y-4 > div"):
                        name_span = div.select("span")[1] if len(div.select("span")) > 1 else None
                        if name_span and name_span.get_text(strip=True) in cats:
                            c_count, c_pct = cats[name_span.get_text(strip=True)]
                            count_span = div.select_one("span.font-semibold")
                            if count_span:
                                count_span.string = str(c_count)
                            pct_span = div.select_one("span.w-9")
                            if pct_span:
                                pct_span.string = f"{c_pct:.1f}%"

                # Update Small Actions / Measurable Impact section
                for p in main.find_all("p"):
                    pt = p.get_text(strip=True)
                    if pt == "E-waste diverted":
                        div = p.find_previous_sibling("div")
                        if div:
                            w = metrics["total_weight_kg"]
                            text_val = f"{w:,.1f} kg" if w < 1000 else f"{w/1000:,.1f} tonnes"
                            for child in list(div.find_all(string=True)):
                                child.extract()
                            div.append(text_val)
                    elif pt == "CO₂ emissions avoided":
                        div = p.find_previous_sibling("div")
                        if div:
                            c = metrics["total_co2_kg"]
                            text_val = f"{c:,.1f} kg" if c < 1000 else f"{c/1000:,.1f} tonnes"
                            for child in list(div.find_all(string=True)):
                                child.extract()
                            div.append(text_val)
                    elif pt == "Circular recovery rate":
                        div = p.find_previous_sibling("div")
                        if div:
                            rate_val = f"{metrics['circular_recovery_rate']:.1f}%"
                            for child in list(div.find_all(string=True)):
                                child.extract()
                            div.append(rate_val)

                # Update Circular recovery pathways section
                for p in main.find_all("p"):
                    pt = p.get_text(strip=True)
                    if "devices" in pt and "128" in pt:
                        p.string = f"{metrics['total_devices']} devices"
                    elif "assessed" in pt and "pending" in pt:
                        p.string = f"{metrics['assessed_count']} assessed · {metrics['pending_count']} pending"

                # Update pathway counts: Refurbishment, Parts Recovery, Recycling
                for item in main.select("div.space-y-1\\.5 > div"):
                    name_span = item.select_one("span:first-child")
                    val_span = item.select_one("span.font-semibold")
                    if name_span and val_span:
                        label = name_span.get_text(strip=True)
                        if label == "Refurbishment":
                            val_span.string = str(metrics["refurbishment_candidates"])
                        elif label == "Parts Recovery":
                            val_span.string = str(metrics["parts_recovery"])
                        elif label == "Recycling":
                            val_span.string = str(metrics["completed_count"])

                # Recent API transfers listing
                title = next(
                    (h for h in main.select("h2") if "recent api transfers" in h.get_text().lower()),
                    None
                )
                if title:
                    parent_sec = title.find_parent("section")
                    if parent_sec:
                        listing = parent_sec.select_one("div.mt-3")
                        if listing:
                            prototypes = [
                                deepcopy(item)
                                for item in listing.find_all("div", recursive=False)
                            ]
                            listing.clear()
                            for index, transfer in enumerate(transfers[:3]):
                                proto_index = min(index, len(prototypes) - 1) if prototypes else None
                                if proto_index is not None:
                                    item = deepcopy(prototypes[proto_index])
                                    paragraphs = item.select("p")
                                    if len(paragraphs) >= 3:
                                        paragraphs[0].string = transfer["file"]
                                        paragraphs[1].clear()
                                        paragraphs[1].append(transfer["sender"])
                                        arrow = document.new_tag("span")
                                        arrow["class"] = ["px-1"]
                                        arrow.string = "→"
                                        paragraphs[1].append(arrow)
                                        paragraphs[1].append(transfer["receiver"])
                                        paragraphs[2].string = transfer.get("time", "")
                                    badge_el = item.select_one("span.inline-flex")
                                    if badge_el:
                                        badge(badge_el, transfer["status"])
                                    listing.append(item)

        if page == "tracking":
            grid = main.select_one("div.grid")
            if grid:
                originals = [
                    deepcopy(card) for card in grid.find_all("button", recursive=False)
                ]
                grid.clear()
                for d in devices:
                    proto_card = deepcopy(originals[0]) if originals else None
                    if proto_card:
                        h2 = proto_card.select_one("h2")
                        if h2:
                            h2.string = d["name"]
                        sub_p = proto_card.select_one("p.text-\\[10px\\]")
                        if sub_p:
                            sub_p.string = d["id"]

                        badge_el = proto_card.select_one("span.inline-flex")
                        if badge_el:
                            badge(badge_el, d["status"])
                        svg_el = proto_card.select_one("svg")
                        if svg_el:
                            icon(svg_el, d["type"], 27)

                        # Stage indicators in tracking card
                        stage = (
                            5 if d["status"] == "Completed"
                            else 0 if d["status"] == "Pending assessment"
                            else 2 if d["status"] == "Processing"
                            else 4
                        )
                        bars = proto_card.select("div.mt-6 > div")
                        for idx, bar in enumerate(bars):
                            bar["class"] = (
                                "h-1.5 flex-1 rounded-full "
                                + ("bg-[#669369]" if idx <= stage else "bg-[#e9eee4]")
                            ).split()

                        link(proto_card, "/devices/" + d["id"])
                        grid.append(proto_card)

        if page in ("detail", "assessment") and device:
            # Replace placeholder device IDs and names
            old_device_id = "EW-2024-00128" if template in ("detail", "assessment-pending") else "EW-2024-00127"
            old_device_name = "Dell Latitude 5420" if template in ("detail", "assessment-pending") else "Samsung Galaxy S21"
            replace_text(main, {old_device_id: device["id"], old_device_name: device["name"]})

            if page == "detail":
                h1 = main.select_one("h1")
                if h1:
                    sub_info = h1.find_next("p")
                    if sub_info:
                        sub_info.string = f"{device['id']} · Registered {device.get('date', '')}"
                values = main.select("dl dd")
                specs = [
                    device["type"],
                    device["brand"],
                    device["model"],
                    f"{max(0, datetime.now().year - int(device['year']))} years",
                    device["condition"],
                    device["weight"] + " kg",
                ]
                for node, value in zip(values, specs):
                    node.string = value

                badge_node = main.select_one("section > span.inline-flex")
                if badge_node:
                    badge(badge_node, device["status"])

                image_container = main.select_one("section div.h-48")
                if image_container:
                    svg_node = image_container.select_one("svg")
                    if device.get("image"):
                        image = document.new_tag(
                            "img", src=device["image"], alt=device["name"]
                        )
                        image["class"] = "size-full rounded-xl object-contain".split()
                        if svg_node:
                            svg_node.replace_with(image)
                        else:
                            image_container.append(image)
                    elif svg_node:
                        icon(svg_node, device["type"], 100)

                # Uploaded files section
                files_sec = main.select("section")[-1]
                if files_sec:
                    files_p = files_sec.select_one("p")
                    if files_p:
                        for text in list(files_p.find_all(string=True)):
                            text.extract()
                        if device.get("document"):
                            files_p.append(device["document"])
                        else:
                            files_p.append("No invoice attached")
                        if device.get("image"):
                            files_p.append(" · Device image uploaded")

                stage = (
                    5 if device["status"] == "Completed"
                    else 0 if device["status"] == "Pending assessment"
                    else 2 if device["status"] == "Processing"
                    else 4
                )
                stages = main.select("section")[1].select("div.mt-7 > div")
                for index, node in enumerate(stages):
                    circle = node.select_one("span")
                    if circle:
                        circle["class"] = (
                            "flex size-8 items-center justify-center rounded-full text-xs "
                            + (
                                "bg-[#e7f1e6] text-[#3c7047]"
                                if index <= stage
                                else "bg-[#f2f4f1] text-[#a6b0a1]"
                            )
                        ).split()
                        circle.clear()
                        circle.append(
                            soup(
                                '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m20 6-11 11-5-5"/></svg>'
                            ).svg
                            if index <= stage
                            else str(index + 1)
                        )
                    line = circle.find_next_sibling("span") if circle else None
                    if line:
                        line["class"] = (
                            "h-8 w-px "
                            + ("bg-[#9bc09d]" if index < stage else "bg-[#e7ece4]")
                        ).split()
                    title = node.select_one("p")
                    if title:
                        title["class"] = (
                            ["font-medium"] if index <= stage else ["text-[#9ba79a]"]
                        )
                        current = title.find_next_sibling("p")
                        if current:
                            current.decompose()
                        if index == stage:
                            title.parent.append(
                                soup(
                                    '<p class="mt-1 text-[10px] text-[#7e9279]">Current stage</p>'
                                ).p
                            )

            if page == "assessment":
                asmt = AssessmentResult.query.filter_by(device_id=device["id"]).first()
                if asmt:
                    replace_text(
                        main,
                        {
                            "86 / 100": f"{asmt.repairability_score} / 100",
                            "₹7,100": f"₹{asmt.repair_cost:,.0f}",
                            "₹12,100": f"₹{asmt.component_value:,.0f}",
                            "18 kg CO₂": f"{asmt.co2_avoided_kg:g} kg CO₂",
                        },
                    )
                    # Update recommendation buttons
                    rec_grid = main.select_one("div.grid.sm\\:grid-cols-4")
                    if rec_grid:
                        rec_norm = asmt.recommendation.upper()
                        target_rec = "REFURBISH" if rec_norm == "REFURBISH" else (
                            "PARTS RECOVERY" if rec_norm in ("PARTS_RECOVERY", "PARTS RECOVERY", "PARTS") else (
                                "RECYCLE" if rec_norm in ("RECYCLE", "SAFE_DISPOSAL", "SAFE DISPOSAL") else "REFURBISH"
                            )
                        )
                        for btn in rec_grid.find_all("div", recursive=False):
                            b_text = btn.get_text(strip=True).upper()
                            if target_rec in b_text:
                                btn["class"] = "rounded-xl border px-4 py-5 text-center text-xs border-[#92b28a] bg-[#edf4e8] font-semibold text-[#4e7b48]".split()
                            else:
                                btn["class"] = "rounded-xl border px-4 py-5 text-center text-xs border-[#e7ece2] text-[#9ba792]".split()

        if page == "transfers":
            tbody = main.select_one("tbody")
            if tbody and tbody.select_one("tr"):
                prototype = deepcopy(tbody.select_one("tr"))
                tbody.clear()
                current_filter = request.args.get("filter", "All transfers")
                for transfer in transfers:
                    if (
                        current_filter != "All transfers"
                        and current_filter != transfer["status"]
                    ):
                        continue
                    row = deepcopy(prototype)
                    cells = row.select("td")
                    values = [
                        transfer["id"],
                        transfer["file"],
                        transfer["sender"] + " Application",
                        transfer["receiver"] + " Application",
                        "JSON",
                        transfer.get("time", ""),
                    ]
                    for cell, val in zip(cells[:6], values):
                        cell.string = val
                    badge_el = row.select_one("td:nth-child(7) span.inline-flex")
                    if badge_el:
                        badge(badge_el, transfer["status"])
                    buttons = row.select("button")
                    if buttons:
                        link(buttons[0], "/transfers?selected=" + transfer["id"])
                        for b in buttons[1:]:
                            b.decompose()
                    if transfer["status"] == "Failed":
                        retry = soup(
                            '<button class="text-[10px] text-[#467454]" type="button">Retry</button>'
                        ).button
                        retry["data-post"] = "/transfers/" + transfer["id"] + "/retry"
                        cells[-1].append(retry)
                    tbody.append(row)

            selected = next(
                (t for t in transfers if t["id"] == request.args.get("selected")), None
            )
            if selected:
                details = soup(
                    render_template("transfer-details.html")
                ).select_one("#transfer-details")
                if details:
                    status = selected["status"]
                    http = selected.get("http_status") or "200 OK"
                    response = selected.get("response_body") or "Payload received."
                    direction = details.select_one("div.mt-4")
                    if direction and len(direction.select("span")) >= 2:
                        direction.select("span")[0].string = selected["sender"] + " Application"
                        direction.select("span")[1].string = selected["receiver"] + " Application"
                    endpoint = selected.get("endpoint") or (
                        "POST /api/v1/refurbishment/devices"
                        if selected["sender"] == "Collection"
                        else "POST /api/v1/collection/assessments"
                    )
                    dl_values = [
                        selected["id"],
                        selected["sender"] + " Application",
                        selected["receiver"] + " Application",
                        endpoint,
                        selected["file"],
                        "JSON · application/json",
                        selected.get("time", ""),
                        http,
                        status,
                        response,
                    ]
                    for node, value in zip(details.select("dd"), dl_values):
                        if node.select_one("span.inline-flex"):
                            badge(node.span, value)
                        else:
                            node.string = value
                    sec = main.select_one("section")
                    if sec:
                        sec.insert_after(details)

            for button in main.select("section > div > button"):
                text = button.get_text(strip=True)
                link(button, "/transfers?filter=" + text)
                button["class"] = (
                    "rounded-lg px-4 py-2 text-xs "
                    + (
                        "bg-[#eaf2e8] font-medium text-[#466c44]"
                        if text == current_filter
                        else "text-[#8b9988]"
                    )
                ).split()

        if page == "register":
            form = main.select_one("form")
            if form:
                form["method"], form["action"], form["enctype"] = (
                    "post",
                    "/register",
                    "multipart/form-data",
                )
                for index, field in enumerate(form.select("input[type=file]")):
                    field["name"] = "image" if index == 0 else "document"
                year = form.select_one("input[name='Purchase Year']")
                if year:
                    year["max"] = str(datetime.now().year)
                    year["data-year"] = ""
                age_input = form.select_one("input[name='Device Age']")
                if age_input:
                    age_input["data-age"] = ""
                    age_input["value"] = f"{datetime.now().year - 2021} years"

        for f in document.select("header form"):
            f["action"], f["method"] = "/devices", "get"
            if f.input:
                f.input["name"] = "q"

        for button in document.select("button"):
            text = button.get_text(" ", strip=True)
            destinations = {
                "Register e-waste": "/register",
                "View all devices": "/devices",
                "← Back to devices": "/devices",
                "View all transfers": "/transfers",
                "Export report": "/download/summary",
                "View assessment": "/assessment/" + (device["id"] if device else ""),
                "Download Report": (
                    "/download/assessment/" + device["id"]
                    if device
                    else "/download/report"
                ),
                "View Partner Assessment": request.path
                + (
                    "?partner=0"
                    if request.args.get("partner") == "1"
                    else "?partner=1"
                ),
            }
            if text in destinations:
                link(button, destinations[text])
            if button.get("aria-label") == "View API transfers":
                link(button, "/transfers")
            if text == "Send to Refurbishment Application" and device:
                button["data-post"] = "/devices/" + device["id"] + "/send"
                if device["status"] in ("Processing", "Refurbishment", "Parts recovery", "Completed"):
                    button["disabled"] = ""
                elif "disabled" in button.attrs:
                    del button["disabled"]
            if button.get("aria-label") == "Close transfer details":
                link(button, "/transfers")
            if text == "Help & support":
                button["data-message"] = "Need help? Contact support@circular.example"
            if text == "Settings":
                button["data-message"] = "Workspace: GreenTech Organization · Production environment"

        if page == "reports":
            reports_list = main.select_one("div.space-y-3")
            if reports_list:
                reports_list.clear()
                assessed_devices = (
                    Device.query.join(AssessmentResult, Device.id == AssessmentResult.device_id)
                    .order_by(AssessmentResult.assessed_at.desc())
                    .all()
                )
                if assessed_devices:
                    for d in assessed_devices:
                        btn = soup(
                            f'<button class="flex w-full items-center gap-3 rounded-lg border border-[#e6ece0] p-4 text-left text-xs">'
                            f'<svg class="lucide lucide-file-text text-[#7e9870]" fill="none" height="19" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" width="19"><path d="M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z"></path><path d="M14 2v5a1 1 0 0 0 1 1h5"></path><path d="M10 9H8"></path><path d="M16 13H8"></path><path d="M16 17H8"></path></svg>'
                            f'<span class="min-w-0 flex-1 break-words">{d.name}<span class="mt-1 block text-[10px] text-[#99a48f]">{d.id} · {d.assessment.recommendation}</span></span>'
                            f'<svg class="lucide lucide-arrow-right" fill="none" height="15" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" width="15"><path d="M5 12h14"></path><path d="m12 5 7 7-7 7"></path></svg>'
                            f'</button>'
                        ).button
                        link(btn, "/assessment/" + d.id)
                        reports_list.append(btn)
                else:
                    empty_msg = soup(
                        '<p class="p-4 text-center text-xs text-[#8c9690]">No completed partner assessments found.</p>'
                    ).p
                    reports_list.append(empty_msg)

        for f in document.select("form[method=post]"):
            field = document.new_tag(
                "input", attrs={"type": "hidden", "name": "csrf", "value": csrf()}
            )
            f.insert(0, field)

        return render_template(
            "base.html", content=Markup(str(document)), csrf=csrf()
        )

    @app.get("/")
    def dashboard():
        return render("dashboard")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "GET":
            return render("register")

        required = [
            "Device Type",
            "Brand",
            "Model",
            "Purchase Year",
            "Condition",
            "Estimated Weight",
        ]
        if any(not request.form.get(key, "").strip() for key in required):
            flash("Please complete all required device information.")
            return redirect("/register")

        try:
            year = int(request.form["Purchase Year"])
            weight = float(request.form["Estimated Weight"])
            if not 1980 <= year <= datetime.now().year or weight < 0:
                raise ValueError
        except ValueError:
            flash("Please enter a valid purchase year and weight.")
            return redirect("/register")

        storage = get_storage_service(app.config)
        uploads = {}
        uploaded_file_records = []
        created_storage_keys = []

        try:
            for key in ("image", "document"):
                upload = request.files.get(key)
                if upload and upload.filename:
                    data = upload.read()
                    is_valid, err_msg = validate_file_content(data, upload.filename, key)
                    if not is_valid:
                        flash(f"Please select supported files. Maximum file size: 5 MB. ({err_msg})")
                        # Clean up any already saved files in this request
                        for sk in created_storage_keys:
                            try:
                                storage.delete(sk)
                            except Exception:
                                pass
                        return redirect("/register")

                    is_private = (key == "document")
                    content_type = get_mime_type(upload.filename)
                    storage_key, access_url = storage.save(
                        data, upload.filename, content_type, is_private=is_private
                    )
                    created_storage_keys.append(storage_key)
                    uploads[key] = access_url

                    uploaded_file_records.append(
                        {
                            "file_type": key,
                            "filename": Path(storage_key).name,
                            "original_filename": Path(upload.filename).name,
                            "storage_key": storage_key,
                            "file_path": access_url,
                            "content_type": content_type,
                            "file_size": len(data),
                            "storage_provider": app.config.get("STORAGE_PROVIDER", "local"),
                            "is_private": is_private,
                        }
                    )

            device_id = f"EW-{datetime.now().year}-{secrets.token_hex(4).upper()}"
            brand = request.form["Brand"].strip()
            model = request.form["Model"].strip()
            device = Device(
                id=device_id,
                name=f"{brand} {model}",
                type=request.form["Device Type"].strip(),
                brand=brand,
                model=model,
                year=str(year),
                weight=str(weight),
                condition=request.form["Condition"].strip(),
                status="Pending assessment",
                date=datetime.now().strftime("%b %d, %Y"),
                damage=request.form.get("Damage Description", "").strip(),
                power=request.form.get("Power Status", "").strip(),
                screen=request.form.get("Screen Condition", "").strip(),
                battery=request.form.get("Battery Condition", "").strip(),
                image=uploads.get("image"),
                document=uploads.get("document"),
            )
            db.session.add(device)

            for f_rec in uploaded_file_records:
                d_file = DeviceFile(
                    device_id=device_id,
                    file_type=f_rec["file_type"],
                    filename=f_rec["filename"],
                    original_filename=f_rec["original_filename"],
                    storage_key=f_rec["storage_key"],
                    file_path=f_rec["file_path"],
                    content_type=f_rec["content_type"],
                    file_size=f_rec["file_size"],
                    storage_provider=f_rec["storage_provider"],
                    is_private=f_rec["is_private"],
                )
                db.session.add(d_file)

            db.session.commit()
            flash("Device registered successfully")
            return redirect("/devices/" + device_id)

        except Exception as e:
            db.session.rollback()
            # Clean up orphaned files on failure
            for sk in created_storage_keys:
                try:
                    storage.delete(sk)
                except Exception:
                    pass
            flash(f"Registration failed: {str(e)}")
            return redirect("/register")

    @app.get("/devices")
    def devices_page():
        return render("devices")

    @app.get("/devices/<device_id>")
    def detail(device_id):
        device = find_device(device_id)
        if not device:
            abort(404)
        return render("detail", device)

    @app.post("/devices/<device_id>/send")
    def send(device_id):
        device = find_device(device_id)
        if not device:
            abort(404)

        # Validate lifecycle status allows dispatch
        if device.status in ("Processing", "Refurbishment", "Parts recovery", "Completed"):
            flash("Device has already completed or is currently undergoing assessment.")
            return redirect("/devices/" + device_id)

        transfer_id = f"TRF-{secrets.token_hex(4).upper()}"
        file_name = f"{device.id}.json"
        now = datetime.now(timezone.utc)

        files_payload = []
        for f in device.files:
            file_url = f"/uploads/{f.storage_key}" if not f.is_private else f"/api/v1/files/{f.storage_key}"
            files_payload.append({
                "file_type": f.file_type,
                "original_filename": f.original_filename,
                "storage_key": f.storage_key,
                "content_type": f.content_type,
                "file_size": f.file_size,
                "url": file_url,
            })

        payload = {
            "schema_version": "1.0.0",
            "transfer_id": transfer_id,
            "device_id": device.id,
            "device": {
                "id": device.id,
                "name": device.name,
                "type": device.type,
                "brand": device.brand,
                "model": device.model,
                "year": device.year,
                "weight": device.weight,
                "condition": device.condition,
                "damage": device.damage or "",
                "power": device.power or "",
                "screen": device.screen or "",
                "battery": device.battery or "",
                "registered_date": device.date,
            },
            "files": files_payload,
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
        }

        partner_url = app.config.get("PARTNER_API_URL")
        partner_key = app.config.get("PARTNER_API_KEY")

        if partner_url:
            if not partner_key:
                # Requirement: If PARTNER_API_URL exists but PARTNER_API_KEY is missing,
                # DO NOT send the request. Mark transfer failed. Do not move device to Processing.
                target_endpoint = f"{partner_url}/api/v1/refurbishment/devices"
                http_status = "401 Unauthorized"
                response_body = "Missing PARTNER_API_KEY for configured PARTNER_API_URL. Transfer aborted."
                transfer_status = "Failed"
                flash("Partner transfer failed: PARTNER_API_KEY is not configured.")
            else:
                target_endpoint = f"{partner_url}/api/v1/refurbishment/devices"
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {partner_key}",
                    "X-API-Key": partner_key,
                    "Idempotency-Key": transfer_id,
                }
                try:
                    resp = requests.post(
                        target_endpoint, json=payload, headers=headers, timeout=10
                    )
                    http_status = f"{resp.status_code} {resp.reason}"
                    response_body = resp.text[:1000]
                    if resp.status_code in (200, 201):
                        transfer_status = "Completed"
                        device.transition_to("Processing")
                        flash("Transfer successfully dispatched to partner application.")
                    elif resp.status_code == 202:
                        transfer_status = "Processing"
                        device.transition_to("Processing")
                        flash("Transfer accepted by partner. Assessment in progress.")
                    else:
                        transfer_status = "Failed"
                        flash(f"Partner transfer failed with status {resp.status_code}.")
                except requests.RequestException as e:
                    http_status = "503 Service Unavailable"
                    response_body = f"Connection error: {str(e)}"
                    transfer_status = "Failed"
                    flash("Network error contacting partner. Transfer recorded as Failed.")
        else:
            target_endpoint = "POST /api/v1/refurbishment/devices"
            http_status = "Not sent — awaiting dispatch"
            response_body = "Demo transfer queued. No external API is connected."
            transfer_status = "Pending"
            # Phase 12: Device status MUST remain Pending assessment when PARTNER_API_URL is empty
            flash("Demo transfer queued. No external API is connected.")

        transfer = Transfer(
            id=transfer_id,
            device_id=device.id,
            file=file_name,
            sender="Collection",
            receiver="Refurbishment",
            status=transfer_status,
            time=f"Today, {now.strftime('%I:%M %p')}",
            timestamp=now,
            http_status=http_status,
            response_body=response_body,
            endpoint=target_endpoint,
            payload=json.dumps(payload),
            idempotency_key=transfer_id,
        )
        db.session.add(transfer)
        db.session.commit()

        return redirect("/devices/" + device_id)

    @app.post("/transfers/<transfer_id>/retry")
    def retry(transfer_id):
        transfer = Transfer.query.filter_by(id=transfer_id).first()
        if not transfer:
            abort(404)

        device = find_device(transfer.device_id) if transfer.device_id else None

        if (transfer.retry_count or 0) >= 5:
            flash("Maximum retry attempts (5) reached for this transfer.")
            return redirect("/transfers")

        partner_url = app.config.get("PARTNER_API_URL")
        partner_key = app.config.get("PARTNER_API_KEY")

        if partner_url and transfer.payload:
            if not partner_key:
                transfer.status = "Failed"
                transfer.http_status = "401 Unauthorized"
                transfer.response_body = "Retry aborted: missing PARTNER_API_KEY."
                transfer.retry_count = (transfer.retry_count or 0) + 1
                transfer.timestamp = datetime.now(timezone.utc)
                flash("Retry failed: PARTNER_API_KEY is not configured.")
            else:
                target_endpoint = (
                    transfer.endpoint or f"{partner_url}/api/v1/refurbishment/devices"
                )
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {partner_key}",
                    "X-API-Key": partner_key,
                    "Idempotency-Key": transfer.idempotency_key or transfer.id,
                }
                try:
                    payload = json.loads(transfer.payload)
                    resp = requests.post(
                        target_endpoint, json=payload, headers=headers, timeout=10
                    )
                    transfer.http_status = f"{resp.status_code} {resp.reason}"
                    transfer.response_body = resp.text[:1000]
                    transfer.retry_count = (transfer.retry_count or 0) + 1
                    transfer.timestamp = datetime.now(timezone.utc)
                    if resp.status_code in (200, 201):
                        transfer.status = "Completed"
                        if device:
                            device.transition_to("Processing")
                        flash("Transfer retry succeeded.")
                    elif resp.status_code == 202:
                        transfer.status = "Processing"
                        if device:
                            device.transition_to("Processing")
                        flash("Transfer retry accepted by partner.")
                    else:
                        transfer.status = "Failed"
                        flash(f"Transfer retry failed with status {resp.status_code}.")
                except requests.RequestException as e:
                    transfer.http_status = "503 Service Unavailable"
                    transfer.response_body = f"Retry connection error: {str(e)}"
                    transfer.status = "Failed"
                    transfer.retry_count = (transfer.retry_count or 0) + 1
                    transfer.timestamp = datetime.now(timezone.utc)
                    flash("Retry failed due to network error.")
        else:
            transfer.status = "Pending"
            transfer.http_status = "Not sent — awaiting dispatch"
            transfer.response_body = (
                "Transfer queued for retry. No HTTP response received yet."
            )
            flash("Transfer queued for retry.")

        db.session.commit()
        return redirect("/transfers")

    @app.get("/assessment/<device_id>")
    def assessment(device_id):
        device = find_device(device_id)
        if not device:
            abort(404)
        return render("assessment", device)

    @app.get("/tracking")
    def tracking():
        return render("tracking")

    @app.get("/transfers")
    def transfers_page():
        return render("transfers")

    @app.get("/reports")
    def reports():
        return render("reports")

    @app.get("/uploads/<name>")
    def upload(name):
        safe_name = Path(name).name
        # Reject path traversal
        if name != safe_name or ".." in name or "/" in name or "\\" in name:
            abort(404)
        if not all(c.isalnum() or c in "-_." for c in safe_name):
            abort(404)

        d_file = DeviceFile.query.filter_by(storage_key=safe_name).first()
        if not d_file:
            abort(404)
        if d_file.is_private or d_file.file_type != "image":
            abort(403, "Private document. Authentication required via API.")

        storage = get_storage_service(app.config)
        try:
            file_bytes, content_type = storage.retrieve(safe_name)
            return send_file(
                io.BytesIO(file_bytes),
                mimetype=content_type or d_file.content_type or "application/octet-stream",
                as_attachment=False,
                download_name=d_file.original_filename or safe_name,
            )
        except Exception:
            abort(404)

    @app.get("/download/<kind>")
    def download(kind):
        if kind not in ("summary", "report"):
            abort(404)

        metrics = get_platform_metrics()

        if kind == "summary" and request.args.get("format") == "pdf":
            devs = [d.to_dict() for d in Device.query.all()]
            pdf_bytes = generate_summary_pdf(metrics, devs)
            return send_file(
                io.BytesIO(pdf_bytes),
                mimetype="application/pdf",
                as_attachment=True,
                download_name="circular-summary.pdf",
            )

        output = io.StringIO()
        writer = csv.writer(output)
        if kind == "summary":
            writer.writerow(["Circular E-Waste Recovery Summary Report — Live Portfolio"])
            writer.writerows(
                [
                    ["Metric", "Value"],
                    ["Total items registered", metrics["total_devices"]],
                    ["Refurbishment candidates", metrics["refurbishment_candidates"]],
                    ["Parts recovery items", metrics["parts_recovery"]],
                    ["Completed / recycled", metrics["completed_count"]],
                    ["Pending assessment", metrics["pending_count"]],
                    ["Total recovery value (INR)", metrics["total_recovery_value"]],
                    ["Total CO2 emissions avoided (kg)", metrics["total_co2_kg"]],
                    ["Circular recovery rate (%)", metrics["circular_recovery_rate"]],
                ]
            )
        else:
            writer.writerow(["Category", "Devices Count", "Recovery Value (INR)", "Live Portfolio"])
            for cat, count in metrics["category_counts"].items():
                val = metrics["category_values"].get(cat, 0.0)
                writer.writerow([cat, count, f"{val:,.0f}"])

        return send_file(
            io.BytesIO(output.getvalue().encode("utf-8-sig")),
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"circular-{kind}.csv",
        )

    @app.get("/download/assessment/<device_id>")
    def download_assessment(device_id):
        device = find_device(device_id)
        if not device:
            abort(404)

        asmt = AssessmentResult.query.filter_by(device_id=device_id).first()
        if not asmt:
            # Assessment is not yet available; do not fabricate fake values
            abort(409, "Assessment is not available yet for this device.")

        asmt_dict = asmt.to_dict()

        req_format = request.args.get("format", "").lower()
        if req_format == "txt":
            report = (
                f"Verified Technical Assessment Report\n"
                f"Device: {device.name} ({device.id})\n"
                f"Repairability Score: {asmt.repairability_score}/100\n"
                f"Circular Recovery Score: {asmt.circular_recovery_score}/100\n"
                f"Repair Cost: ₹{asmt.repair_cost:,.0f}\n"
                f"Component Recovery Value: ₹{asmt.component_value:,.0f}\n"
                f"Recommendation: {asmt.recommendation}\n"
                f"Environmental Benefit: {asmt.co2_avoided_kg:g} kg CO2 avoided\n"
                f"Assessed By: {asmt.assessed_by}\n"
            )
            return send_file(
                io.BytesIO(report.encode("utf-8")),
                mimetype="text/plain; charset=utf-8",
                as_attachment=True,
                download_name=f"assessment-{device_id}.txt",
            )

        pdf_bytes = generate_assessment_pdf(device, asmt_dict)
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"assessment-{device_id}.pdf",
        )

    return app


if __name__ == "__main__":
    create_app().run(
        host="127.0.0.1", port=int(os.environ.get("FLASK_PORT", "5000"))
    )
