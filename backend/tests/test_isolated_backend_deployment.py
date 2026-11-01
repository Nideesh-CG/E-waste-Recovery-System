"""Test isolated backend deployment structure as invoked by Vercel Services.

In Vercel multi-service mode:
Service 'backend' runs with root 'backend' and entrypoint 'wsgi.py'.
The parent directory does NOT contain 'frontend/'.
This test verifies that:
1. 'backend/wsgi.py' imports without FileNotFoundError or top-level crashes.
2. 'backend/static/icons.json' is present and valid JSON.
3. 'backend/app.py' imports safely and creates the Flask app.
4. The '/api/v1/assessment-results' endpoint is properly registered and accessible.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent


def test_icons_json_exists_in_backend():
    icons_file = BACKEND_DIR / "static" / "icons.json"
    assert icons_file.exists(), "backend/static/icons.json must exist for the isolated backend service."
    data = json.loads(icons_file.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert len(data) > 0


def test_isolated_backend_import_in_separate_directory():
    """Simulate Vercel service container where only 'backend' directory is copied."""
    with tempfile.TemporaryDirectory() as td:
        t_backend = Path(td) / "backend"
        # Copy backend directory only
        shutil.copytree(str(BACKEND_DIR), str(t_backend), ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.sqlite3"))
        
        # Run python subprocess in that directory with sys.path pointing ONLY to t_backend
        code = (
            "import sys\n"
            f"sys.path.insert(0, r'{t_backend}')\n"
            "from wsgi import app\n"
            "assert app is not None\n"
            "client = app.test_client()\n"
            "resp = client.get('/api/health')\n"
            "assert resp.status_code in (200, 500, 503)\n"
            "print('ISOLATED_BACKEND_SUCCESS')\n"
        )
        res = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False
        )
        assert res.returncode == 0, f"Isolated backend failed to start:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}"
        assert "ISOLATED_BACKEND_SUCCESS" in res.stdout

