"""Targeted verification test for the exact production failing device:
Device: HP Omen Hyper X
Device ID: EW-2026-D86F5C8F
Repairability score: 59.5/100
Repair cost: 6600
Component recovery value: 9700
Estimated refurbished value: 14000
Environmental benefit: 4.3 kg CO2 avoided
Recommended action: PARTS RECOVERY
"""

import json
import os
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from models import Device, AssessmentResult, Transfer, db

DEVICE_ID = "EW-2026-D86F5C8F"
MODEL_NAME = "HP Omen Hyper X"

@pytest.fixture
def app():
    app = create_app("testing")
    app.config.update({
        "TESTING": True,
        "CIRCULAR_API_KEY": "test-circular-api-key-2026",
        "COLLECTION_API_KEY": "test-circular-api-key-2026",
        "ADMIN_API_KEY": "test-admin-api-key-2026",
    })
    with app.app_context():
        db.create_all()
        # Ensure device exists and is in 'Processing' state
        dev = Device.query.filter_by(id=DEVICE_ID).first()
        if not dev:
            dev = Device(
                id=DEVICE_ID,
                name=f"HP {MODEL_NAME}",
                type="Laptop",
                brand="HP",
                model=MODEL_NAME,
                status="Processing",
                condition="Damaged",
                year="2022",
                weight="2.3",
                damage="Cracked screen, failed battery",
                power="No power",
                screen="Damaged",
                battery="Degraded",
            )
            db.session.add(dev)
        else:
            dev.status = "Processing"
        db.session.commit()

        yield app

        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def test_hp_omen_callback_with_float_scores_and_parts_recovery(client):
    """Test callback delivery for HP Omen Hyper X with floating-point score 59.5."""
    payload = {
        "device_id": DEVICE_ID,
        "assessment_id": "ASM-TEST-D86F5C8F",
        "repairability_score": 59.5,
        "circular_recovery_score": 59.5,
        "repair_cost": 6600.0,
        "estimated_repair_cost": 6600.0,
        "component_value": 9700.0,
        "component_recovery_value": 9700.0,
        "estimated_refurbished_value": 14000.0,
        "co2_avoided_kg": 4.3,
        "environmental_benefit": 4.3,
        "recommended_action": "PARTS RECOVERY",
        "assessed_by": "Refurbishment Intelligence Partner",
        "notes": "Cost of repair exceeds target threshold. Harvest valuable internal components.",
        "factors": ["Damaged OLED Display", "Functional 1TB NVMe SSD", "Functional 16GB DDR5 RAM"],
        "assessment_date": "2026-11-01T14:00:00Z"
    }

    # 1. Delivery with valid COLLECTION_API_KEY via X-API-Key
    headers = {
        "X-API-Key": "test-circular-api-key-2026",
        "Idempotency-Key": "ASM-TEST-D86F5C8F",
        "Content-Type": "application/json",
    }
    resp = client.post("/api/v1/assessment-results", headers=headers, json=payload)
    assert resp.status_code == 200, f"Expected 200 OK, got {resp.status_code}: {resp.data.decode()}"
    data = resp.get_json()
    assert data["success"] is True
    assert data["status"] == "success"
    assert data["device_id"] == DEVICE_ID
    assert data["new_device_status"] == "Parts recovery"
    assert data["assessment_id"] == "ASM-TEST-D86F5C8F"
    assert data["assessment"]["repairability_score"] == 59.5
    assert data["assessment"]["recommendation"] == "PARTS_RECOVERY"

    # 2. Verify database records
    device = Device.query.filter_by(id=DEVICE_ID).first()
    assert device is not None
    assert device.status == "Parts recovery"

    assessment = AssessmentResult.query.filter_by(device_id=DEVICE_ID).first()
    assert assessment is not None
    assert assessment.repairability_score == 59.5
    assert assessment.repair_cost == 6600.0
    assert assessment.component_value == 9700.0
    assert assessment.co2_avoided_kg == 4.3
    assert assessment.recommendation == "PARTS_RECOVERY"

    transfer = Transfer.query.filter_by(device_id=DEVICE_ID, sender="Refurbishment").first()
    assert transfer is not None
    assert transfer.status == "Completed"
    assert transfer.http_status == "200 OK"

    # 3. Test idempotent retry (re-sending same assessment)
    resp_retry = client.post("/api/v1/assessment-results", headers=headers, json=payload)
    assert resp_retry.status_code == 200
    retry_data = resp_retry.get_json()
    assert retry_data["success"] is True
    assert retry_data["new_device_status"] == "Parts recovery"

    # 4. Test force re-send with updated notes
    payload["notes"] = "Updated technical notes after re-evaluation"
    headers["X-Force-Update"] = "true"
    resp_force = client.post("/api/v1/assessment-results", headers=headers, json=payload)
    assert resp_force.status_code == 200
    force_data = resp_force.get_json()
    assert force_data["success"] is True
    assert "Updated technical notes" in force_data["assessment"]["notes"]


