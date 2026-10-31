"""REST API blueprint for Circular E-Waste Recovery platform.
Provides authenticated endpoints for devices, transfers, partner assessments, and secure files.
"""

import io
import json
import math
import secrets
from datetime import datetime, timezone
from typing import Optional, Tuple
import requests
from flask import Blueprint, current_app, jsonify, request, send_file, session
from models import AssessmentResult, Device, DeviceFile, Transfer, db
from storage import get_storage_service

api_bp = Blueprint("api", __name__, url_prefix="/api")

CANONICAL_RECOMMENDATIONS = {
    "REFURBISH",
    "PARTS_RECOVERY",
    "RECYCLE",
    "SAFE_DISPOSAL",
    "REJECT",
}

RECOMMENDATION_ALIASES = {
    "REFURBISH": "REFURBISH",
    "PARTS_RECOVERY": "PARTS_RECOVERY",
    "PARTS RECOVERY": "PARTS_RECOVERY",
    "PARTS": "PARTS_RECOVERY",
    "RECYCLE": "RECYCLE",
    "SAFE_DISPOSAL": "SAFE_DISPOSAL",
    "SAFE DISPOSAL": "SAFE_DISPOSAL",
    "DISPOSAL": "SAFE_DISPOSAL",
    "REJECT": "REJECT",
}


def verify_api_key() -> bool:
    """Verify inbound API key against configuration.
    
    Strictly checks headers only; credentials from query parameters are prohibited.
    """
    expected_key = current_app.config.get("CIRCULAR_API_KEY")
    is_prod = current_app.config.get("IS_PRODUCTION", False)

    # In production or development, missing expected_key must fail
    if not expected_key:
        return False

    auth_header = request.headers.get("Authorization", "")
    api_key_header = request.headers.get("X-API-Key", "")

    provided_key = None
    if api_key_header:
        provided_key = api_key_header.strip()
    elif auth_header.startswith("Bearer "):
        provided_key = auth_header.split("Bearer ", 1)[1].strip()

    # Reject query parameter credentials and empty keys
    if not provided_key:
        return False

    return bool(secrets.compare_digest(provided_key, expected_key))


@api_bp.before_request
def authenticate():
    """Global authentication guard for API endpoints."""
    # Health check and notifications are public
    if request.path in ("/api/health", "/api/v1/notifications"):
        return None

    if not verify_api_key():
        return jsonify({
            "error": "Unauthorized",
            "message": "Invalid or missing API key. Provide via 'X-API-Key' or 'Authorization: Bearer <key>'."
        }), 401


@api_bp.get("/health")
def health_check():
    """Service health and database connectivity probe (Public)."""
    try:
        db.session.execute(db.text("SELECT 1"))
        db_status = "connected"
    except Exception as e:
        current_app.logger.error("Health check database probe error: %s", type(e).__name__)
        db_status = "unavailable"

    is_healthy = db_status == "connected"
    return jsonify({
        "status": "healthy" if is_healthy else "degraded",
        "database": db_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "Circular E-Waste Recovery Platform API",
        "version": "1.0.0"
    }), 200 if is_healthy else 503


@api_bp.get("/v1/devices")
def list_devices():
    """List all registered devices with optional status and query filtering."""
    query = Device.query

    status_filter = request.args.get("status")
    if status_filter:
        query = query.filter(Device.status == status_filter)

    search = request.args.get("q")
    if search:
        pattern = f"%{search}%"
        query = query.filter(
            (Device.name.ilike(pattern)) |
            (Device.id.ilike(pattern)) |
            (Device.brand.ilike(pattern)) |
            (Device.model.ilike(pattern)) |
            (Device.type.ilike(pattern))
        )

    devices = query.order_by(Device.created_at.desc()).all()
    return jsonify({
        "count": len(devices),
        "devices": [d.to_dict() for d in devices]
    }), 200


@api_bp.get("/v1/devices/<device_id>")
def get_device(device_id):
    """Retrieve details for a single device, including assessment and transfers."""
    device = Device.query.filter_by(id=device_id).first()
    if not device:
        return jsonify({"error": "Device not found", "device_id": device_id}), 404

    data = device.to_dict()
    data["assessment"] = device.assessment.to_dict() if device.assessment else None
    data["transfers"] = [t.to_dict() for t in device.transfers]
    data["files"] = [f.to_dict() for f in device.files]
    return jsonify(data), 200


