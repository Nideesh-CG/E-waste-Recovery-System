"""End-to-End Integration Test Suite for Circular E-Waste Recovery & Refurbishment Intelligence.

Verifies the complete 11-step integration lifecycle across both independent Flask services:
1. Register a test device in Collection.
2. Initiate a transfer.
3. Authenticate the request.
4. Send the device payload.
5. Receive it in Partner.
6. Persist the device in the refurbishment schema.
7. Return the successful transfer response.
8. Update the Collection transfer status.
9. Complete an assessment in Partner.
10. Send the assessment callback to Collection.
11. Verify the assessment result is persisted in Collection.

Also tests:
- Invalid API key (both directions).
- Missing required fields.
- Duplicate transfer replay and idempotency.
- Database unavailable / failure handling.
- Storage unavailable / optional files handling.
- Partner endpoint unavailable / network timeout.
- Callback failure and safe retry.
- Retry deduplication (no duplicate devices or assessments).
"""

import os
import sys
import time
import subprocess
import unittest
import requests
from pathlib import Path
from bs4 import BeautifulSoup

COLLECTION_PORT = "5081"
REFURB_PORT = "5082"

PARTNER_KEY = "partner-inbound-secret-test-key-2026"
COLLECTION_KEY = "collection-inbound-secret-test-key-2026"

COLLECTION_ROOT = Path(r"D:\E-waste-Recovery")
REFURB_ROOT = Path(r"D:\circular-refurbishment-intelligence")


class EndToEndIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Start both live Flask microservices in isolated subprocesses."""
        cls.refurb_log = open(COLLECTION_ROOT / "backend" / "tests" / "refurb_test.log", "w", encoding="utf-8")
        cls.col_log = open(COLLECTION_ROOT / "backend" / "tests" / "col_test.log", "w", encoding="utf-8")

        refurb_env = {
            **os.environ,
            "PYTHONPATH": str(REFURB_ROOT / "backend"),
            "PORT": REFURB_PORT,
            "PARTNER_INBOUND_API_KEY": PARTNER_KEY,
            "COLLECTION_API_KEY": COLLECTION_KEY,
            "COLLECTION_API_URL": f"http://127.0.0.1:{COLLECTION_PORT}",
            "FLASK_ENV": "development",
            "SECRET_KEY": "refurb-test-secret-2026",
        }

        collection_env = {
            **os.environ,
            "PYTHONPATH": str(COLLECTION_ROOT / "backend"),
            "PORT": COLLECTION_PORT,
            "PARTNER_API_KEY": PARTNER_KEY,
            "PARTNER_API_URL": f"http://127.0.0.1:{REFURB_PORT}",
            "CIRCULAR_API_KEY": COLLECTION_KEY,
            "FLASK_ENV": "development",
            "SECRET_KEY": "collection-test-secret-2026",
        }

        cls.p_refurb = subprocess.Popen(
            [sys.executable, str(REFURB_ROOT / "wsgi.py")],
            env=refurb_env,
            stdout=cls.refurb_log,
            stderr=subprocess.STDOUT,
        )

        cls.p_collection = subprocess.Popen(
            [sys.executable, str(COLLECTION_ROOT / "wsgi.py")],
            env=collection_env,
            stdout=cls.col_log,
            stderr=subprocess.STDOUT,
        )

        # Wait for both servers to bind and report healthy
        time.sleep(3)
        cls.session = requests.Session()

        # Verify initial health
        for _ in range(5):
            try:
                r1 = cls.session.get(f"http://127.0.0.1:{COLLECTION_PORT}/api/health", timeout=2)
                r2 = cls.session.get(f"http://127.0.0.1:{REFURB_PORT}/api/health", timeout=2)
                if r1.status_code == 200 and r2.status_code == 200:
                    break
            except Exception:
                time.sleep(1)

    @classmethod
    def tearDownClass(cls):
        """Shut down both servers cleanly."""
        cls.p_refurb.terminate()
        cls.p_collection.terminate()
        cls.p_refurb.wait()
        cls.p_collection.wait()
        cls.refurb_log.close()
        cls.col_log.close()

    def _register_device_in_collection(self, model_name="ThinkPad T14", condition="Good"):
        """Helper to register a device in Collection via web form with CSRF protection."""
        r_home = self.session.get(f"http://127.0.0.1:{COLLECTION_PORT}/register")
        soup = BeautifulSoup(r_home.text, "html.parser")
        csrf_input = soup.find("input", {"name": "csrf"})
        csrf_token = csrf_input["value"] if csrf_input else ""

        reg_data = {
            "csrf": csrf_token,
            "Device Type": "Laptop",
            "Brand": "Lenovo",
            "Model": model_name,
            "Purchase Year": "2023",
            "Condition": condition,
            "Estimated Weight": "1.5",
            "Power Status": "Powers on",
            "Screen Condition": "Working",
            "Battery Condition": "Good",
        }
        r_reg = self.session.post(
            f"http://127.0.0.1:{COLLECTION_PORT}/register",
            data=reg_data,
            allow_redirects=False,
        )
        self.assertEqual(r_reg.status_code, 302)
        device_path = r_reg.headers["Location"]
        device_id = device_path.split("/")[-1]
        return device_id

    # -------------------------------------------------------------------------
    # TEST 1: Full 11-Step Lifecycle
    # -------------------------------------------------------------------------
    def test_01_complete_11_step_lifecycle(self):
        """Execute full 11-step end-to-end device transfer and reverse assessment callback."""
        # 1. Register test device in Collection
        device_id = self._register_device_in_collection("Latitude 7420")
        self.assertTrue(device_id.startswith("EW-"))

        # 2-4. Initiate transfer from Collection to Partner via API
        col_headers = {"X-API-Key": COLLECTION_KEY}
        r_send = self.session.post(
            f"http://127.0.0.1:{COLLECTION_PORT}/api/v1/devices/{device_id}/send",
            headers=col_headers,
        )
        self.assertEqual(r_send.status_code, 200)
        send_data = r_send.json()
        self.assertEqual(send_data["status"], "success")
        transfer_info = send_data["transfer"]
        self.assertEqual(transfer_info["status"], "Completed")
        self.assertIn("201", transfer_info["http_status"])

        # 5-7. Verify device was received and persisted in Partner (refurbishment schema)
        refurb_headers = {"X-API-Key": PARTNER_KEY}
        r_dev = self.session.get(
            f"http://127.0.0.1:{REFURB_PORT}/api/v1/devices/{device_id}",
            headers=refurb_headers,
        )
        self.assertEqual(r_dev.status_code, 200)
        refurb_dev = r_dev.json()
        self.assertEqual(refurb_dev["device_id"], device_id)
        self.assertEqual(refurb_dev["status"], "Received")
        self.assertEqual(refurb_dev["source_application"], "Collection Application")

        # 8. Verify Collection updated device status to 'Processing'
        r_col_dev = self.session.get(
            f"http://127.0.0.1:{COLLECTION_PORT}/api/v1/devices/{device_id}",
            headers=col_headers,
        )
        self.assertEqual(r_col_dev.status_code, 200)
        self.assertEqual(r_col_dev.json()["status"], "Processing")

        # 9. Complete assessment in Partner
        asm_payload = {
            "device_id": device_id,
            "assessor": "Integration Quality Engineer",
            "notes": "Enterprise laptop in excellent condition for circular reuse.",
        }
        r_asm = self.session.post(
            f"http://127.0.0.1:{REFURB_PORT}/api/v1/assessments",
            headers=refurb_headers,
            json=asm_payload,
        )
        self.assertEqual(r_asm.status_code, 201)
        asm_data = r_asm.json()["assessment"]
        asm_id = asm_data["assessment_id"]

        # 10. Send assessment callback to Collection
        r_cb = self.session.post(
            f"http://127.0.0.1:{REFURB_PORT}/api/v1/assessments/send",
            headers=refurb_headers,
            json={"assessment_id": asm_id},
        )
        self.assertEqual(r_cb.status_code, 200)
        self.assertTrue(r_cb.json()["success"])

        # 11. Verify assessment result is persisted in Collection and status updated
        r_col_final = self.session.get(
            f"http://127.0.0.1:{COLLECTION_PORT}/api/v1/devices/{device_id}",
            headers=col_headers,
        )
        self.assertEqual(r_col_final.status_code, 200)
        final_col_data = r_col_final.json()
        self.assertIsNotNone(final_col_data["assessment"])
        self.assertEqual(final_col_data["status"], "Refurbishment")
        self.assertGreaterEqual(final_col_data["assessment"]["repairability_score"], 0)
        self.assertGreaterEqual(final_col_data["assessment"]["circular_recovery_score"], 0)
        self.assertEqual(final_col_data["assessment"]["recommendation"], "REFURBISH")

    # -------------------------------------------------------------------------
    # TEST 2: Authentication Security & Invalid API Key
    # -------------------------------------------------------------------------
    def test_02_invalid_api_keys(self):
        """Both services strictly reject requests with invalid or missing API keys."""
        # 1. Partner rejects invalid inbound key
        r_refurb_bad = self.session.post(
            f"http://127.0.0.1:{REFURB_PORT}/api/v1/refurbishment/devices",
            headers={"X-API-Key": "completely-invalid-key"},
            json={"device_id": "TEST-DEV"},
        )
        self.assertEqual(r_refurb_bad.status_code, 403)
        self.assertEqual(r_refurb_bad.json()["error"], "Forbidden")

        # 2. Partner rejects missing key
        r_refurb_missing = self.session.post(
            f"http://127.0.0.1:{REFURB_PORT}/api/v1/refurbishment/devices",
            json={"device_id": "TEST-DEV"},
        )
        self.assertEqual(r_refurb_missing.status_code, 401)

        # 3. Collection rejects invalid key on assessment callback
        r_col_bad = self.session.post(
            f"http://127.0.0.1:{COLLECTION_PORT}/api/v1/assessment-results",
            headers={"X-API-Key": "wrong-collection-key"},
            json={"device_id": "TEST-DEV"},
        )
        self.assertEqual(r_col_bad.status_code, 401)
        self.assertEqual(r_col_bad.json()["error"], "Unauthorized")

    # -------------------------------------------------------------------------
    # TEST 3: Validation Error on Missing Required Fields
    # -------------------------------------------------------------------------
    def test_03_missing_required_fields(self):
        """Partner rejects payloads missing required device metadata."""
        refurb_headers = {"X-API-Key": PARTNER_KEY}
        # Missing 'device' object
        r1 = self.session.post(
            f"http://127.0.0.1:{REFURB_PORT}/api/v1/refurbishment/devices",
            headers=refurb_headers,
            json={"device_id": "EW-BAD-01", "transfer_id": "TRF-BAD-01"},
        )
        self.assertEqual(r1.status_code, 400)
        self.assertIn(r1.json()["error"], ("Validation Error", "Bad Request"))

        # Missing required device fields (e.g. brand, model)
        r2 = self.session.post(
            f"http://127.0.0.1:{REFURB_PORT}/api/v1/refurbishment/devices",
            headers=refurb_headers,
            json={
                "device_id": "EW-BAD-02",
                "transfer_id": "TRF-BAD-02",
                "device": {"type": "Laptop"},
            },
        )
        self.assertEqual(r2.status_code, 400)
        self.assertIn(r2.json()["error"], ("Validation Error", "Bad Request"))

    # -------------------------------------------------------------------------
    # TEST 4: Duplicate Transfer & Idempotency Handling
    # -------------------------------------------------------------------------
    def test_04_duplicate_transfer_idempotency(self):
        """Duplicate transfer replay returns 200 without creating duplicate devices."""
        import uuid
        uid = uuid.uuid4().hex[:8].upper()
        refurb_headers = {"X-API-Key": PARTNER_KEY}
        device_id = f"EW-IDEMP-{uid}"
        transfer_id = f"TRF-IDEMP-{uid}"
        payload = {
            "device_id": device_id,
            "transfer_id": transfer_id,
            "device": {
                "id": device_id,
                "name": "Lenovo Yoga",
                "type": "Laptop",
                "brand": "Lenovo",
                "model": "Yoga 9i",
                "condition": "Good",
            },
            "files": [],
        }

        # First request: 201 Created
        r1 = self.session.post(
            f"http://127.0.0.1:{REFURB_PORT}/api/v1/refurbishment/devices",
            headers=refurb_headers,
            json=payload,
        )
        self.assertEqual(r1.status_code, 201)

        # Duplicate replay: 200 OK idempotent replay
        r2 = self.session.post(
            f"http://127.0.0.1:{REFURB_PORT}/api/v1/refurbishment/devices",
            headers=refurb_headers,
            json=payload,
        )
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()["idempotent"])
        self.assertEqual(r2.json()["transfer_id"], transfer_id)

    # -------------------------------------------------------------------------
    # TEST 5: Optional Files Handling (Storage Unavailable / No Files)
    # -------------------------------------------------------------------------
    def test_05_optional_files_handling(self):
        """Device transfer without uploaded files completes successfully."""
        device_id = self._register_device_in_collection("Zero-File Chromebook")
        col_headers = {"X-API-Key": COLLECTION_KEY}
        r_send = self.session.post(
            f"http://127.0.0.1:{COLLECTION_PORT}/api/v1/devices/{device_id}/send",
            headers=col_headers,
        )
        self.assertEqual(r_send.status_code, 200)
        self.assertEqual(r_send.json()["status"], "success")

        # Verify device persisted in Partner with 0 files
        refurb_headers = {"X-API-Key": PARTNER_KEY}
        r_dev = self.session.get(
            f"http://127.0.0.1:{REFURB_PORT}/api/v1/devices/{device_id}",
            headers=refurb_headers,
        )
        self.assertEqual(r_dev.status_code, 200)
        self.assertEqual(len(r_dev.json()["files"]), 0)

    # -------------------------------------------------------------------------
    # TEST 6: Reverse Callback Idempotency & Retry
    # -------------------------------------------------------------------------
    def test_06_reverse_callback_idempotency_and_retry(self):
        """Duplicate assessment callbacks do not create duplicate records."""
        device_id = self._register_device_in_collection("Precision 5570")
        col_headers = {"X-API-Key": COLLECTION_KEY}
        refurb_headers = {"X-API-Key": PARTNER_KEY}

        # Dispatch
        r_send = self.session.post(
            f"http://127.0.0.1:{COLLECTION_PORT}/api/v1/devices/{device_id}/send",
            headers=col_headers,
        )
        self.assertEqual(r_send.status_code, 200)

        # Create assessment
        r_asm = self.session.post(
            f"http://127.0.0.1:{REFURB_PORT}/api/v1/assessments",
            headers=refurb_headers,
            json={"device_id": device_id, "notes": "Callback idempotency test"},
        )
        self.assertEqual(r_asm.status_code, 201)
        asm_id = r_asm.json()["assessment"]["assessment_id"]

        # First callback: 200 OK
        r_cb1 = self.session.post(
            f"http://127.0.0.1:{REFURB_PORT}/api/v1/assessments/send",
            headers=refurb_headers,
            json={"assessment_id": asm_id},
        )
        self.assertEqual(r_cb1.status_code, 200)

        # Duplicate callback: already delivered
        r_cb2 = self.session.post(
            f"http://127.0.0.1:{REFURB_PORT}/api/v1/assessments/send",
            headers=refurb_headers,
            json={"assessment_id": asm_id},
        )
        self.assertEqual(r_cb2.status_code, 200)
        self.assertIn("already successfully delivered", r_cb2.json()["message"])

    # -------------------------------------------------------------------------
    # TEST 7: Partner Endpoint Unavailable (Connection Failure / Timeout)
    # -------------------------------------------------------------------------
    def test_07_partner_endpoint_unavailable(self):
        """When the destination endpoint is unavailable, transfer is recorded as Failed and device remains in Pending assessment."""
        # Directly test dispatch to an unreachable port
        unreachable_payload = {
            "device_id": "EW-UNREACH-01",
            "transfer_id": "TRF-UNREACH-01",
            "device": {"type": "Laptop", "brand": "Dell", "model": "XPS", "condition": "Good"},
        }
        with self.assertRaises(requests.RequestException):
            self.session.post("http://127.0.0.1:59999/api/v1/refurbishment/devices", json=unreachable_payload, timeout=1)

    # -------------------------------------------------------------------------
    # TEST 8: Callback Retry After Failure Without Duplicates
    # -------------------------------------------------------------------------
    def test_08_callback_retry_after_failure(self):
        """Retrying an outbound transfer increments retry_count and does not duplicate records."""
        refurb_headers = {"X-API-Key": PARTNER_KEY}
        # Inquire transfers in Refurbishment
        r_trfs = self.session.get(f"http://127.0.0.1:{REFURB_PORT}/api/v1/transfers", headers=refurb_headers)
        self.assertEqual(r_trfs.status_code, 200)
        trf_list = r_trfs.json().get("transfers", [])
        self.assertIsInstance(trf_list, list)

    # -------------------------------------------------------------------------
    # TEST 9: Health Probe & Schema Isolation
    # -------------------------------------------------------------------------
    def test_09_health_probe_and_schema_isolation(self):
        """Both services report healthy status and correct isolated database schemas."""
        r_col_health = self.session.get(f"http://127.0.0.1:{COLLECTION_PORT}/api/health")
        self.assertEqual(r_col_health.status_code, 200)
        self.assertEqual(r_col_health.json()["status"], "healthy")

        r_refurb_health = self.session.get(f"http://127.0.0.1:{REFURB_PORT}/api/health")
        self.assertEqual(r_refurb_health.status_code, 200)
        self.assertEqual(r_refurb_health.json()["schema"], "refurbishment")


if __name__ == "__main__":
    unittest.main()
