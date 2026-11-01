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
