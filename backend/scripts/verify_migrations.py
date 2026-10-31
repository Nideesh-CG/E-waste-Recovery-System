#!/usr/bin/env python3
"""Alembic Migrations Chain & Integrity Verifier.

Validates the Alembic migration history, verifies that the revision chain is linear
with no broken references or divergent branches, and checks that the head matches
the authoritative schema.

Exit codes:
  0 - All migrations valid and head matches expected revision.
  1 - Broken migration chain, multiple heads, or missing revisions.
"""

import os
import sys
from pathlib import Path

# Add project root and backend directory to path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR.parent))

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

EXPECTED_CHAIN = [
    ("41179279b201", None, "initial_database_schema"),
    ("95b1a260ef7a", "41179279b201", "align_models_with_supabase_and_add_partner_sync"),
    ("b7a8c9d0e1f2", "95b1a260ef7a", "reconcile_collection_schema"),
]
EXPECTED_HEAD = "b7a8c9d0e1f2"


def verify_migrations():
    print("=" * 80)
    print("ALEMBIC MIGRATIONS INTEGRITY VERIFIER")
    print("=" * 80)

    migrations_dir = BASE_DIR / "migrations"
    alembic_ini = BASE_DIR / "alembic.ini"

    if not migrations_dir.exists():
        print(f"[ERROR] Migrations directory not found at: {migrations_dir}")
        return 1

    alembic_cfg = AlembicConfig(str(alembic_ini) if alembic_ini.exists() else None)
    alembic_cfg.set_main_option("script_location", str(migrations_dir))

    script = ScriptDirectory.from_config(alembic_cfg)
    heads = script.get_heads()

    print(f"Migrations Directory: {migrations_dir}")
    print(f"Detected Heads:       {heads}")

    if len(heads) == 0:
        print("[ERROR] No migration heads found in repository!")
        return 1
    elif len(heads) > 1:
        print(f"[ERROR] Multiple migration heads detected: {heads}. Linear history required.")
        return 1

    current_head = heads[0]
    if current_head != EXPECTED_HEAD:
        print(f"[ERROR] Current head '{current_head}' does not match expected head '{EXPECTED_HEAD}'!")
        return 1

    # Traverse revision chain from base to head
    print("\nVerifying revision chain:")
    for rev_id, expected_down, desc in EXPECTED_CHAIN:
        rev = script.get_revision(rev_id)
        if not rev:
            print(f"  [FAIL] Revision {rev_id} ({desc}) NOT found!")
            return 1
        actual_down = rev.down_revision
        if actual_down != expected_down:
            print(f"  [FAIL] Revision {rev_id}: down_revision is '{actual_down}', expected '{expected_down}'!")
            return 1
        print(f"  [OK] {rev_id} -> down: {str(actual_down):<15} ({desc})")

    print("\n" + "=" * 80)
    print(f"SUCCESS: Linear migration chain verified with active head '{current_head}'.")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(verify_migrations())

