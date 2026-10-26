# Circular E-Waste Platform — Backend Service

A Python Flask backend and intelligence platform for the Circular E-Waste Recovery application.

---

## Directory Structure

```text
backend/
├── app.py              # Main Flask application and BeautifulSoup UI rendering
├── config.py           # Configuration with SQLite / PostgreSQL auto-detection & security validation
├── models.py           # SQLAlchemy ORM models with transition state machine
├── metrics.py          # Central database-backed portfolio metrics service
├── storage.py          # Storage abstraction (Local & Supabase object storage)
├── api.py              # REST API blueprint (/api/health, /api/v1/...)
├── reports.py          # ReportLab PDF report generation and CSV exports
├── wsgi.py             # Production WSGI entry point
├── Procfile            # Gunicorn process definition
├── render.yaml         # Render deployment blueprint
├── demo.json           # Seed database records
├── requirements.txt    # Python dependencies (including Flask-Migrate)
├── .env.example        # Environment variables template
├── migrations/         # Alembic database migrations
└── tests/
    └── test_app.py     # Automated test suite (23 tests, 18 subtests)
```

---

## Running Locally

From `backend/`:

```powershell
# 1. Activate virtual environment:
.venv\Scripts\Activate.ps1

# 2. Run migrations:
flask db upgrade

# 3. Start WSGI server:
python wsgi.py
```

Open your browser at `http://127.0.0.1:5000`. The backend automatically loads the HTML templates and static stylesheets from the neighboring `../frontend` folder.

---

## Running Tests

From repository root or `backend/`:

```powershell
pytest backend/tests/test_app.py -v
```

All 23 tests and 18 subtests pass with a 100% success rate.

For full architectural, API schema, and Render deployment documentation, refer to the root [README.md](../README.md).
