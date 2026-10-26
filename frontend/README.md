# Circular E-Waste Platform — Frontend UI

This folder contains the pre-rendered HTML templates, styling, icons, and client interactions for the Circular E-Waste Recovery application.

---

## Directory Structure

```text
frontend/
├── templates/          # Approved HTML templates (layout.html, dashboard.html, detail.html, etc.)
├── static/             # Static UI assets:
│   ├── style.css       # Production Tailwind CSS stylesheet
│   ├── interactions.js # Client-side behaviors (toasts, menus, search, modal links)
│   └── icons.json      # Lucide SVG icon definitions
├── demo.json           # Approved demonstration dataset
└── README.md           # Frontend asset documentation
```

---

## Backend Application

All backend logic, REST APIs, database models, ReportLab PDF generators, and deployment manifests reside in the separate [`../backend`](file:///d:/E-waste-Recovery/backend) directory.

To run the application, navigate to `backend/` and execute:
```powershell
cd ../backend
python wsgi.py
```