def dispatch_device_to_partner(device: Device, transfer_id: Optional[str] = None) -> Tuple[Transfer, bool, str]:
    """Dispatch registered device metadata to partner Refurbishment Intelligence service.

    Returns:
        (transfer_record, is_success, message)
    """
    if not transfer_id:
        transfer_id = f"TRF-{secrets.token_hex(4).upper()}"

    payload = device.to_partner_payload(transfer_id)
    partner_url = (current_app.config.get("PARTNER_API_URL") or "").rstrip("/")
    partner_key = current_app.config.get("PARTNER_API_KEY") or ""
    now = datetime.now(timezone.utc)
    file_name = f"{device.id}.json"

    if partner_url:
        target_endpoint = f"{partner_url}/api/v1/refurbishment/devices"
        if not partner_key:
            http_status = "401 Unauthorized"
            response_body = "Missing PARTNER_API_KEY for configured PARTNER_API_URL. Transfer aborted."
            transfer_status = "Failed"
            success = False
            message = "Partner transfer failed: PARTNER_API_KEY is not configured."
        else:
            headers = {
                "Content-Type": "application/json",
                "X-API-Key": partner_key,
                "Authorization": f"Bearer {partner_key}",
                "Idempotency-Key": transfer_id,
            }
            try:
                resp = requests.post(
                    target_endpoint, json=payload, headers=headers, timeout=10
                )
                http_status = f"{resp.status_code} {resp.reason}"
                response_body = resp.text[:2000]

                if resp.status_code in (200, 201):
                    transfer_status = "Completed"
                    device.transition_to("Processing")
                    success = True
                    message = "Transfer successfully dispatched to partner application."
                elif resp.status_code == 202:
                    transfer_status = "Processing"
                    device.transition_to("Processing")
                    success = True
                    message = "Transfer accepted by partner. Assessment in progress."
                elif resp.status_code == 409:
                    transfer_status = "Failed"
                    success = False
                    message = "Partner reported duplicate transfer conflict (409)."
                else:
                    transfer_status = "Failed"
                    success = False
                    message = f"Partner transfer failed with status {resp.status_code}."
            except requests.Timeout as e:
                http_status = "504 Gateway Timeout"
                response_body = f"Network timeout contacting partner: {str(e)}"
                transfer_status = "Failed"
                success = False
                message = "Network timeout contacting partner. Transfer recorded as Failed."
            except requests.RequestException as e:
                http_status = "503 Service Unavailable"
                response_body = f"Connection error: {str(e)}"
                transfer_status = "Failed"
                success = False
                message = "Network error contacting partner. Transfer recorded as Failed."
    else:
        target_endpoint = "POST /api/v1/refurbishment/devices"
        http_status = "Not sent — awaiting dispatch"
        response_body = "Demo transfer queued. No external API is connected."
        transfer_status = "Pending"
        success = False
        message = "Demo transfer queued. No external API is connected."

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
    return transfer, success, message


@api_bp.post("/v1/devices/<device_id>/send")
def api_send_device(device_id):
    """Dispatch device to partner Refurbishment Intelligence service via REST API."""
    device = Device.query.filter_by(id=device_id).first()
    if not device:
        return jsonify({"error": "Not Found", "message": f"Device {device_id} not found"}), 404

    if device.status in ("Processing", "Refurbishment", "Parts recovery", "Completed"):
        return jsonify({
            "error": "Conflict",
            "message": f"Device {device_id} is in status '{device.status}' and cannot be dispatched."
        }), 409

    transfer, success, message = dispatch_device_to_partner(device)
    http_code = 200 if success else (
        504 if "timeout" in (transfer.http_status or "").lower()
        else (401 if "401" in (transfer.http_status or "")
        else (202 if transfer.status == "Pending"
        else 502))
    )
    return jsonify({
        "status": "success" if success else "failed",
        "message": message,
        "transfer": transfer.to_dict(),
        "device_status": device.status
    }), http_code


