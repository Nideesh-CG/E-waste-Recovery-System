"""REST API blueprint for Circular E-Waste Recovery platform.
Provides authenticated endpoints for devices, transfers, partner assessments, and secure files.
"""

import io
import json
import math
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple
import requests
from flask import Blueprint, current_app, has_request_context, jsonify, request, send_file, session
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
    Accepts valid inbound partner keys (COLLECTION_API_KEY, CIRCULAR_API_KEY) and ADMIN_API_KEY.
    """
    key_sources = [
        ("COLLECTION_API_KEY", current_app.config.get("COLLECTION_API_KEY")),
        ("CIRCULAR_API_KEY", current_app.config.get("CIRCULAR_API_KEY")),
        ("ADMIN_API_KEY", current_app.config.get("ADMIN_API_KEY")),
    ]
    valid_expected_keys = [(name, str(k).strip()) for name, k in key_sources if k and str(k).strip()]

    # In production or development, missing expected keys must fail
    if not valid_expected_keys:
        current_app.logger.warning(
            "[ASSESSMENT_CALLBACK_STAGE_2] Inbound API verification failed: No inbound API keys configured"
        )
        return False

    auth_header = request.headers.get("Authorization", "")
    api_key_header = request.headers.get("X-API-Key", "")

    provided_key = None
    auth_scheme = None
    if api_key_header:
        provided_key = api_key_header.strip()
        auth_scheme = "X-API-Key"
    elif auth_header.startswith("Bearer "):
        provided_key = auth_header.split("Bearer ", 1)[1].strip()
        auth_scheme = "Bearer"

    # Reject query parameter credentials and empty keys
    if not provided_key:
        current_app.logger.warning(
            "[ASSESSMENT_CALLBACK_STAGE_2] Authentication failed: No key header provided | path=%s",
            request.path
        )
        return False

    for key_name, exp_key in valid_expected_keys:
        if secrets.compare_digest(provided_key, exp_key):
            current_app.logger.info(
                "[ASSESSMENT_CALLBACK_STAGE_2] Authentication succeeded | path=%s, scheme=%s, matched_source=%s",
                request.path, auth_scheme, key_name
            )
            return True

    current_app.logger.warning(
        "[ASSESSMENT_CALLBACK_STAGE_2] Authentication failed: Provided key did not match any authorized key | path=%s, scheme=%s",
        request.path, auth_scheme
    )
    return False


@api_bp.before_request
def authenticate():
    """Global authentication guard for API endpoints."""
    # Health check and notifications are public
    clean_path = request.path.rstrip("/")
    if clean_path in ("/api/health", "/api/v1/health", "/api/notifications", "/api/v1/notifications"):
        return None

    # Immediate rejection of path traversal on files endpoint
    for prefix in ("/api/v1/files/", "/api/files/"):
        if request.path.startswith(prefix):
            from urllib.parse import unquote
            subpath = request.path[len(prefix):]
            decoded_subpath = unquote(subpath)
            if (
                ".." in subpath
                or ".." in decoded_subpath
                or "\\" in subpath
                or "\\" in decoded_subpath
            ):
                return jsonify({"error": "Bad Request", "message": "Invalid file key"}), 400

    # Allow browser session authenticated requests to view uploaded files
    if (request.path.startswith("/api/v1/files/") or request.path.startswith("/api/files/")) and (
        session.get("authenticated") or session.get("logged_in")
    ):
        return None

    if not verify_api_key():
        return jsonify({
            "error": "Unauthorized",
            "message": "Invalid or missing API key. Provide via 'X-API-Key' or 'Authorization: Bearer <key>'."
        }), 401


@api_bp.get("/health", strict_slashes=False)
@api_bp.get("/v1/health", strict_slashes=False)
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


@api_bp.get("/devices", strict_slashes=False)
@api_bp.get("/v1/devices", strict_slashes=False)
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


@api_bp.get("/devices/<device_id>", strict_slashes=False)
@api_bp.get("/v1/devices/<device_id>", strict_slashes=False)
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

    storage = get_storage_service(current_app.config)
    base_url = current_app.config.get("BACKEND_URL") or (request.host_url if has_request_context() else "")
    payload = device.to_partner_payload(transfer_id, storage_service=storage, base_url=base_url)
    partner_url = (current_app.config.get("PARTNER_API_URL") or "").rstrip("/")
    partner_key = current_app.config.get("PARTNER_API_KEY") or ""
    now = datetime.now(timezone.utc)
    file_name = f"{device.id}.json"

    failure_stage = "pre_dispatch"
    if partner_url:
        clean_partner_url = partner_url.rstrip("/")
        if clean_partner_url.endswith("/api/v1/refurbishment/devices"):
            target_endpoint = clean_partner_url
        elif clean_partner_url.endswith("/api/v1"):
            target_endpoint = f"{clean_partner_url}/refurbishment/devices"
        elif clean_partner_url.endswith("/api"):
            target_endpoint = f"{clean_partner_url}/v1/refurbishment/devices"
        else:
            target_endpoint = f"{clean_partner_url}/api/v1/refurbishment/devices"

        if not partner_key:
            http_status = "401 Unauthorized"
            response_body = "Missing PARTNER_API_KEY for configured PARTNER_API_URL. Transfer aborted."
            transfer_status = "Failed"
            success = False
            message = "Partner transfer failed: PARTNER_API_KEY is not configured."
            current_app.logger.warning(
                "Partner transfer aborted: PARTNER_API_KEY missing | transfer_id=%s, device_id=%s, endpoint=%s",
                transfer_id, device.id, target_endpoint,
            )
        else:
            headers = {
                "Content-Type": "application/json",
                "X-API-Key": partner_key,
                "Authorization": f"Bearer {partner_key}",
                "Idempotency-Key": transfer_id,
            }
            failure_stage = "partner_dispatch"
            try:
                current_app.logger.info(
                    "Dispatching device to partner | transfer_id=%s, device_id=%s, endpoint=%s, stage=%s",
                    transfer_id, device.id, target_endpoint, failure_stage,
                )
                resp = requests.post(
                    target_endpoint, json=payload, headers=headers, timeout=10
                )
                http_status = f"{resp.status_code} {resp.reason}"
                response_body = resp.text[:2000]

                # Strict API contract & JSON validation (reject HTML rewrites / proxy pages)
                is_html = (
                    response_body.strip().startswith("<!doctype html")
                    or response_body.strip().startswith("<html")
                    or "<html" in response_body[:200].lower()
                )
                raw_ct = ""
                if hasattr(resp, "headers") and hasattr(resp.headers, "get"):
                    raw_ct = resp.headers.get("Content-Type", "")
                if isinstance(raw_ct, str) and "text/html" in raw_ct.lower():
                    is_html = True

                resp_json = None
                if not is_html:
                    try:
                        parsed = resp.json()
                        if isinstance(parsed, dict):
                            resp_json = parsed
                    except Exception:
                        pass
                    if resp_json is None and response_body:
                        try:
                            parsed = json.loads(response_body)
                            if isinstance(parsed, dict):
                                resp_json = parsed
                        except Exception:
                            resp_json = None

                if resp.status_code in (200, 201):
                    if is_html or not isinstance(resp_json, dict):
                        transfer_status = "Failed"
                        success = False
                        message = f"Partner endpoint returned non-JSON response (status {resp.status_code}). Device transfer not confirmed."
                        current_app.logger.warning(
                            "Partner returned non-JSON 200/201 | transfer_id=%s, device_id=%s, content_type=%s, body_preview=%s",
                            transfer_id, device.id, raw_ct, response_body[:200]
                        )
                    else:
                        returned_device_id = resp_json.get("device_id")
                        returned_status = resp_json.get("status")
                        # Confirmed if device_id matches (or omitted in mock) and status confirms receipt
                        dev_confirmed = (returned_device_id == device.id or not returned_device_id)
                        status_confirmed = (
                            returned_status in ("success", "received", "ok", "accepted", "queued", "retried_ok")
                            or (returned_status not in ("error", "failed", "rejected"))
                            or resp.status_code == 201
                        )
                        if dev_confirmed and status_confirmed:
                            transfer_status = "Completed"
                            device.transition_to("Processing")
                            success = True
                            message = "Transfer successfully dispatched to partner application."
                            current_app.logger.info(
                                "Partner transfer confirmed & persisted | transfer_id=%s, device_id=%s, status=%s",
                                transfer_id, device.id, http_status
                            )
                        else:
                            transfer_status = "Failed"
                            success = False
                            message = f"Partner response missing device confirmation (device_id: {returned_device_id}). Transfer not completed."
                            current_app.logger.warning(
                                "Partner response missing device confirmation | transfer_id=%s, expected_device=%s, got_device=%s, status=%s",
                                transfer_id, device.id, returned_device_id, returned_status
                            )
                elif resp.status_code == 202:
                    transfer_status = "Processing"
                    device.transition_to("Processing")
                    success = True
                    message = "Transfer accepted by partner. Assessment in progress."
                    current_app.logger.info(
                        "Partner transfer accepted | transfer_id=%s, device_id=%s, status=%s",
                        transfer_id, device.id, http_status,
                    )
                elif resp.status_code == 409:
                    transfer_status = "Failed"
                    success = False
                    message = "Partner reported duplicate transfer conflict (409)."
                    current_app.logger.warning(
                        "Partner transfer duplicate conflict | transfer_id=%s, device_id=%s, status=%s",
                        transfer_id, device.id, http_status,
                    )
                else:
                    transfer_status = "Failed"
                    success = False
                    err_msg = ""
                    if isinstance(resp_json, dict):
                        err_msg = resp_json.get("message") or resp_json.get("error")
                    if err_msg:
                        message = f"Partner transfer failed with status {resp.status_code}: {err_msg}"
                    else:
                        message = f"Partner transfer failed with status {resp.status_code}."
                    current_app.logger.warning(
                        "Partner transfer failed | transfer_id=%s, device_id=%s, endpoint=%s, status=%s, stage=%s, response=%s",
                        transfer_id, device.id, target_endpoint, http_status, failure_stage, response_body[:200],
                    )
            except requests.Timeout as e:
                http_status = "504 Gateway Timeout"
                response_body = f"Network timeout contacting partner: {type(e).__name__}"
                transfer_status = "Failed"
                success = False
                message = "Network timeout contacting partner. Transfer recorded as Failed."
                current_app.logger.error(
                    "Partner transfer timeout | transfer_id=%s, device_id=%s, endpoint=%s, stage=%s, exception=%s",
                    transfer_id, device.id, target_endpoint, failure_stage, type(e).__name__,
                )
            except requests.RequestException as e:
                http_status = "503 Service Unavailable"
                response_body = f"Connection error: {type(e).__name__}"
                transfer_status = "Failed"
                success = False
                message = "Network error contacting partner. Transfer recorded as Failed."
                current_app.logger.error(
                    "Partner transfer network error | transfer_id=%s, device_id=%s, endpoint=%s, stage=%s, exception=%s",
                    transfer_id, device.id, target_endpoint, failure_stage, type(e).__name__,
                )
    else:
        target_endpoint = "POST /api/v1/refurbishment/devices"
        http_status = "Not sent — awaiting dispatch"
        response_body = "Demo transfer queued. No external API is connected."
        transfer_status = "Pending"
        success = False
        message = "Demo transfer queued. No external API is connected."

    existing_transfer = Transfer.query.filter(
        (Transfer.id == transfer_id) | (Transfer.idempotency_key == transfer_id)
    ).first()
    if existing_transfer:
        transfer = existing_transfer
        transfer.device_id = device.id
        transfer.file = file_name
        transfer.status = transfer_status
        transfer.time = f"Today, {now.strftime('%I:%M %p')}"
        transfer.timestamp = now
        transfer.http_status = http_status
        transfer.response_body = response_body
        transfer.endpoint = target_endpoint
        transfer.payload = json.dumps(payload)
        transfer.idempotency_key = transfer_id
    else:
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

    try:
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.error(
            "Failed to commit transfer dispatch record | transfer_id=%s, error=%s",
            transfer_id, exc, exc_info=True
        )
        success = False
        message = f"Failed to persist transfer dispatch record: {type(exc).__name__}"
        if transfer:
            transfer.status = "Failed"
    return transfer, success, message


@api_bp.post("/devices/<device_id>/send", strict_slashes=False)
@api_bp.post("/v1/devices/<device_id>/send", strict_slashes=False)
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
        else (409 if "409" in (transfer.http_status or "")
        else (202 if transfer.status == "Pending"
        else (503 if "503" in (transfer.http_status or "")
        else 502))))
    )
    return jsonify({
        "status": "success" if success else "failed",
        "message": message,
        "transfer": transfer.to_dict(),
        "device_status": device.status
    }), http_code


@api_bp.get("/transfers", strict_slashes=False)
@api_bp.get("/v1/transfers", strict_slashes=False)
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


@api_bp.get("/transfers/<transfer_id>", strict_slashes=False)
@api_bp.get("/v1/transfers/<transfer_id>", strict_slashes=False)
def get_transfer(transfer_id):
    """Retrieve details for a specific transfer."""
    transfer = Transfer.query.filter_by(id=transfer_id).first()
    if not transfer:
        return jsonify({"error": "Transfer not found", "transfer_id": transfer_id}), 404
    return jsonify(transfer.to_dict()), 200


def extract_numeric(val, field_name: str, min_val: float = None, max_val: float = None, default=None):
    """Safely parse numbers from floats, ints, or formatted strings (currency, commas, units)."""
    if val is None or val == "":
        return default
    if isinstance(val, bool):
        raise ValueError(f"{field_name} cannot be a boolean")
    if isinstance(val, (int, float)):
        num = float(val)
    elif isinstance(val, str):
        cleaned = val.strip()
        for symbol in ("₹", "$", "€", "£", ","):
            cleaned = cleaned.replace(symbol, "")
        cleaned = cleaned.strip()
        if "/" in cleaned:
            cleaned = cleaned.split("/")[0].strip()
        else:
            parts = cleaned.split()
            if parts:
                cleaned = parts[0].strip()
        try:
            num = float(cleaned)
        except (ValueError, TypeError):
            raise ValueError(f"{field_name} must be a valid number, got '{val}'")
    else:
        try:
            num = float(val)
        except (ValueError, TypeError):
            raise ValueError(f"{field_name} must be a valid number, got '{val}'")

    if not math.isfinite(num):
        raise ValueError(f"{field_name} must be a finite number")
    if min_val is not None and num < min_val:
        raise ValueError(f"{field_name} must be >= {min_val}")
    if max_val is not None and num > max_val:
        raise ValueError(f"{field_name} must be <= {max_val}")
    return num


@api_bp.post("/v1/assessment-results", strict_slashes=False)
@api_bp.post("/v1/collection/assessments", strict_slashes=False)
@api_bp.post("/assessment-results", strict_slashes=False)
@api_bp.post("/collection/assessments", strict_slashes=False)
def receive_assessment():
    """Inbound webhook / API endpoint for partners to post assessment results.
    
    Enforces strict field validation, non-negative values, range checks,
    transactional persistence, and idempotency handling.
    """
    current_app.logger.info(
        "[ASSESSMENT_CALLBACK_STAGE_1] Request received | path=%s, method=%s, remote_addr=%s, content_type=%s",
        request.path, request.method, request.remote_addr, request.content_type
    )

    if not request.is_json:
        current_app.logger.warning(
            "[ASSESSMENT_CALLBACK_STAGE_1] Rejected non-JSON request | content_type=%s",
            request.content_type
        )
        return jsonify({"error": "Bad Request", "message": "JSON body required"}), 400

    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        current_app.logger.warning("[ASSESSMENT_CALLBACK_STAGE_1] Empty or non-dict JSON body")
        return jsonify({"error": "Bad Request", "message": "Empty or malformed JSON payload"}), 400

    # 1. Check idempotency key (header or payload)
    idempotency_key = (
        request.headers.get("Idempotency-Key") or
        request.headers.get("X-Idempotency-Key") or
        data.get("idempotency_key") or
        data.get("assessment_id") or
        data.get("event_id")
    )
    is_force = bool(
        request.headers.get("X-Force-Update")
        or data.get("force_resend")
        or data.get("force")
        or data.get("retry")
    )
    if idempotency_key and not is_force:
        try:
            existing_transfer = Transfer.query.filter_by(idempotency_key=str(idempotency_key)).first()
            if existing_transfer:
                existing_dev = Device.query.filter_by(id=existing_transfer.device_id).first()
                existing_asmt = AssessmentResult.query.filter_by(device_id=existing_transfer.device_id).first()
                if existing_asmt:
                    current_app.logger.info(
                        "[ASSESSMENT_CALLBACK_IDEMPOTENT] Duplicate delivery returned idempotent response | idempotency_key=%s, device_id=%s, transfer_id=%s",
                        idempotency_key, existing_transfer.device_id, existing_transfer.id
                    )
                    return jsonify({
                        "success": True,
                        "status": "success",
                        "idempotent": True,
                        "message": "Assessment received successfully",
                        "transfer_id": existing_transfer.id,
                        "device_id": existing_transfer.device_id,
                        "assessment_id": data.get("assessment_id") or str(idempotency_key),
                        "new_device_status": existing_dev.status if existing_dev else "Completed",
                        "assessment": existing_asmt.to_dict()
                    }), 200
        except Exception as idemp_err:
            current_app.logger.warning(
                "[ASSESSMENT_CALLBACK_IDEMPOTENT] Querying existing transfer encountered warning: %s",
                idemp_err, exc_info=True
            )

    # 2. Check device existence first
    device_id = data.get("device_id")
    if not device_id:
        current_app.logger.warning("[ASSESSMENT_CALLBACK_STAGE_3] Validation failure: device_id is missing")
        return jsonify({"error": "Validation Error", "message": "device_id is required"}), 400

    clean_device_id = str(device_id).strip()
    failure_stage = "device_lookup"
    try:
        device = Device.query.filter_by(id=clean_device_id).first()
    except Exception as db_err:
        current_app.logger.error(
            "[ASSESSMENT_CALLBACK_ERROR] Database query failure during device lookup | device_id=%s, error=%s",
            clean_device_id, db_err, exc_info=True
        )
        return jsonify({
            "success": False,
            "error": "Internal Error",
            "message": f"Database error looking up device {clean_device_id}: {type(db_err).__name__}"
        }), 500

    if not device:
        current_app.logger.warning(
            "[ASSESSMENT_CALLBACK_STAGE_4] Device not found | device_id=%s",
            clean_device_id
        )
        return jsonify({"error": "Not Found", "message": f"Device {clean_device_id} not found"}), 404

    # 3. Numeric range and format validation with extract_numeric()
    failure_stage = "payload_validation"
    try:
        r_raw = data.get("repairability_score") if "repairability_score" in data else data.get("repairability")
        repairability = extract_numeric(r_raw, "repairability_score", min_val=0.0, max_val=100.0)

        c_raw = data.get("circular_recovery_score") if "circular_recovery_score" in data else data.get("circular_score")
        recovery_score = extract_numeric(c_raw, "circular_recovery_score", min_val=0.0, max_val=100.0)
        if recovery_score is None and repairability is not None:
            recovery_score = repairability  # Default fallback if partner payload does not supply separate recovery score
        if repairability is None and recovery_score is not None:
            repairability = recovery_score

        rc_raw = data.get("repair_cost") if "repair_cost" in data else data.get("estimated_repair_cost")
        repair_cost = extract_numeric(rc_raw, "repair_cost", min_val=0.0)

        comp_val_raw = data.get("component_value") if "component_value" in data else data.get("component_recovery_value")
        component_val = extract_numeric(comp_val_raw, "component_value", min_val=0.0)

        co2_raw = data.get("co2_avoided_kg") if "co2_avoided_kg" in data else data.get("environmental_benefit")
        co2_avoided = extract_numeric(co2_raw, "co2_avoided_kg", min_val=0.0)

    except ValueError as val_err:
        current_app.logger.warning(
            "[ASSESSMENT_CALLBACK_STAGE_3] Numeric validation error | device_id=%s, error=%s",
            clean_device_id, val_err
        )
        return jsonify({"error": "Validation Error", "message": str(val_err)}), 400

    # 4. Required field presence validation - NO fabricated defaults
    rec_val = data.get("recommendation") or data.get("recommended_action")
    if (
        repairability is None
        or recovery_score is None
        or repair_cost is None
        or component_val is None
        or co2_avoided is None
        or not rec_val
    ):
        current_app.logger.warning(
            "[ASSESSMENT_CALLBACK_STAGE_3] Missing required fields | device_id=%s, repairability=%s, recovery_score=%s, repair_cost=%s, component_val=%s, co2=%s, rec_val=%s",
            clean_device_id, repairability, recovery_score, repair_cost, component_val, co2_avoided, bool(rec_val)
        )
        return jsonify({
            "error": "Validation Error",
            "message": "Missing required assessment fields: repairability_score, circular_recovery_score, repair_cost, component_value, recommendation, co2_avoided_kg"
        }), 400

    # 5. Recommendation validation & canonical mapping
    raw_rec = str(rec_val).strip()
    rec_clean = raw_rec.upper().replace("-", "_")
    if rec_clean in RECOMMENDATION_ALIASES:
        canonical_rec = RECOMMENDATION_ALIASES[rec_clean]
    elif rec_clean.replace(" ", "_") in RECOMMENDATION_ALIASES:
        canonical_rec = RECOMMENDATION_ALIASES[rec_clean.replace(" ", "_")]
    elif rec_clean in CANONICAL_RECOMMENDATIONS:
        canonical_rec = rec_clean
    else:
        current_app.logger.warning(
            "[ASSESSMENT_CALLBACK_STAGE_3] Unsupported recommendation | device_id=%s, recommendation=%s",
            clean_device_id, raw_rec
        )
        return jsonify({
            "error": "Validation Error",
            "message": f"Unsupported recommendation '{raw_rec}'. Must be one of: PARTS_RECOVERY, RECYCLE, REFURBISH, REJECT, SAFE_DISPOSAL"
        }), 400

    # 6. String length limits and metadata fields
    assessed_by = str(data.get("assessed_by", "GreenTech Refurbishment Partner")).strip()[:128]
    notes = str(data.get("notes") or data.get("technical_summary") or "").strip()[:2000]

    factors_raw = data.get("factors") or data.get("reasons") or data.get("component_breakdown")
    if isinstance(factors_raw, (list, dict)):
        factors_str = json.dumps(factors_raw)
    elif isinstance(factors_raw, str):
        factors_str = factors_raw[:2000]
    else:
        factors_str = None

    assessed_at_raw = data.get("assessment_date") or data.get("assessed_at")
    assessed_at_val = None
    if assessed_at_raw:
        try:
            assessed_at_val = datetime.fromisoformat(str(assessed_at_raw).replace("Z", "+00:00"))
        except Exception:
            assessed_at_val = None
    if not assessed_at_val:
        assessed_at_val = datetime.now(timezone.utc)

    # 7. Determine new device status based on canonical recommendation
    if canonical_rec == "REFURBISH":
        new_status = "Refurbishment"
    elif canonical_rec == "PARTS_RECOVERY":
        new_status = "Parts recovery"
    elif canonical_rec in ("RECYCLE", "SAFE_DISPOSAL", "REJECT"):
        new_status = "Completed"
    else:
        new_status = "Completed"

    current_app.logger.info(
        "[ASSESSMENT_CALLBACK_STAGE_3] Payload validation succeeded | device_id=%s, repairability=%.2f, recovery_score=%.2f, repair_cost=%.2f, component_val=%.2f, co2_avoided=%.2f, recommendation=%s, new_status=%s",
        clean_device_id, repairability, recovery_score, repair_cost, component_val, co2_avoided, canonical_rec, new_status
    )

    # Verify lifecycle prerequisites: device MUST be in 'Processing' or already in target new_status (for idempotent retries/resends)
    if device.status != "Processing" and device.status != new_status:
        current_app.logger.warning(
            "[ASSESSMENT_CALLBACK_STAGE_4] State conflict | device_id=%s, current_status=%s, target_status=%s",
            clean_device_id, device.status, new_status
        )
        return jsonify({
            "error": "Conflict",
            "message": f"Device {clean_device_id} is in status '{device.status}' and cannot transition to '{new_status}'. Device must be in 'Processing' before assessment can be applied."
        }), 409

    current_app.logger.info(
        "[ASSESSMENT_CALLBACK_STAGE_4] Device lookup result | device_id=%s, found=True, current_status=%s, target_status=%s",
        clean_device_id, device.status, new_status
    )

    assessment_id_val = str(data.get("assessment_id") or f"ASM-{clean_device_id}")

    # Transactional execution
    failure_stage = "assessment_persistence"
    try:
        # Save or update AssessmentResult
        assessment = AssessmentResult.query.filter_by(device_id=clean_device_id).first()
        is_update = bool(assessment)
        if not assessment:
            assessment = AssessmentResult(device_id=clean_device_id)
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
        assessment.assessed_at = assessed_at_val

        # Update device status
        if device.status == "Processing" or device.can_transition_to(new_status):
            device.transition_to(new_status)
        else:
            device.status = new_status

        current_app.logger.info(
            "[ASSESSMENT_CALLBACK_STAGE_5] Assessment record prepared | device_id=%s, action=%s, target_status=%s",
            clean_device_id, "UPDATE" if is_update else "CREATE", device.status
        )

        # Log inbound transfer with idempotency key (upsert to avoid unique constraint violations)
        now = datetime.now(timezone.utc)
        file_id = clean_device_id.replace("EW-", "EW").replace("-", "")
        file_name = f"assessment_{file_id}.json"

        inbound_transfer = None
        if idempotency_key:
            inbound_transfer = Transfer.query.filter_by(idempotency_key=str(idempotency_key)).first()

        if not inbound_transfer:
            transfer_id = f"TRF-{secrets.token_hex(4).upper()}"
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
        else:
            transfer_id = inbound_transfer.id
            inbound_transfer.status = "Completed"
            inbound_transfer.http_status = "200 OK"
            inbound_transfer.response_body = "Assessment intelligence received and processed successfully."
            inbound_transfer.payload = json.dumps(data)
            inbound_transfer.timestamp = now
            inbound_transfer.time = f"Today, {now.strftime('%I:%M %p')}"

        current_app.logger.info(
            "[ASSESSMENT_CALLBACK_STAGE_7] Transfer audit prepared | transfer_id=%s, device_id=%s, status=%s",
            transfer_id, clean_device_id, inbound_transfer.status
        )

        failure_stage = "database_commit"
        current_app.logger.info(
            "[ASSESSMENT_CALLBACK_STAGE_6] Executing primary database commit | device_id=%s, transfer_id=%s",
            clean_device_id, transfer_id
        )

        try:
            db.session.commit()
            current_app.logger.info(
                "[ASSESSMENT_CALLBACK_STAGE_6] Primary database commit succeeded | device_id=%s, transfer_id=%s, device_status=%s",
                clean_device_id, transfer_id, device.status
            )
        except Exception as commit_err:
            db.session.rollback()
            current_app.logger.warning(
                "[ASSESSMENT_CALLBACK_STAGE_6] Primary commit failed (%s: %s). Activating integer score coercion fallback for schema compatibility.",
                type(commit_err).__name__, commit_err, exc_info=True
            )
            # Re-fetch records after rollback
            device = Device.query.filter_by(id=clean_device_id).first()
            assessment = AssessmentResult.query.filter_by(device_id=clean_device_id).first()
            if not assessment:
                assessment = AssessmentResult(device_id=clean_device_id)
                db.session.add(assessment)

            assessment.repairability_score = int(round(repairability))
            assessment.circular_recovery_score = int(round(recovery_score))
            assessment.repair_cost = float(repair_cost)
            assessment.component_value = float(component_val)
            assessment.recommendation = canonical_rec
            assessment.co2_avoided_kg = float(co2_avoided)
            assessment.assessed_by = assessed_by
            assessment.notes = notes
            assessment.factors = factors_str
            assessment.assessed_at = assessed_at_val

            if device:
                if device.status == "Processing" or device.can_transition_to(new_status):
                    device.transition_to(new_status)
                else:
                    device.status = new_status

            inbound_transfer = None
            if idempotency_key:
                inbound_transfer = Transfer.query.filter_by(idempotency_key=str(idempotency_key)).first()
            if not inbound_transfer:
                transfer_id = f"TRF-{secrets.token_hex(4).upper()}"
                inbound_transfer = Transfer(
                    id=transfer_id,
                    device_id=device.id if device else clean_device_id,
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
            else:
                transfer_id = inbound_transfer.id
                inbound_transfer.status = "Completed"
                inbound_transfer.http_status = "200 OK"
                inbound_transfer.response_body = "Assessment intelligence received and processed successfully."
                inbound_transfer.payload = json.dumps(data)
                inbound_transfer.timestamp = now
                inbound_transfer.time = f"Today, {now.strftime('%I:%M %p')}"

            db.session.commit()
            current_app.logger.info(
                "[ASSESSMENT_CALLBACK_STAGE_6] Fallback database commit succeeded | device_id=%s, transfer_id=%s, device_status=%s",
                clean_device_id, transfer_id, device.status
            )

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(
            "[ASSESSMENT_CALLBACK_ERROR] Unhandled exception during assessment processing | device_id=%s, stage=%s, exception_type=%s, error_message=%s",
            clean_device_id,
            failure_stage,
            type(e).__name__,
            str(e),
            exc_info=True,
        )
        return jsonify({
            "success": False,
            "error": "Internal Error",
            "message": f"Failed to process assessment result: {type(e).__name__} - {str(e)}"
        }), 500

    current_app.logger.info(
        "[ASSESSMENT_CALLBACK_STAGE_8] Returning final HTTP 200 response | device_id=%s, transfer_id=%s, new_status=%s",
        clean_device_id, transfer_id, device.status
    )

    return jsonify({
        "success": True,
        "status": "success",
        "message": "Assessment received successfully",
        "device_id": device.id,
        "assessment_id": assessment_id_val,
        "transfer_id": transfer_id,
        "new_device_status": device.status,
        "assessment": assessment.to_dict()
    }), 200


@api_bp.get("/notifications", strict_slashes=False)
@api_bp.get("/v1/notifications", strict_slashes=False)
def get_notifications():
    """Retrieve recent notifications derived from real database records."""
    items = []
    try:
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
    except Exception as exc:
        current_app.logger.warning("Error loading notifications: %s", exc)

    return jsonify({
        "status": "success",
        "notifications": items
    }), 200


@api_bp.get("/files/<path:storage_key>", strict_slashes=False)
@api_bp.get("/v1/files/<path:storage_key>", strict_slashes=False)
def get_private_file(storage_key):
    """Retrieve private document or invoice with authentication."""
    # Ensure safe storage key and reject path traversal
    if (
        not storage_key
        or not all(c.isalnum() or c in "-_./" for c in storage_key)
        or ".." in storage_key
        or "\\" in storage_key
    ):
        return jsonify({"error": "Bad Request", "message": "Invalid file key"}), 400

    d_file = DeviceFile.query.filter(
        (DeviceFile.storage_key == storage_key)
        | (DeviceFile.filename == storage_key)
        | (DeviceFile.storage_key.endswith("/" + storage_key))
    ).first()
    if not d_file:
        return jsonify({"error": "Not Found", "message": "File not found"}), 404

    storage = get_storage_service(current_app.config)

    # If short-lived signed URL is requested and supported by storage backend
    if request.args.get("signed") == "true" and hasattr(storage, "create_signed_url"):
        try:
            signed_url = storage.create_signed_url(d_file.storage_key, expires_in=3600)
            return jsonify({
                "storage_key": d_file.storage_key,
                "signed_url": signed_url,
                "expires_in": 3600
            }), 200
        except Exception as e:
            current_app.logger.warning("Could not generate signed URL: %s", e)

    try:
        file_bytes, content_type = storage.retrieve(d_file.storage_key)
        safe_filename = (d_file.original_filename or Path(storage_key).name or "document.bin").replace('"', '')
        resp = send_file(
            io.BytesIO(file_bytes),
            mimetype=content_type or d_file.content_type or "application/octet-stream",
            as_attachment=False,
            download_name=safe_filename
        )
        resp.headers["Content-Disposition"] = f'inline; filename="{safe_filename}"'
        return resp
    except FileNotFoundError:
        return jsonify({"error": "Not Found", "message": "File not found"}), 404
    except PermissionError as e:
        return jsonify({"error": "Forbidden", "message": str(e)}), 403
    except Exception as e:
        current_app.logger.error("Error retrieving file %s: %s", storage_key, e)
        return jsonify({"error": "Storage Error", "message": "Failed to retrieve requested file"}), 500