def test_hp_omen_callback_with_admin_api_key(client):
    """Test callback delivery authenticated via ADMIN_API_KEY with Bearer token."""
    payload = {
        "device_id": DEVICE_ID,
        "assessment_id": "ASM-TEST-ADMIN",
        "repairability_score": 59.5,
        "circular_recovery_score": 59.5,
        "repair_cost": 6600.0,
        "component_value": 9700.0,
        "co2_avoided_kg": 4.3,
        "recommended_action": "PARTS RECOVERY",
    }
    headers = {
        "Authorization": "Bearer test-admin-api-key-2026",
        "Idempotency-Key": "ASM-TEST-ADMIN",
        "Content-Type": "application/json",
    }
    resp = client.post("/api/v1/assessment-results", headers=headers, json=payload)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["new_device_status"] == "Parts recovery"


def test_unauthorized_callback_rejected(client):
    """Test callback rejection with 401 when key is invalid or missing."""
    payload = {"device_id": DEVICE_ID}
    # Missing key
    resp1 = client.post("/api/v1/assessment-results", json=payload)
    assert resp1.status_code == 401
    assert resp1.get_json()["error"] == "Unauthorized"

    # Invalid key
    resp2 = client.post(
        "/api/v1/assessment-results",
        headers={"X-API-Key": "invalid-garbage-key"},
        json=payload
    )
    assert resp2.status_code == 401
    assert resp2.get_json()["error"] == "Unauthorized"


def test_hp_omen_callback_with_formatted_strings(client):
    """Test callback delivery when partner or proxy passes formatted strings (currency, units)."""
    payload = {
        "device_id": DEVICE_ID,
        "assessment_id": "ASM-TEST-FORMATTED",
        "repairability_score": "59.5/100",
        "circular_recovery_score": "59.5",
        "repair_cost": "₹6,600",
        "estimated_repair_cost": "6,600",
        "component_value": "₹9,700",
        "component_recovery_value": "9,700",
        "estimated_refurbished_value": "₹14,000",
        "co2_avoided_kg": "4.3 kg CO2 avoided",
        "environmental_benefit": "4.3 kg",
        "recommended_action": "PARTS RECOVERY",
        "notes": "Cost of repair exceeds target threshold. Harvest valuable internal components.",
    }
    headers = {
        "X-API-Key": "test-circular-api-key-2026",
        "Idempotency-Key": "ASM-TEST-FORMATTED",
        "Content-Type": "application/json",
    }
    resp = client.post("/api/v1/assessment-results", headers=headers, json=payload)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["assessment"]["repairability_score"] == 59.5
    assert data["assessment"]["repair_cost"] == 6600.0
    assert data["assessment"]["component_value"] == 9700.0
    assert data["assessment"]["co2_avoided_kg"] == 4.3


