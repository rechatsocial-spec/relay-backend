"""
Creates the first Super Admin from BOOTSTRAP_ADMIN_* environment variables, so a
production deploy never needs the demo seed (which uses a known password).
Runs on every startup; does nothing if the variables are empty or the user exists.
"""
import logging

from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.user import Role, User

log = logging.getLogger("relay.bootstrap")


def ensure_bootstrap_admin() -> None:
    email = settings.BOOTSTRAP_ADMIN_EMAIL.strip().lower()
    if not email or not settings.BOOTSTRAP_ADMIN_PASSWORD:
        return
    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == email).first():
            return
        db.add(User(
            name=settings.BOOTSTRAP_ADMIN_NAME, email=email, phone="",
            hashed_password=hash_password(settings.BOOTSTRAP_ADMIN_PASSWORD), role=Role.SUPER_ADMIN,
        ))
        db.commit()
        log.warning("Created bootstrap Super Admin %s", email)
    finally:
        db.close()
