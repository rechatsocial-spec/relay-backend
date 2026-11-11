"""
Preview or run the demo-data cleanup:

    python -m app.purge_demo          # dry run: shows what would be removed
    python -m app.purge_demo --yes    # really removes it
"""
import json
import sys

from app.core.config import settings
from app.db.session import SessionLocal
from app.services.demo_cleanup import purge_demo_data

if __name__ == "__main__":
    apply = "--yes" in sys.argv
    db = SessionLocal()
    try:
        report = purge_demo_data(db, settings.PURGE_KEEP_EMAILS.split(","), dry_run=not apply)
    finally:
        db.close()
    print(json.dumps(report, indent=2, default=str))
    if report.get("error"):
        sys.exit(1)
    print("\nRemoved." if apply else "\nDry run only — nothing was changed. Add --yes to apply.")