def test_assessment_persists_and_reflects_in_reports_and_metrics(client, app):
    """Test that assessment results reflect in platform metrics and reports."""
    from metrics import get_platform_metrics
    from reports import generate_assessment_pdf

    payload = {
        "device_id": DEVICE_ID,
        "assessment_id": "ASM-TEST-METRICS",
        "repairability_score": 59.5,
        "circular_recovery_score": 59.5,
        "repair_cost": 6600.0,
        "component_value": 9700.0,
        "co2_avoided_kg": 4.3,
        "recommended_action": "PARTS RECOVERY",
        "notes": "Enterprise gaming laptop evaluated for parts recovery.",
    }
    headers = {
        "X-API-Key": "test-circular-api-key-2026",
        "Idempotency-Key": "ASM-TEST-METRICS",
        "Content-Type": "application/json",
    }
    resp = client.post("/api/v1/assessment-results", headers=headers, json=payload)
    assert resp.status_code == 200

    with app.app_context():
        # Check platform metrics
        metrics = get_platform_metrics()
        assert metrics["parts_recovery"] >= 1
        assert metrics["total_recovery_value"] >= 9700.0
        assert metrics["total_co2_kg"] >= 4.3

        # Check technical assessment PDF report generation
        dev = Device.query.filter_by(id=DEVICE_ID).first()
        asmt = AssessmentResult.query.filter_by(device_id=DEVICE_ID).first()
        assert dev is not None
        assert asmt is not None

        pdf_bytes = generate_assessment_pdf(dev.to_dict(), asmt.to_dict())
        assert len(pdf_bytes) > 500
        assert pdf_bytes.startswith(b"%PDF")


def test_ew_2026_b834e735_callback_delivery_with_collection_api_key(client, app):
    """Target verification test for reported device EW-2026-B834E735 stuck in Processing."""
    b834_id = "EW-2026-B834E735"
    with app.app_context():
        dev = Device.query.filter_by(id=b834_id).first()
        if not dev:
            dev = Device(
                id=b834_id,
                name="Lenovo ThinkPad T14",
                type="Laptop",
                brand="Lenovo",
                model="ThinkPad T14 Gen 2",
                status="Processing",
                condition="Used",
                year="2021",
                weight="1.5",
                damage="Motherboard issue, good battery and screen",
                power="Powers on",
                screen="Flawless",
                battery="Good",
            )
            db.session.add(dev)
        else:
            dev.status = "Processing"
        db.session.commit()

    payload = {
        "device_id": b834_id,
        "transfer_id": "TRF-OUT-B834E735",
        "assessment_id": "ASM-B834E735",
        "idempotency_key": "ASM-B834E735",
        "repairability_score": 62.5,
        "circular_recovery_score": 68.0,
        "technical_condition_score": 65.0,
        "age_score": 70.0,
        "confidence_level": "High",
        "repair_cost": 4500.0,
        "estimated_repair_cost": 4500.0,
        "component_value": 8200.0,
        "component_recovery_value": 8200.0,
        "estimated_refurbished_value": 16000.0,
        "recommendation": "PARTS RECOVERY",
        "recommended_action": "PARTS RECOVERY",
        "co2_avoided_kg": 5.2,
        "environmental_benefit": 5.2,
        "notes": "Cost-effective parts harvesting recommended for display and RAM modules.",
        "assessed_at": "2026-11-01T15:30:00Z"
    }

    # Authenticate via COLLECTION_API_KEY
    headers = {
        "X-API-Key": "test-circular-api-key-2026",
        "Authorization": "Bearer test-circular-api-key-2026",
        "Idempotency-Key": "ASM-B834E735",
        "Content-Type": "application/json",
    }

    resp = client.post("/api/v1/assessment-results", headers=headers, json=payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.data.decode()}"
    data = resp.get_json()
    assert data["success"] is True
    assert data["status"] == "success"
    assert data["device_id"] == b834_id
    assert data["new_device_status"] == "Parts recovery"
    assert data["assessment"]["repairability_score"] == 62.5
    assert data["assessment"]["circular_recovery_score"] == 68.0

    with app.app_context():
        # Verify device is no longer in Processing
        updated_dev = Device.query.filter_by(id=b834_id).first()
        assert updated_dev is not None
        assert updated_dev.status == "Parts recovery"
        assert updated_dev.status != "Processing"

        # Verify assessment record
        asmt = AssessmentResult.query.filter_by(device_id=b834_id).first()
        assert asmt is not None
        assert asmt.repairability_score == 62.5
        assert asmt.recommendation == "PARTS_RECOVERY"


