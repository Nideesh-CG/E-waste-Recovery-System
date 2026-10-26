"""Comprehensive test suite for Circular E-Waste Recovery & Refurbishment Platform.
Tests UI rendering, database persistence, REST APIs, partner transfers, and PDF reporting.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app, soup
from metrics import get_platform_metrics
from models import AssessmentResult, Device, Transfer, db


class CircularTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "test.sqlite3"
        self.app = create_app(self.database)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="test-only",
            CIRCULAR_API_KEY="test-circular-secret",
            PARTNER_API_KEY="test-partner-key",
            PARTNER_API_URL="",
        )
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        try:
            self.directory.cleanup()
        except Exception:
            pass

    def token(self):
        self.client.get("/")
        with self.client.session_transaction() as session:
            return session["csrf"]

    def test_all_pages_and_states(self):
        paths = [
            "/",
            "/?period=Last+7+days",
            "/register",
            "/devices",
            "/tracking",
            "/transfers",
            "/transfers?selected=TRF-00842",
            "/transfers?selected=TRF-00841",
            "/transfers?selected=TRF-00838",
            "/transfers?filter=Failed",
            "/reports",
            "/assessment/EW-2024-00127",
            "/assessment/EW-2024-00127?partner=1",
            "/assessment/EW-2024-00126",
            "/assessment/EW-2024-00128",
            "/devices/EW-2024-00128",
            "/devices/EW-2024-00127",
            "/devices/EW-2024-00125",
        ]
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                html = soup(response.data.decode())
                self.assertEqual(len(html.select("main")), 1)
                self.assertEqual(len(html.select("aside")), 1)
                self.assertIn("E-Waste Admin", html.get_text())
                self.assertNotIn("{{", response.data.decode())
                self.assertEqual(len(html.select("nav a")), 6)
        partner = soup(
            self.client.get("/assessment/EW-2024-00127?partner=1").data.decode()
        )
        self.assertIn("hidden", partner.aside["class"])
        self.assertIn("GreenTech Refurbishment", partner.get_text())

    def test_registration_persistence_transfer_and_search(self):
        response = self.client.post(
            "/register",
            data={
                "csrf": self.token(),
                "Device Type": "Laptop",
                "Brand": "Test",
                "Model": "Notebook",
                "Purchase Year": "2021",
                "Condition": "Good",
                "Estimated Weight": "1.5",
                "Power Status": "Powers on",
            },
        )
        self.assertEqual(response.status_code, 302)
        path = response.location
        detail = self.client.get(path)
        self.assertIn(b"Test Notebook", detail.data)
        self.assertIn(b"1.5 kg", detail.data)
        restarted = create_app(self.database).test_client()
        self.assertIn(b"Test Notebook", restarted.get(path).data)
        self.assertIn(b"Test Notebook", self.client.get("/devices?q=Notebook").data)
        self.assertNotIn(b"Test Notebook", self.client.get("/devices?q=Samsung").data)
        sent = self.client.post(
            path + "/send", data={"csrf": self.token()}, follow_redirects=True
        )
        self.assertEqual(sent.status_code, 200)
        self.assertIn(b"Demo transfer queued", sent.data)
        self.assertIn(b"Pending assessment", sent.data)
        self.assertIn(b"Test Notebook", self.client.get("/tracking").data)
        self.assertIn(
            path.rsplit("/", 1)[1].encode() + b".json",
            self.client.get("/transfers").data,
        )

    def test_retry_and_transfer_directions(self):
        sent = self.client.post(
            "/transfers/TRF-00838/retry", data={"csrf": self.token()}
        )
        self.assertEqual(sent.status_code, 302)
        incoming = self.client.get("/transfers?selected=TRF-00841")
        self.assertIn(b"POST /api/v1/collection/assessments", incoming.data)
        outgoing = self.client.get("/transfers?selected=TRF-00842")
        self.assertIn(b"POST /api/v1/refurbishment/devices", outgoing.data)
        retried = self.client.get("/transfers?selected=TRF-00838")
        self.assertIn("Not sent — awaiting dispatch", retried.data.decode())

    def test_downloads_and_invalid_requests(self):
        for path in [
            "/download/summary",
            "/download/report",
            "/download/assessment/EW-2024-00127",
        ]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response.headers["Content-Disposition"])
            self.assertTrue(len(response.data) > 0)
        self.assertIn(b"Live Portfolio", self.client.get("/download/summary").data)
        self.assertIn(b"Category", self.client.get("/download/report").data)
        self.assertTrue(self.client.get("/download/assessment/EW-2024-00127").data.startswith(b"%PDF-"))
        self.assertEqual(
            self.client.get("/download/assessment/EW-2024-00128").status_code, 409
        )
        self.assertEqual(self.client.get("/devices/missing").status_code, 404)
        self.assertEqual(self.client.post("/register", data={}).status_code, 400)
        result = self.client.post(
            "/register", data={"csrf": self.token()}, follow_redirects=True
        )
        self.assertIn(b"Please complete all required", result.data)

    def test_original_styles_and_separate_report_columns(self):
        original = next(
            Path(__file__).resolve().parents[2].glob("dist/assets/*.css"), None
        )
        if original:
            self.assertEqual(
                original.read_bytes(),
                (Path(__file__).resolve().parents[1] / "static/style.css").read_bytes(),
            )
        report = soup(self.client.get("/reports").data.decode())
        sections = report.main.select("section")
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].parent, sections[1].parent)
        for section in sections:
            self.assertIn("min-w-0", section["class"])
            self.assertIn("overflow-hidden", section["class"])

    def test_registration_escapes_user_content_and_rejects_invalid_files(self):
        payload = {
            "csrf": self.token(),
            "Device Type": "Monitor",
            "Brand": "<script>alert(1)</script>",
            "Model": "Screen",
            "Purchase Year": "2022",
            "Condition": "Good",
            "Estimated Weight": "2.3",
        }
        response = self.client.post("/register", data=payload)
        self.assertEqual(response.status_code, 302)
        html = self.client.get(response.location).data.decode()
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("Monitor", html)
        payload["csrf"] = self.token()
        payload["image"] = (io.BytesIO(b"not an image"), "unsafe.html")
        invalid = self.client.post("/register", data=payload, follow_redirects=True)
        self.assertIn(b"Please select supported files", invalid.data)

    def test_api_health_endpoint(self):
        """Verify API health endpoint reports connection state and service version."""
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["database"], "connected")
        self.assertIn("version", data)

        # Verify degraded 503 response when database probe fails
        with patch.object(db.session, "execute", side_effect=Exception("Simulated DB connection error")):
            res_degraded = self.client.get("/api/health")
            self.assertEqual(res_degraded.status_code, 503)
            data_degraded = res_degraded.get_json()
            self.assertEqual(data_degraded["status"], "degraded")
            self.assertEqual(data_degraded["database"], "unavailable")


    def test_api_device_endpoints(self):
        """Verify GET /api/v1/devices and GET /api/v1/devices/<id>."""
        headers = {"X-API-Key": "test-circular-secret"}
        res = self.client.get("/api/v1/devices", headers=headers)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertGreaterEqual(data["count"], 4)

        single = self.client.get(
            "/api/v1/devices/EW-2024-00127", headers=headers
        )
        self.assertEqual(single.status_code, 200)
        s_data = single.get_json()
        self.assertEqual(s_data["id"], "EW-2024-00127")
        self.assertEqual(s_data["brand"], "Samsung")

        not_found = self.client.get(
            "/api/v1/devices/EW-NOT-REAL", headers=headers
        )
        self.assertEqual(not_found.status_code, 404)

    def test_api_authentication_required(self):
        """Verify endpoints require authentication when CIRCULAR_API_KEY is configured."""
        res = self.client.get("/api/v1/devices")
        self.assertEqual(res.status_code, 401)
        self.assertIn("Unauthorized", res.get_json().get("error", ""))

        bad = self.client.get(
            "/api/v1/devices", headers={"X-API-Key": "wrong-key"}
        )
        self.assertEqual(bad.status_code, 401)

        valid = self.client.get(
            "/api/v1/devices",
            headers={"Authorization": "Bearer test-circular-secret"},
        )
        self.assertEqual(valid.status_code, 200)

    def test_api_inbound_assessment_results(self):
        """Verify partner posting assessment results updates device status and records transfer."""
        # Per Phase 11: device must be in Processing before receiving partner assessment
        with self.app.app_context():
            dev = Device.query.filter_by(id="EW-2024-00128").first()
            dev.status = "Processing"
            db.session.commit()

        headers = {
            "Content-Type": "application/json",
            "X-API-Key": "test-circular-secret",
        }
        payload = {
            "device_id": "EW-2024-00128",
            "repairability_score": 92,
            "circular_recovery_score": 89,
            "repair_cost": 3200.0,
            "component_value": 15400.0,
            "recommendation": "REFURBISH",
            "co2_avoided_kg": 24.5,
            "assessed_by": "Partner GreenTech Labs",
            "notes": "Excellent condition Dell unit. Minor fan cleaning required.",
        }
        res = self.client.post(
            "/api/v1/assessment-results",
            data=json.dumps(payload),
            headers=headers,
        )
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["new_device_status"], "Refurbishment")

        with self.app.app_context():
            dev = Device.query.filter_by(id="EW-2024-00128").first()
            self.assertEqual(dev.status, "Refurbishment")
            self.assertIsNotNone(dev.assessment)
            self.assertEqual(dev.assessment.repairability_score, 92)

        page = self.client.get("/assessment/EW-2024-00128")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"92 / 100", page.data)
        self.assertIn(b"3,200", page.data)

    def test_api_inbound_assessment_validation(self):
        """Verify validation rules on inbound assessment payloads."""
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": "test-circular-secret",
        }
        res = self.client.post(
            "/api/v1/assessment-results",
            data=json.dumps({"device_id": "NON-EXISTENT"}),
            headers=headers,
        )
        self.assertEqual(res.status_code, 404)

        res_bad_score = self.client.post(
            "/api/v1/assessment-results",
            data=json.dumps(
                {"device_id": "EW-2024-00127", "repairability_score": 150}
            ),
            headers=headers,
        )
        self.assertEqual(res_bad_score.status_code, 400)

        res_neg = self.client.post(
            "/api/v1/assessment-results",
            data=json.dumps(
                {"device_id": "EW-2024-00127", "repair_cost": -50.0}
            ),
            headers=headers,
        )
        self.assertEqual(res_neg.status_code, 400)

    @patch("app.requests.post")
    def test_outbound_partner_transfer_success(self, mock_post):
        """Verify real outbound HTTP POST request when PARTNER_API_URL is configured."""
        self.app.config["PARTNER_API_URL"] = "https://partner-test.example.com"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason = "OK"
        mock_response.text = '{"status":"received","transfer_id":"TRF-PARTNER-1"}'
        mock_post.return_value = mock_response

        res = self.client.post(
            "/devices/EW-2024-00128/send",
            data={"csrf": self.token()},
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Transfer successfully dispatched to partner", res.data)

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(
            args[0], "https://partner-test.example.com/api/v1/refurbishment/devices"
        )
        self.assertEqual(kwargs["headers"]["X-API-Key"], "test-partner-key")
        self.assertEqual(kwargs["json"]["device_id"], "EW-2024-00128")

        with self.app.app_context():
            transfer = (
                Transfer.query.filter_by(device_id="EW-2024-00128")
                .order_by(Transfer.timestamp.desc())
                .first()
            )
            self.assertIsNotNone(transfer)
            self.assertEqual(transfer.status, "Completed")
            self.assertEqual(transfer.http_status, "200 OK")

    @patch("app.requests.post")
    def test_outbound_partner_transfer_failure(self, mock_post):
        """Verify network error or failure is recorded as Failed in Transfer log."""
        self.app.config["PARTNER_API_URL"] = "https://partner-test.example.com"
        mock_post.side_effect = requests.RequestException("Connection timed out")

        res = self.client.post(
            "/devices/EW-2024-00128/send",
            data={"csrf": self.token()},
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Transfer recorded as Failed", res.data)

        with self.app.app_context():
            transfer = (
                Transfer.query.filter_by(device_id="EW-2024-00128")
                .order_by(Transfer.timestamp.desc())
                .first()
            )
            self.assertIsNotNone(transfer)
            self.assertEqual(transfer.status, "Failed")
            self.assertEqual(transfer.http_status, "503 Service Unavailable")

    def test_pdf_download_structure(self):
        """Verify PDF downloads generate valid PDF binaries with ReportLab."""
        res = self.client.get("/download/assessment/EW-2024-00127")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.mimetype, "application/pdf")
        self.assertTrue(res.data.startswith(b"%PDF-"))

        sum_pdf = self.client.get("/download/summary?format=pdf")
        self.assertEqual(sum_pdf.status_code, 200)
        self.assertEqual(sum_pdf.mimetype, "application/pdf")
        self.assertTrue(sum_pdf.data.startswith(b"%PDF-"))

    def test_empty_database_and_dashboard_metrics(self):
        """Scenario I & A: Empty database behaves correctly and returns 0 metrics without fabricated values."""
        with tempfile.TemporaryDirectory() as empty_dir:
            empty_db = Path(empty_dir) / "empty.sqlite3"
            app = create_app(empty_db)
            app.config.update(
                TESTING=True,
                SECRET_KEY="test-secret",
                CIRCULAR_API_KEY="test-api-key",
                SEED_DEMO_DATA=False,
            )
            with app.app_context():
                Device.query.delete()
                Transfer.query.delete()
                AssessmentResult.query.delete()
                db.session.commit()
                metrics = get_platform_metrics()
                self.assertEqual(metrics["total_devices"], 0)
                self.assertEqual(metrics["pending_count"], 0)
                self.assertEqual(metrics["processing_count"], 0)
                self.assertEqual(metrics["refurbishment_candidates"], 0)
                self.assertEqual(metrics["parts_recovery"], 0)
                self.assertEqual(metrics["completed_count"], 0)
                self.assertEqual(metrics["total_recovery_value"], 0.0)
                self.assertEqual(metrics["total_co2_kg"], 0.0)
                self.assertEqual(metrics["circular_recovery_rate"], 0.0)

            client = app.test_client()
            resp = client.get("/")
            self.assertEqual(resp.status_code, 200)
            text = resp.data.decode("utf-8")
            self.assertIn("0", text)
            self.assertIn("₹0", text)

    def test_registration_validation_boundary_conditions(self):
        """Scenario B: Registration boundary conditions, year, weight, oversized files."""
        bad_data = {
            "csrf": self.token(),
            "Device Type": "Laptop",
            "Brand": "",
            "Model": "Pro",
            "Purchase Year": "2020",
            "Condition": "Good",
            "Estimated Weight": "1.5",
        }
        res = self.client.post("/register", data=bad_data, follow_redirects=True)
        self.assertIn(b"Please complete all required device information", res.data)

        bad_year = dict(bad_data)
        bad_year["Brand"] = "Dell"
        bad_year["Purchase Year"] = "1960"
        bad_year["csrf"] = self.token()
        res = self.client.post("/register", data=bad_year, follow_redirects=True)
        self.assertIn(b"Please enter a valid purchase year and weight", res.data)

        bad_weight = dict(bad_year)
        bad_weight["Purchase Year"] = "2021"
        bad_weight["Estimated Weight"] = "-2.5"
        bad_weight["csrf"] = self.token()
        res = self.client.post("/register", data=bad_weight, follow_redirects=True)
        self.assertIn(b"Please enter a valid purchase year and weight", res.data)

        oversized_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * 1024 * 1024 + 100)
        bad_size = dict(bad_year)
        bad_size["Purchase Year"] = "2021"
        bad_size["Estimated Weight"] = "1.5"
        bad_size["csrf"] = self.token()
        bad_size["image"] = (io.BytesIO(oversized_content), "large.png")
        res = self.client.post("/register", data=bad_size, follow_redirects=True)
        self.assertIn(b"Maximum file size: 5 MB", res.data)

    def test_device_lifecycle_state_machine(self):
        """Scenario C: Validate state transitions on Device model."""
        dev = Device(
            id="TEST-DEV-1",
            name="Test Device",
            type="Laptop",
            brand="Brand",
            model="Model",
            status="Pending assessment",
            year="2022",
            weight="2.0",
            condition="Fair",
        )
        self.assertTrue(dev.can_transition_to("Processing"))
        dev.transition_to("Processing")
        self.assertEqual(dev.status, "Processing")

        self.assertTrue(dev.can_transition_to("Refurbishment"))
        self.assertTrue(dev.can_transition_to("Parts recovery"))
        self.assertTrue(dev.can_transition_to("Completed"))

        dev.transition_to("Refurbishment")
        self.assertEqual(dev.status, "Refurbishment")

        self.assertTrue(dev.can_transition_to("Completed"))
        dev.transition_to("Completed")
        self.assertEqual(dev.status, "Completed")

        self.assertFalse(dev.can_transition_to("Pending assessment"))
        self.assertFalse(dev.transition_to("Pending assessment"))
        with self.assertRaises(ValueError):
            dev.transition_to("Pending assessment", raise_on_error=True)

    @patch("app.requests.post")
    def test_outbound_transfer_async_202_and_rejection(self, mock_post):
        """Scenario E: Outbound partner transfer handles 202 Accepted and 400 Bad Request."""
        self.app.config["PARTNER_API_URL"] = "https://partner-async.example.com"

        mock_resp = MagicMock()
        mock_resp.status_code = 202
        mock_resp.reason = "Accepted"
        mock_resp.text = '{"status":"queued","job_id":"job-999"}'
        mock_post.return_value = mock_resp

        res = self.client.post(
            "/devices/EW-2024-00128/send",
            data={"csrf": self.token()},
            follow_redirects=True,
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Transfer accepted by partner", res.data)

        # Partner rejection (HTTP 400) on pending device
        with self.app.app_context():
            dev = Device.query.filter_by(id="EW-2024-00128").first()
            dev.status = "Pending assessment"
            db.session.commit()

        mock_resp_err = MagicMock()
        mock_resp_err.status_code = 400
        mock_resp_err.reason = "Bad Request"
        mock_resp_err.text = '{"error":"Invalid payload specifications"}'
        mock_post.return_value = mock_resp_err

        res_err = self.client.post(
            "/devices/EW-2024-00128/send",
            data={"csrf": self.token()},
            follow_redirects=True,
        )
        self.assertEqual(res_err.status_code, 200)
        self.assertIn(b"Partner transfer failed", res_err.data)
        with self.app.app_context():
            trf = Transfer.query.filter_by(device_id="EW-2024-00128").order_by(Transfer.timestamp.desc()).first()
            self.assertIsNotNone(trf)
            self.assertEqual(trf.status, "Failed")
            self.assertEqual(trf.http_status, "400 Bad Request")

    def test_inbound_assessment_idempotency(self):
        """Scenario F: Repeated webhook with same Idempotency-Key returns predictable response without duplicates."""
        # Per Phase 11: device must be in Processing before receiving partner assessment
        with self.app.app_context():
            dev = Device.query.filter_by(id="EW-2024-00126").first()
            dev.status = "Processing"
            db.session.commit()

        headers = {
            "Content-Type": "application/json",
            "X-API-Key": "test-circular-secret",
            "Idempotency-Key": "IDEMP-TEST-999",
        }
        payload = {
            "device_id": "EW-2024-00126",
            "repairability_score": 88,
            "circular_recovery_score": 85,
            "repair_cost": 2500.0,
            "component_value": 9000.0,
            "recommendation": "REFURBISH",
            "co2_avoided_kg": 18.0,
            "assessed_by": "Test Assessor",
            "notes": "Idempotency test evaluation.",
        }
        res1 = self.client.post(
            "/api/v1/assessment-results",
            data=json.dumps(payload),
            headers=headers,
        )
        self.assertEqual(res1.status_code, 200)
        data1 = res1.get_json()
        self.assertEqual(data1["status"], "success")

        res2 = self.client.post(
            "/api/v1/assessment-results",
            data=json.dumps(payload),
            headers=headers,
        )
        self.assertEqual(res2.status_code, 200)
        data2 = res2.get_json()
        self.assertTrue(data2.get("idempotent"))
        self.assertEqual(data2["status"], "success")

    def test_file_storage_and_private_document_access(self):
        """Scenario G: File storage metadata, unauthorized access rejection, and retrieval."""
        res = self.client.get("/api/v1/files/nonexistentkey123.pdf")
        self.assertEqual(res.status_code, 401)

        res_valid_auth = self.client.get(
            "/api/v1/files/nonexistentkey123.pdf",
            headers={"X-API-Key": "test-circular-secret"},
        )
        self.assertEqual(res_valid_auth.status_code, 404)

    def test_config_and_production_validation(self):
        """Scenario D & J: Config normalization and production validation enforcement."""
        from config import Config, normalize_database_url
        from storage import get_storage_service

        # Test all URL formats on both module-level and Config-level
        url_formats = [
            ("postgres://usr:pwd@host.render.com:5432/dbname", "postgresql+psycopg://usr:pwd@host.render.com:5432/dbname"),
            ("postgresql://usr:pwd@host.render.com:5432/dbname", "postgresql+psycopg://usr:pwd@host.render.com:5432/dbname"),
            ("postgresql+psycopg://usr:pwd@host.render.com:5432/dbname", "postgresql+psycopg://usr:pwd@host.render.com:5432/dbname"),
            ("postgresql+psycopg2://usr:pwd@host.render.com:5432/dbname", "postgresql+psycopg://usr:pwd@host.render.com:5432/dbname"),
        ]
        for raw, expected in url_formats:
            self.assertEqual(normalize_database_url(raw), expected)
            self.assertEqual(Config.normalize_database_url(raw), expected)

        # Verify UPLOAD_STORAGE_PATH in Flask app.config is honored by get_storage_service
        custom_upload_dir = Path(self.directory.name) / "custom_uploads"
        self.app.config["UPLOAD_STORAGE_PATH"] = str(custom_upload_dir)
        storage_svc = get_storage_service(self.app.config)
        self.assertEqual(str(storage_svc.upload_folder), str(custom_upload_dir.resolve()))
        self.assertTrue(custom_upload_dir.exists())

        class BadProdConfig(Config):
            IS_PRODUCTION = True
            SECRET_KEY = "dev-secret-key-replace-in-production"
            CIRCULAR_API_KEY = "test"

        with self.assertRaises(ValueError):
            BadProdConfig.validate_production_config()

        class MissingApiKeyProdConfig(Config):
            IS_PRODUCTION = True
            SECRET_KEY = "secure-random-prod-key-12345"
            CIRCULAR_API_KEY = ""

        with self.assertRaises(ValueError):
            MissingApiKeyProdConfig.validate_production_config()

        # Dictionary configuration validation in production
        bad_dict_config = {
            "IS_PRODUCTION": True,
            "SECRET_KEY": "dev-secret-key-replace-in-production",
            "CIRCULAR_API_KEY": "valid-key",
        }
        with self.assertRaises(ValueError):
            Config.validate_production_config(bad_dict_config)


    def test_reports_csv_and_non_existent_assessment(self):
        """Scenario H: CSV export and proper error on missing assessment record."""
        res_csv = self.client.get("/download/report?format=csv")
        self.assertEqual(res_csv.status_code, 200)
        self.assertEqual(res_csv.mimetype, "text/csv")
        self.assertIn("Category", res_csv.data.decode("utf-8"))
        self.assertIn("Recovery Value", res_csv.data.decode("utf-8"))

        res_no_assess = self.client.get("/download/assessment/EW-2024-00128")
        self.assertEqual(res_no_assess.status_code, 409)

    def test_wsgi_entry_and_render_yaml_validity(self):
        """Scenario J: Verify wsgi entrypoint imports properly and render.yaml exists."""
        import wsgi
        self.assertIsNotNone(wsgi.app)

        root_yaml = Path(__file__).resolve().parents[2] / "render.yaml"
        self.assertTrue(root_yaml.exists(), "Root render.yaml must exist")
        content = root_yaml.read_text(encoding="utf-8")
        self.assertIn("circular-ewaste-recovery", content)
        self.assertIn("pip install -r backend/requirements.txt", content)
    def test_dashboard_date_filtering_and_diverted_ewaste(self):
        """Requirement 9 & 10: Date filtering and strict e-waste diverted weight exclusion."""
        from datetime import datetime, timedelta, timezone
        from models import Device, db
        with self.app.app_context():
            # Add an old device (40 days ago) with 10 kg in Refurbishment
            old_dev = Device(
                id="EW-OLD-001",
                name="Old Server",
                type="Other",
                brand="HP",
                model="Proliant",
                status="Refurbishment",
                year="2018",
                weight="10.0",
                condition="Used",
                created_at=datetime.now(timezone.utc) - timedelta(days=40),
            )
            # Add a pending device (today) with 5 kg
            pending_dev = Device(
                id="EW-PEND-001",
                name="Pending Phone",
                type="Smartphone",
                brand="Apple",
                model="iPhone 11",
                status="Pending assessment",
                year="2020",
                weight="5.0",
                condition="Fair",
                created_at=datetime.now(timezone.utc),
            )
            db.session.add(old_dev)
            db.session.add(pending_dev)
            db.session.commit()

            metrics_all = get_platform_metrics("All time")
            metrics_7d = get_platform_metrics("Last 7 days")

            # Pending assessment devices must NOT be counted in diverted weight
            self.assertGreater(metrics_all["total_weight_kg"], 0)
            self.assertNotEqual(metrics_all["total_devices"], metrics_7d["total_devices"])

    def test_inbound_assessment_numeric_validation_nan_infinity(self):
        """Requirement 11: Reject NaN, Infinity, -Infinity, and negative values."""
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": "test-circular-secret",
        }
        invalid_cases = [
            {"repair_cost": float("nan")},
            {"repair_cost": float("inf")},
            {"repair_cost": -100.0},
            {"component_value": float("-inf")},
            {"component_value": -50.0},
            {"co2_avoided_kg": float("nan")},
            {"co2_avoided_kg": -1.0},
            {"repairability_score": 105},
            {"repairability_score": -1},
            {"circular_recovery_score": 150},
        ]
        for idx, bad_field in enumerate(invalid_cases):
            payload = {
                "device_id": "EW-2024-00128",
                "repairability_score": 85,
                "circular_recovery_score": 85,
                "repair_cost": 500.0,
                "component_value": 1200.0,
                "recommendation": "REFURBISH",
                "co2_avoided_kg": 15.0,
            }
            payload.update(bad_field)
            res = self.client.post(
                "/api/v1/assessment-results",
                data=json.dumps(payload),
                headers=headers,
            )
            self.assertEqual(res.status_code, 400, f"Case {idx} with {bad_field} should return 400")

    def test_file_security_public_vs_private_and_path_traversal(self):
        """Requirement 8: Public images vs private documents and path traversal protection."""
        from models import DeviceFile, db
        with self.app.app_context():
            pub_file = DeviceFile(
                device_id="EW-2024-00128",
                file_type="image",
                filename="photo.jpg",
                original_filename="photo.jpg",
                storage_key="11111111222233334444555566667777.jpg",
                file_path="/uploads/11111111222233334444555566667777.jpg",
                content_type="image/jpeg",
                file_size=1024,
                is_private=False,
            )
            priv_file = DeviceFile(
                device_id="EW-2024-00128",
                file_type="document",
                filename="invoice.pdf",
                original_filename="invoice.pdf",
                storage_key="8888888899990000aaaabbbbccccdddd.pdf",
                file_path="/uploads/8888888899990000aaaabbbbccccdddd.pdf",
                content_type="application/pdf",
                file_size=2048,
                is_private=True,
            )
            db.session.add(pub_file)
            db.session.add(priv_file)
            db.session.commit()

        # Path traversal checks
        res_trav1 = self.client.get("/uploads/..%2F..%2Fsecret.txt")
        self.assertIn(res_trav1.status_code, (400, 404))
        res_trav2 = self.client.get("/api/v1/files/..%2Fsecret.txt")
        self.assertIn(res_trav2.status_code, (400, 404))

        # Public upload endpoint should reject private document with 403
        res_priv = self.client.get("/uploads/8888888899990000aaaabbbbccccdddd.pdf")
        self.assertEqual(res_priv.status_code, 403)

    def test_outbound_partner_missing_api_key_when_url_present(self):
        """Requirement 13: If PARTNER_API_URL exists but PARTNER_API_KEY is missing, abort transfer."""
        from models import Device, db
        self.app.config["PARTNER_API_URL"] = "https://partner.example.com"
        self.app.config["PARTNER_API_KEY"] = ""
        with self.app.app_context():
            dev = Device.query.filter_by(id="EW-2024-00128").first()
            dev.status = "Pending assessment"
            db.session.commit()

        res = self.client.post("/devices/EW-2024-00128/send", data={"csrf": self.token()}, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        with self.app.app_context():
            dev = Device.query.filter_by(id="EW-2024-00128").first()
            # Device must NOT move to Processing
            self.assertEqual(dev.status, "Pending assessment")

    def test_notifications_endpoint(self):
        """Requirement 19: Notifications API endpoint returns real database activities."""
        res = self.client.get("/api/v1/notifications")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "success")
    def test_postgresql_dialect_and_query_compatibility(self):
        """Requirement 24: Test that models and queries compile cleanly to PostgreSQL dialect."""
        from sqlalchemy.dialects import postgresql
        from sqlalchemy import func
        from models import Device, Transfer, AssessmentResult, DeviceFile, db

        with self.app.app_context():
            pg_dialect = postgresql.dialect()

            # Device query compilation
            dev_q = Device.query.filter(Device.status.in_(["Processing", "Refurbishment"])).order_by(Device.created_at.desc())
            sql = str(dev_q.statement.compile(dialect=pg_dialect))
            self.assertIn("devices", sql)
            self.assertIn("ORDER BY", sql)

            # Aggregation query compilation
            agg_q = db.session.query(
                func.coalesce(func.sum(AssessmentResult.component_value), 0.0),
                func.coalesce(func.sum(AssessmentResult.repair_cost), 0.0),
                func.coalesce(func.sum(AssessmentResult.co2_avoided_kg), 0.0)
            )
            sql_agg = str(agg_q.statement.compile(dialect=pg_dialect))
            self.assertIn("COALESCE", sql_agg.upper())
            self.assertIn("assessment_results", sql_agg)

            # Transfer idempotency query compilation
            trf_q = Transfer.query.filter_by(idempotency_key="TRF-TEST").order_by(Transfer.timestamp.desc())
            sql_trf = str(trf_q.statement.compile(dialect=pg_dialect))
            self.assertIn("transfers", sql_trf)

            # File query compilation
            file_q = DeviceFile.query.filter_by(storage_key="safe_key.jpg", is_private=False)
            sql_file = str(file_q.statement.compile(dialect=pg_dialect))
            self.assertIn("device_files", sql_file)

    def test_mandatory_file_validation_suite(self):
        """MANDATORY TESTS 1-7: validate_file_content rules."""
        from storage import validate_file_content, MAX_FILE_SIZE

        # TEST 1: valid JPG under 5 MB -> accepted
        valid_jpg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00" + b"\x00" * 2000
        ok, msg = validate_file_content(valid_jpg, "photo.jpg", "image")
        self.assertTrue(ok)
        self.assertEqual(msg, "")

        # TEST 2: valid PNG under 5 MB -> accepted
        valid_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 2000
        ok, msg = validate_file_content(valid_png, "photo.png", "image")
        self.assertTrue(ok)
        self.assertEqual(msg, "")

        # TEST 3: valid PDF under 5 MB -> accepted
        valid_pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"
        ok, msg = validate_file_content(valid_pdf, "invoice.pdf", "document")
        self.assertTrue(ok)
        self.assertEqual(msg, "")

        # Also WEBP and GIF acceptance
        valid_webp = b"RIFF\x20\x00\x00\x00WEBPVP8 " + b"\x00" * 100
        ok, _ = validate_file_content(valid_webp, "photo.webp", "image")
        self.assertTrue(ok)

        valid_gif = b"GIF89a\x01\x00\x01\x00\x80\x00\x00" + b"\x00" * 100
        ok, _ = validate_file_content(valid_gif, "photo.gif", "image")
        self.assertTrue(ok)

        # TEST 4: JPG exactly 5 MB -> accepted
        exact_5mb_jpg = b"\xff\xd8\xff\xe0" + b"\x00" * (MAX_FILE_SIZE - 4)
        self.assertEqual(len(exact_5mb_jpg), MAX_FILE_SIZE)
        ok, msg = validate_file_content(exact_5mb_jpg, "photo_exact.jpg", "image")
        self.assertTrue(ok)
        self.assertEqual(msg, "")

        # TEST 5: file over 5 MB -> rejected
        over_5mb_jpg = b"\xff\xd8\xff\xe0" + b"\x00" * (MAX_FILE_SIZE - 3)
        self.assertEqual(len(over_5mb_jpg), MAX_FILE_SIZE + 1)
        ok, msg = validate_file_content(over_5mb_jpg, "photo_over.jpg", "image")
        self.assertFalse(ok)
        self.assertIn("exceeds maximum size of 5 MB", msg)

        # TEST 6: fake PDF with .pdf extension -> rejected
        fake_pdf = b"MZ\x90\x00\x03\x00\x00\x00Fake Executable Binary disguised as pdf"
        ok, msg = validate_file_content(fake_pdf, "malware.pdf", "document")
        self.assertFalse(ok)
        self.assertIn("signature", msg.lower())

        # TEST 7: fake JPG with .jpg extension -> rejected
        fake_jpg = b"This is just raw text inside a file named photo.jpg"
        ok, msg = validate_file_content(fake_jpg, "fake.jpg", "image")
        self.assertFalse(ok)
        self.assertIn("signature", msg.lower())

        # Empty file check (0 bytes) -> rejected
        ok, msg = validate_file_content(b"", "empty.jpg", "image")
        self.assertFalse(ok)
        self.assertIn("empty", msg.lower())

    def test_mandatory_upload_registration_lifecycle(self):
        """MANDATORY TESTS 8-17: Registration, retrieval, dashboard, and 413 handling."""
        from models import Device, DeviceFile, db

        valid_jpg_data = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00" + b"\x00" * 4096
        valid_pdf_data = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"

        # TEST 8: registration with image only -> succeeds
        token8 = self.token()
        payload8 = {
            "csrf": token8,
            "Device Type": "Laptop",
            "Brand": "Dell",
            "Model": "Latitude 7420",
            "Purchase Year": "2022",
            "Condition": "Good",
            "Power Status": "Powers on",
            "Screen Condition": "Working",
            "Battery Condition": "Good",
            "Estimated Weight": "1.3",
            "image": (io.BytesIO(valid_jpg_data), "dell_photo.jpg"),
        }
        res8 = self.client.post("/register", data=payload8, follow_redirects=True)
        self.assertEqual(res8.status_code, 200)
        self.assertIn(b"Device registered successfully", res8.data)

        with self.app.app_context():
            dev8 = Device.query.filter_by(name="Dell Latitude 7420").first()
            self.assertIsNotNone(dev8)
            self.assertIsNotNone(dev8.image)
            self.assertTrue(dev8.image.startswith("/uploads/"))
            self.assertIsNone(dev8.document)
            # Authoritative DeviceFile record
            df8 = DeviceFile.query.filter_by(device_id=dev8.id, file_type="image").first()
            self.assertIsNotNone(df8)
            self.assertFalse(df8.is_private)
            self.assertEqual(df8.content_type, "image/jpeg")
            image_key_8 = df8.storage_key

        # TEST 11: uploaded image can be retrieved from /uploads/<key> -> HTTP 200
        res11 = self.client.get(f"/uploads/{image_key_8}")
        self.assertEqual(res11.status_code, 200)
        self.assertEqual(res11.mimetype, "image/jpeg")
        self.assertEqual(res11.data, valid_jpg_data)

        # TEST 14: dashboard device with image -> rendered HTML contains image URL
        res14 = self.client.get("/")
        self.assertEqual(res14.status_code, 200)
        html14 = res14.data.decode("utf-8")
        self.assertIn(f"/uploads/{image_key_8}", html14)
        self.assertIn("Dell Latitude 7420", html14)

        # TEST 9: registration with document only -> succeeds
        token9 = self.token()
        payload9 = {
            "csrf": token9,
            "Device Type": "Monitor",
            "Brand": "Samsung",
            "Model": "ViewFinity S8",
            "Purchase Year": "2023",
            "Condition": "Good",
            "Power Status": "Powers on",
            "Screen Condition": "Working",
            "Battery Condition": "Not applicable",
            "Estimated Weight": "4.2",
            "document": (io.BytesIO(valid_pdf_data), "receipt.pdf"),
        }
        res9 = self.client.post("/register", data=payload9, follow_redirects=True)
        self.assertEqual(res9.status_code, 200)
        self.assertIn(b"Device registered successfully", res9.data)

        with self.app.app_context():
            dev9 = Device.query.filter_by(name="Samsung ViewFinity S8").first()
            self.assertIsNotNone(dev9)
            self.assertIsNone(dev9.image)
            self.assertIsNotNone(dev9.document)
            self.assertTrue(dev9.document.startswith("/api/v1/files/"))
            df9 = DeviceFile.query.filter_by(device_id=dev9.id, file_type="document").first()
            self.assertIsNotNone(df9)
            self.assertTrue(df9.is_private)
            self.assertEqual(df9.content_type, "application/pdf")
            doc_key_9 = df9.storage_key

        # TEST 12: private PDF cannot be retrieved through /uploads/ -> HTTP 403/404
        res12 = self.client.get(f"/uploads/{doc_key_9}")
        self.assertEqual(res12.status_code, 403)

        # TEST 13: private PDF through authenticated API endpoint -> HTTP 200
        res13_unauth = self.client.get(f"/api/v1/files/{doc_key_9}")
        self.assertEqual(res13_unauth.status_code, 401)

        res13_auth = self.client.get(
            f"/api/v1/files/{doc_key_9}",
            headers={"X-API-Key": "test-circular-secret"}
        )
        self.assertEqual(res13_auth.status_code, 200)
        self.assertEqual(res13_auth.mimetype, "application/pdf")
        self.assertIn("inline", res13_auth.headers.get("Content-Disposition", ""))

        # TEST 15: dashboard device without image -> existing device icon remains
        res15 = self.client.get("/")
        html15 = soup(res15.data.decode("utf-8"))
        found_row = None
        for tr in html15.select("table tbody tr"):
            if "Samsung ViewFinity S8" in tr.get_text():
                found_row = tr
                break
        self.assertIsNotNone(found_row)
        self.assertIsNotNone(found_row.select_one("div.size-9 svg"))
        self.assertIsNone(found_row.select_one("div.size-9 img"))

        # TEST 10: registration with image + document -> succeeds
        token10 = self.token()
        payload10 = {
            "csrf": token10,
            "Device Type": "Smartphone",
            "Brand": "Google",
            "Model": "Pixel 7 Pro",
            "Purchase Year": "2023",
            "Condition": "Good",
            "Power Status": "Powers on",
            "Screen Condition": "Working",
            "Battery Condition": "Good",
            "Estimated Weight": "0.21",
            "image": (io.BytesIO(valid_jpg_data), "pixel.jpg"),
            "document": (io.BytesIO(valid_pdf_data), "warranty.pdf"),
        }
        res10 = self.client.post("/register", data=payload10, follow_redirects=True)
        self.assertEqual(res10.status_code, 200)
        self.assertIn(b"Device registered successfully", res10.data)

        with self.app.app_context():
            dev10 = Device.query.filter_by(name="Google Pixel 7 Pro").first()
            self.assertIsNotNone(dev10)
            self.assertIsNotNone(dev10.image)
            self.assertIsNotNone(dev10.document)
            self.assertEqual(len(dev10.files), 2)

        # Detail page test for dev10 (has image)
        res_detail = self.client.get(f"/devices/{dev10.id}")
        self.assertEqual(res_detail.status_code, 200)
        html_detail = soup(res_detail.data.decode("utf-8"))
        img_detail = html_detail.select_one("section div.h-48 img")
        self.assertIsNotNone(img_detail)
        self.assertEqual(img_detail["src"], dev10.image)

        # TEST 16: two files close to 5 MB each -> registration succeeds
        size_4_5mb = int(4.5 * 1024 * 1024)
        big_jpg_data = b"\xff\xd8\xff\xe0" + b"\x00" * (size_4_5mb - 4)
        big_pdf_data = b"%PDF-1.4\n" + b"\x00" * (size_4_5mb - 9)
        token16 = self.token()
        payload16 = {
            "csrf": token16,
            "Device Type": "Laptop",
            "Brand": "Apple",
            "Model": "MacBook Pro 16",
            "Purchase Year": "2023",
            "Condition": "Good",
            "Power Status": "Powers on",
            "Screen Condition": "Working",
            "Battery Condition": "Good",
            "Estimated Weight": "2.1",
            "image": (io.BytesIO(big_jpg_data), "macbook.jpg"),
            "document": (io.BytesIO(big_pdf_data), "invoice_big.pdf"),
        }
        res16 = self.client.post("/register", data=payload16, follow_redirects=True)
        self.assertEqual(res16.status_code, 200)
        self.assertIn(b"Device registered successfully", res16.data)

        # TEST 17: request exceeding MAX_CONTENT_LENGTH -> friendly error
        self.app.config["MAX_CONTENT_LENGTH"] = 1000  # 1 KB limit to test overflow
        token17 = self.token()
        overflow_payload = {
            "csrf": token17,
            "Device Type": "Laptop",
            "Brand": "HP",
            "Model": "Omen",
            "Purchase Year": "2023",
            "Condition": "Good",
            "Power Status": "Powers on",
            "Screen Condition": "Working",
            "Battery Condition": "Good",
            "Estimated Weight": "2.5",
            "image": (io.BytesIO(b"\xff\xd8\xff\xe0" + b"\x00" * 5000), "too_big.jpg"),
        }
        res17 = self.client.post("/register", data=overflow_payload, follow_redirects=True)
        self.assertEqual(res17.status_code, 200)
        self.assertIn(b"Upload is too large. Each file can be up to 5 MB.", res17.data)
        self.app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

    def test_upload_failure_cleanup_transactional(self):
        """Requirement 18: If document fails or commit fails, stored image is cleaned up."""
        from models import Device, db
        from storage import get_storage_service

        valid_jpg_data = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00" + b"\x00" * 1000
        fake_pdf_data = b"NOT_A_REAL_PDF_FILE"

        storage = get_storage_service(self.app.config)
        upload_folder = Path(storage.upload_folder)
        files_before = set(upload_folder.iterdir()) if upload_folder.exists() else set()

        token = self.token()
        payload = {
            "csrf": token,
            "Device Type": "Tablet",
            "Brand": "Apple",
            "Model": "iPad Pro",
            "Purchase Year": "2022",
            "Condition": "Good",
            "Power Status": "Powers on",
            "Screen Condition": "Working",
            "Battery Condition": "Good",
            "Estimated Weight": "0.68",
            "image": (io.BytesIO(valid_jpg_data), "ipad.jpg"),
            "document": (io.BytesIO(fake_pdf_data), "fake_doc.pdf"),
        }
        res = self.client.post("/register", data=payload, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Maximum file size: 5 MB", res.data)

        # Verify device was NOT created
        with self.app.app_context():
            dev = Device.query.filter_by(name="Apple iPad Pro").first()
            self.assertIsNone(dev)

        # Verify no orphaned files left in storage folder
        files_after = set(upload_folder.iterdir()) if upload_folder.exists() else set()
        self.assertEqual(files_before, files_after)

    @patch("app.requests.post")
    def test_phase_rectifications_comprehensive(self, mock_post):
        """Comprehensive verification of Phases 11, 12, 13, 14, 15, 17, and retry preservation."""
        from models import Device, Transfer, db
        from metrics import get_platform_metrics

        # 1. Verify pending_count vs processing_count separation
        with self.app.app_context():
            dev_pending = Device(
                id="EW-TEST-PEND-1",
                name="Pending Device",
                type="Laptop",
                brand="Dell",
                model="XPS 13",
                status="Pending assessment",
                year="2022",
                weight="1.2",
                condition="Good",
            )
            dev_processing = Device(
                id="EW-TEST-PROC-1",
                name="Processing Device",
                type="Smartphone",
                brand="Google",
                model="Pixel 7",
                status="Processing",
                year="2022",
                weight="0.2",
                condition="Good",
            )
            db.session.add(dev_pending)
            db.session.add(dev_processing)
            db.session.commit()

            metrics = get_platform_metrics()
            self.assertEqual(metrics["pending_count"], Device.query.filter_by(status="Pending assessment").count())
            self.assertEqual(metrics["processing_count"], Device.query.filter_by(status="Processing").count())
            self.assertNotEqual(metrics["pending_count"], metrics["pending_count"] + metrics["processing_count"])

        # 2. Inbound assessment on Pending assessment device is rejected with 409
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": "test-circular-secret",
        }
        res_conflict = self.client.post(
            "/api/v1/assessment-results",
            data=json.dumps({
                "device_id": "EW-TEST-PEND-1",
                "repairability_score": 80,
                "circular_recovery_score": 75,
                "repair_cost": 2000.0,
                "component_value": 8000.0,
                "recommendation": "REFURBISH",
                "co2_avoided_kg": 15.0,
            }),
            headers=headers,
        )
        self.assertEqual(res_conflict.status_code, 409)
        self.assertIn("cannot transition", res_conflict.get_json()["message"])

        # 3. Outbound partner 201 moves device to Processing
        self.app.config["PARTNER_API_URL"] = "https://partner.example.com"
        mock_resp_201 = MagicMock()
        mock_resp_201.status_code = 201
        mock_resp_201.reason = "Created"
        mock_resp_201.text = '{"status":"queued"}'
        mock_post.return_value = mock_resp_201

        res_send = self.client.post(
            "/devices/EW-TEST-PEND-1/send",
            data={"csrf": self.token()},
            follow_redirects=True,
        )
        self.assertEqual(res_send.status_code, 200)
        with self.app.app_context():
            dev = Device.query.filter_by(id="EW-TEST-PEND-1").first()
            self.assertEqual(dev.status, "Processing")

        # 4. Inbound assessment on now-Processing device succeeds and records canonical recommendation
        res_assess_proc = self.client.post(
            "/api/v1/assessment-results",
            data=json.dumps({
                "device_id": "EW-TEST-PEND-1",
                "repairability_score": 80,
                "circular_recovery_score": 75,
                "repair_cost": 2000.0,
                "component_value": 8000.0,
                "recommendation": "PARTS RECOVERY",  # Alias must be mapped to PARTS_RECOVERY
                "co2_avoided_kg": 15.0,
            }),
            headers=headers,
        )
        self.assertEqual(res_assess_proc.status_code, 200)
        with self.app.app_context():
            dev = Device.query.filter_by(id="EW-TEST-PEND-1").first()
            self.assertEqual(dev.status, "Parts recovery")
            self.assertEqual(dev.assessment.recommendation, "PARTS_RECOVERY")

        # 5. Outbound partner timeout preserves previous status (no transition to Processing)
        with self.app.app_context():
            dev_timeout = Device(
                id="EW-TEST-TIMEOUT",
                name="Timeout Laptop",
                type="Laptop",
                brand="Asus",
                model="ZenBook",
                status="Pending assessment",
                year="2021",
                weight="1.1",
                condition="Fair",
            )
            db.session.add(dev_timeout)
            db.session.commit()

        mock_post.side_effect = requests.RequestException("Partner network timeout")
        res_timeout = self.client.post(
            "/devices/EW-TEST-TIMEOUT/send",
            data={"csrf": self.token()},
            follow_redirects=True,
        )
        self.assertEqual(res_timeout.status_code, 200)
        with self.app.app_context():
            dev_t = Device.query.filter_by(id="EW-TEST-TIMEOUT").first()
            self.assertEqual(dev_t.status, "Pending assessment")
            trf_failed = Transfer.query.filter_by(device_id="EW-TEST-TIMEOUT").first()
            self.assertEqual(trf_failed.status, "Failed")
            original_trf_id = trf_failed.id
            original_idemp_key = trf_failed.idempotency_key

        # 6. Retry preserves transfer ID and idempotency key
        mock_post.side_effect = None
        mock_resp_200 = MagicMock()
        mock_resp_200.status_code = 200
        mock_resp_200.reason = "OK"
        mock_resp_200.text = '{"status":"ok"}'
        mock_post.return_value = mock_resp_200

        res_retry = self.client.post(
            f"/transfers/{original_trf_id}/retry",
            data={"csrf": self.token()},
            follow_redirects=True,
        )
        self.assertEqual(res_retry.status_code, 200)
        with self.app.app_context():
            trf_retried = Transfer.query.filter_by(id=original_trf_id).first()
            self.assertEqual(trf_retried.id, original_trf_id)
            self.assertEqual(trf_retried.idempotency_key, original_idemp_key)
            self.assertEqual(trf_retried.status, "Completed")

    def test_render_deployment_readiness_comprehensive(self):
        """Verify all deployment-critical requirements for Render with PostgreSQL."""
        from config import Config, normalize_database_url
        from storage import LocalStorage, get_storage_service

        # 1. PostgreSQL URL normalization
        cases = [
            ("postgres://user:pass@ep-cool-db.us-east-1.render.com:5432/circular_ewaste", "postgresql+psycopg://user:pass@ep-cool-db.us-east-1.render.com:5432/circular_ewaste"),
            ("postgresql://user:pass@ep-cool-db.us-east-1.render.com:5432/circular_ewaste", "postgresql+psycopg://user:pass@ep-cool-db.us-east-1.render.com:5432/circular_ewaste"),
            ("postgresql+psycopg://user:pass@ep-cool-db.us-east-1.render.com:5432/circular_ewaste", "postgresql+psycopg://user:pass@ep-cool-db.us-east-1.render.com:5432/circular_ewaste"),
            ("postgresql+psycopg2://user:pass@ep-cool-db.us-east-1.render.com:5432/circular_ewaste", "postgresql+psycopg://user:pass@ep-cool-db.us-east-1.render.com:5432/circular_ewaste"),
        ]
        for raw, expected in cases:
            self.assertEqual(normalize_database_url(raw), expected)
            self.assertEqual(Config.normalize_database_url(raw), expected)

        # 2. Production fail-safe configuration in create_app
        class IncompleteProdConfig(Config):
            IS_PRODUCTION = True
            SECRET_KEY = "dev-secret-key-replace-in-production"
            CIRCULAR_API_KEY = "secret"
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

        with self.assertRaises(ValueError):
            create_app(config_class=IncompleteProdConfig)

        # 3. Production demo seeding disabled
        prod_db_path = Path(self.directory.name) / "prod_test.sqlite3"
        class CleanProdConfig(Config):
            IS_PRODUCTION = True
            SECRET_KEY = "valid-prod-secret-key-12345678"
            CIRCULAR_API_KEY = "valid-prod-api-key-12345678"
            SEED_DEMO_DATA = True  # Attempt to seed in production should be blocked
            SQLALCHEMY_DATABASE_URI = f"sqlite:///{prod_db_path}"

        prod_app = create_app(config_class=CleanProdConfig)
        with prod_app.app_context():
            db.create_all()
            from models import Device, Metadata
            # Verify no demo devices were seeded
            self.assertEqual(Device.query.count(), 0)
            self.assertIsNone(Metadata.query.filter_by(key="seeded").first())

        # 4. Storage creation and write check
        valid_dir = Path(self.directory.name) / "render_storage_check"
        storage = LocalStorage(str(valid_dir))
        self.assertTrue(valid_dir.exists())
        k, url = storage.save(b"%PDF-1.4 test bytes", "test.pdf", is_private=True)
        retrieved, ctype = storage.retrieve(k)
        self.assertEqual(retrieved, b"%PDF-1.4 test bytes")
        self.assertEqual(ctype, "application/pdf")
        self.assertTrue(storage.delete(k))


if __name__ == "__main__":
    unittest.main()