@api_bp.get("/v1/transfers")
def list_transfers():
    """List all transfers with optional status filter."""
    query = Transfer.query
    status_filter = request.args.get("status")
    if status_filter:
        query = query.filter(Transfer.status == status_filter)
    transfers = query.order_by(Transfer.timestamp.desc()).all()
    return jsonify({
        "count": len(transfers),
        "transfers": [t.to_dict() for t in transfers]
    }), 200


@api_bp.get("/v1/transfers/<transfer_id>")
def get_transfer(transfer_id):
    """Retrieve details for a specific transfer."""
    transfer = Transfer.query.filter_by(id=transfer_id).first()
    if not transfer:
        return jsonify({"error": "Transfer not found", "transfer_id": transfer_id}), 404
    return jsonify(transfer.to_dict()), 200


@api_bp.post("/v1/assessment-results")
@api_bp.post("/v1/collection/assessments")
def receive_assessment():
    """Inbound webhook / API endpoint for partners to post assessment results.
    
    Enforces strict field validation, non-negative values, range checks,
    transactional persistence, and idempotency handling.
    """
    if not request.is_json:
        return jsonify({"error": "Bad Request", "message": "JSON body required"}), 400

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Bad Request", "message": "Empty or malformed JSON payload"}), 400

    # 1. Check idempotency key (header or payload)
    idempotency_key = (
        request.headers.get("Idempotency-Key") or
        request.headers.get("X-Idempotency-Key") or
        data.get("idempotency_key") or
        data.get("event_id")
    )
    if idempotency_key:
        existing_transfer = Transfer.query.filter_by(idempotency_key=str(idempotency_key)).first()
        if existing_transfer:
            existing_dev = Device.query.filter_by(id=existing_transfer.device_id).first()
            existing_asmt = existing_dev.assessment if existing_dev else None
            return jsonify({
                "status": "success",
                "idempotent": True,
                "message": "Assessment result already processed (idempotent response)",
                "transfer_id": existing_transfer.id,
                "device_id": existing_transfer.device_id,
                "new_device_status": existing_dev.status if existing_dev else "Completed",
                "assessment": existing_asmt.to_dict() if existing_asmt else None
            }), 200

    # 2. Check device existence first
    device_id = data.get("device_id")
    if not device_id:
        return jsonify({"error": "Validation Error", "message": "device_id is required"}), 400

    device = Device.query.filter_by(id=str(device_id).strip()).first()
    if not device:
        return jsonify({"error": "Not Found", "message": f"Device {device_id} not found"}), 404

    # 3. Numeric range and format validation with math.isfinite()
    try:
        if "repairability_score" in data and data["repairability_score"] is not None:
            if isinstance(data["repairability_score"], bool):
                return jsonify({"error": "Validation Error", "message": "repairability_score must be an integer between 0 and 100"}), 400
            r_val = float(data["repairability_score"])
            if not math.isfinite(r_val) or r_val != int(r_val) or not (0 <= r_val <= 100):
                return jsonify({"error": "Validation Error", "message": "repairability_score must be an integer between 0 and 100"}), 400
            repairability = int(r_val)
        else:
            repairability = None

        if "circular_recovery_score" in data and data["circular_recovery_score"] is not None:
            if isinstance(data["circular_recovery_score"], bool):
                return jsonify({"error": "Validation Error", "message": "circular_recovery_score must be an integer between 0 and 100"}), 400
            c_val = float(data["circular_recovery_score"])
            if not math.isfinite(c_val) or c_val != int(c_val) or not (0 <= c_val <= 100):
                return jsonify({"error": "Validation Error", "message": "circular_recovery_score must be an integer between 0 and 100"}), 400
            recovery_score = int(c_val)
        else:
            recovery_score = None

        if "repair_cost" in data and data["repair_cost"] is not None:
            if isinstance(data["repair_cost"], bool):
                return jsonify({"error": "Validation Error", "message": "repair_cost must be finite and >= 0"}), 400
            repair_cost = float(data["repair_cost"])
            if not math.isfinite(repair_cost) or repair_cost < 0:
                return jsonify({"error": "Validation Error", "message": "repair_cost must be finite and >= 0"}), 400
        else:
            repair_cost = None

        # Accept component_value or component_recovery_value
        comp_val_raw = data.get("component_value") if "component_value" in data else data.get("component_recovery_value")
        if comp_val_raw is not None:
            if isinstance(comp_val_raw, bool):
                return jsonify({"error": "Validation Error", "message": "component_value must be finite and >= 0"}), 400
            component_val = float(comp_val_raw)
            if not math.isfinite(component_val) or component_val < 0:
                return jsonify({"error": "Validation Error", "message": "component_value must be finite and >= 0"}), 400
        else:
            component_val = None

        if "co2_avoided_kg" in data and data["co2_avoided_kg"] is not None:
            if isinstance(data["co2_avoided_kg"], bool):
                return jsonify({"error": "Validation Error", "message": "co2_avoided_kg must be finite and >= 0"}), 400
            co2_avoided = float(data["co2_avoided_kg"])
            if not math.isfinite(co2_avoided) or co2_avoided < 0:
                return jsonify({"error": "Validation Error", "message": "co2_avoided_kg must be finite and >= 0"}), 400
        else:
            co2_avoided = None

    except (TypeError, ValueError) as err:
        return jsonify({"error": "Validation Error", "message": f"Invalid numeric field value: {err}"}), 400

    # 4. Required field presence validation - NO fabricated defaults
    if (
        repairability is None
        or recovery_score is None
        or repair_cost is None
        or component_val is None
        or co2_avoided is None
        or not data.get("recommendation")
    ):
        return jsonify({
            "error": "Validation Error",
            "message": "Missing required assessment fields: repairability_score, circular_recovery_score, repair_cost, component_value, recommendation, co2_avoided_kg"
        }), 400

    # 5. Recommendation validation & canonical mapping
    raw_rec = str(data["recommendation"]).strip()
    rec_clean = raw_rec.upper().replace("-", "_")
    if rec_clean in RECOMMENDATION_ALIASES:
        canonical_rec = RECOMMENDATION_ALIASES[rec_clean]
    elif rec_clean.replace(" ", "_") in RECOMMENDATION_ALIASES:
        canonical_rec = RECOMMENDATION_ALIASES[rec_clean.replace(" ", "_")]
    elif rec_clean in CANONICAL_RECOMMENDATIONS:
        canonical_rec = rec_clean
    else:
        return jsonify({
            "error": "Validation Error",
            "message": f"Unsupported recommendation '{raw_rec}'. Must be one of: PARTS_RECOVERY, RECYCLE, REFURBISH, REJECT, SAFE_DISPOSAL"
        }), 400

    # 6. String length limits
    assessed_by = str(data.get("assessed_by", "GreenTech Refurbishment Partner")).strip()[:128]
    notes = str(data.get("notes", "")).strip()[:2000]

    factors_raw = data.get("factors")
    if isinstance(factors_raw, list):
        factors_str = json.dumps(factors_raw)
    elif isinstance(factors_raw, str):
        factors_str = factors_raw[:2000]
    else:
        factors_str = None

    # 7. Determine new device status based on canonical recommendation
    if canonical_rec == "REFURBISH":
        new_status = "Refurbishment"
    elif canonical_rec == "PARTS_RECOVERY":
        new_status = "Parts recovery"
    elif canonical_rec in ("RECYCLE", "SAFE_DISPOSAL"):
        new_status = "Completed"
    elif canonical_rec == "REJECT":
        # Explicit business mapping: REJECT signifies the device cannot be refurbished or harvested.
        # Recommendation is recorded as 'REJECT' and device transitions to 'Completed' (triage conclusion).
        new_status = "Completed"

    # Verify lifecycle prerequisites: device MUST be in 'Processing'
    if device.status != "Processing":
        return jsonify({
            "error": "Conflict",
            "message": f"Device {device_id} is in status '{device.status}' and cannot transition to '{new_status}'. Device must be in 'Processing' before assessment can be applied."
        }), 409

    if not device.can_transition_to(new_status):
        return jsonify({
            "error": "Conflict",
            "message": f"Device {device_id} is in status '{device.status}' and cannot transition to '{new_status}'."
        }), 409

    # Transactional execution
    try:
        # Save or update AssessmentResult
        assessment = AssessmentResult.query.filter_by(device_id=device_id).first()
        if not assessment:
            assessment = AssessmentResult(device_id=device_id)
            db.session.add(assessment)

        assessment.repairability_score = repairability
        assessment.circular_recovery_score = recovery_score
        assessment.repair_cost = repair_cost
        assessment.component_value = component_val
        assessment.recommendation = canonical_rec
        assessment.co2_avoided_kg = co2_avoided
        assessment.assessed_by = assessed_by
        assessment.notes = notes
        assessment.factors = factors_str
        assessment.assessed_at = datetime.now(timezone.utc)

        # Update device status
        device.transition_to(new_status)

        # Log inbound transfer with idempotency key
        now = datetime.now(timezone.utc)
        transfer_id = f"TRF-{secrets.token_hex(4).upper()}"
        file_id = device_id.replace("EW-", "EW").replace("-", "")
        file_name = f"assessment_{file_id}.json"

        inbound_transfer = Transfer(
            id=transfer_id,
            device_id=device.id,
            file=file_name,
            sender="Refurbishment",
            receiver="Collection",
            status="Completed",
            time=f"Today, {now.strftime('%I:%M %p')}",
            timestamp=now,
            http_status="200 OK",
            response_body="Assessment intelligence received and processed successfully.",
            endpoint="POST /api/v1/assessment-results",
            payload=json.dumps(data),
            idempotency_key=str(idempotency_key) if idempotency_key else None
        )
        db.session.add(inbound_transfer)
        db.session.commit()

    except Exception as e:
        db.session.rollback()
        current_app.logger.error("Transaction failed during assessment processing: %s", e)
        return jsonify({"error": "Internal Error", "message": "Failed to process assessment result."}), 500

    return jsonify({
        "status": "success",
        "message": "Assessment result stored successfully",
        "transfer_id": transfer_id,
        "device_id": device.id,
        "new_device_status": device.status,
        "assessment": assessment.to_dict()
    }), 200


