"""Production WSGI entry point for Circular E-Waste Recovery platform."""

import os
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", os.environ.get("FLASK_PORT", "5000")))
    app.run(host="0.0.0.0", port=port)