def test_ew_2026_b834e735_missing_optional_fields_and_nested_payload(client, app):
    """Test safe handling when optional fields are omitted or payload is nested under 'assessment'."""
    nested_id = "EW-2026-B834E735"
    with app.app_context():
        dev = Device.query.filter_by(id=nested_id).first()
        if not dev:
            dev = Device(
                id=nested_id,
                name="Lenovo ThinkPad T14",
                type="Laptop",
                brand="Lenovo",
                model="ThinkPad T14",
                status="Processing",
                condition="Used",
                year="2021",
                weight="1.5",
            )
            db.session.add(dev)
        else:
            dev.status = "Processing"
        db.session.commit()

    # Payload with nested assessment dict and missing optional financial/environmental fields
    nested_payload = {
        "device_id": nested_id,
        "assessment_id": "ASM-NESTED-B834",
        "assessment": {
            "repairability_score": 75.0,
            "circular_recovery_score": 80.0,
            "recommendation": "REFURBISH",
            "notes": "Good condition unit suitable for refurbishment.",
        }
    }
    headers = {
        "X-API-Key": "test-circular-api-key-2026",
        "Content-Type": "application/json",
    }

    resp = client.post("/api/v1/assessment-results", headers=headers, json=nested_payload)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.data.decode()}"
    data = resp.get_json()
    assert data["success"] is True
    assert data["new_device_status"] == "Refurbishment"
    assert data["assessment"]["repair_cost"] == 0.0  # safely defaulted
    assert data["assessment"]["component_value"] == 0.0  # safely defaulted
    assert data["assessment"]["co2_avoided_kg"] == 0.0  # safely defaulted


def test_ew_2026_b834e735_duplicate_idempotent_retry(client, app):
    """Test that partner retrying the assessment delivers idempotent 200 OK without duplicating records."""
    retry_id = "EW-2026-B834E735"
    with app.app_context():
        dev = Device.query.filter_by(id=retry_id).first()
        if not dev:
            dev = Device(
                id=retry_id,
                name="Lenovo ThinkPad T14",
                type="Laptop",
                brand="Lenovo",
                model="ThinkPad T14",
                status="Processing",
                condition="Used",
                year="2021",
                weight="1.5",
            )
            db.session.add(dev)
        else:
            dev.status = "Processing"
        db.session.commit()

    payload = {
        "device_id": retry_id,
        "assessment_id": "ASM-RETRY-TEST",
        "idempotency_key": "ASM-RETRY-TEST",
        "repairability_score": 55.0,
        "circular_recovery_score": 60.0,
        "recommendation": "PARTS RECOVERY",
        "repair_cost": 5000.0,
        "component_value": 7500.0,
        "co2_avoided_kg": 3.8,
    }
    headers = {
        "X-API-Key": "test-circular-api-key-2026",
        "Idempotency-Key": "ASM-RETRY-TEST",
        "Content-Type": "application/json",
    }

    # First attempt
    resp1 = client.post("/api/v1/assessment-results", headers=headers, json=payload)
    assert resp1.status_code == 200

    # Second attempt (retry)
    resp2 = client.post("/api/v1/assessment-results", headers=headers, json=payload)
    assert resp2.status_code == 200
    data2 = resp2.get_json()
    assert data2["success"] is True
    assert data2.get("idempotent") is True or data2.get("status") == "success"

    with app.app_context():
        # Ensure only 1 AssessmentResult exists for this device
        asmts = AssessmentResult.query.filter_by(device_id=retry_id).all()
        assert len(asmts) == 1