@api_bp.get("/v1/notifications")
def get_notifications():
    """Retrieve recent notifications derived from real database records."""
    items = []
    recent_transfers = Transfer.query.order_by(Transfer.timestamp.desc()).limit(3).all()
    for t in recent_transfers:
        items.append({
            "id": t.id,
            "type": "transfer",
            "title": f"Transfer {t.id} ({t.status})",
            "detail": f"{t.sender} → {t.receiver}: {t.file}",
            "time": t.time or (t.timestamp.strftime("%I:%M %p") if t.timestamp else "")
        })
    recent_asmts = AssessmentResult.query.order_by(AssessmentResult.assessed_at.desc()).limit(3).all()
    for a in recent_asmts:
        items.append({
            "id": f"asmt-{a.device_id}",
            "type": "assessment",
            "title": f"Assessment received for {a.device_id}",
            "detail": f"Outcome: {a.recommendation} (Score: {a.circular_recovery_score}/100)",
            "time": a.assessed_at.strftime("%I:%M %p") if a.assessed_at else ""
        })
    return jsonify({
        "status": "success",
        "notifications": items
    }), 200


@api_bp.get("/v1/files/<storage_key>")
def get_private_file(storage_key):
    """Retrieve private document or invoice with authentication."""
    # Ensure safe storage key and reject path traversal
    if (
        not storage_key
        or not all(c.isalnum() or c in "-_." for c in storage_key)
        or ".." in storage_key
        or "/" in storage_key
        or "\\" in storage_key
    ):
        return jsonify({"error": "Bad Request", "message": "Invalid file key"}), 400

    d_file = DeviceFile.query.filter_by(storage_key=storage_key).first()
    if not d_file:
        return jsonify({"error": "Not Found", "message": "File not found"}), 404

    storage = get_storage_service(current_app.config)
    try:
        file_bytes, content_type = storage.retrieve(storage_key)
        resp = send_file(
            io.BytesIO(file_bytes),
            mimetype=content_type or d_file.content_type or "application/octet-stream",
            as_attachment=False,
            download_name=d_file.original_filename or storage_key
        )
        resp.headers["Content-Disposition"] = f'inline; filename="{d_file.original_filename or storage_key}"'
        return resp
    except FileNotFoundError:
        return jsonify({"error": "Not Found", "message": "File not found"}), 404
    except Exception as e:
        current_app.logger.error("Error retrieving file %s: %s", storage_key, e)
        return jsonify({"error": "Storage Error", "message": "Failed to retrieve requested file"}), 500
