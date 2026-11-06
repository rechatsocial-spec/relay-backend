import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User, Role, UserStatus

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    try:
        user = db.get(User, uuid.UUID(payload["sub"]))
    except (KeyError, ValueError):
        user = None
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    # A deactivated admin's still-valid token must stop working immediately.
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is inactive")
    return user


def require_super_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.SUPER_ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super Admin access required")
    return user


def admin_scope_filter(user: User = Depends(get_current_user)) -> uuid.UUID | None:
    """
    Returns the admin_id every scoped query should filter by, or None when
    the caller is a Super Admin and should see everything. Every router that
    touches admin-owned data (numbers, contacts, campaigns, automations,
    chatbots) uses this instead of trusting a client-supplied admin_id.
    """
    return None if user.role == Role.SUPER_ADMIN else user.id


def get_owned_number(db: Session, number_id: uuid.UUID, scope: uuid.UUID | None):
    """
    Loads a WhatsApp number and enforces tenant ownership. Use this whenever a
    client sends a whatsapp_number_id (creating contacts, campaigns, chatbots)
    so an Admin can't attach rows to another Admin's number. Returns 404
    (not 403) on a foreign number so ids from other tenants aren't confirmed.
    """
    from app.models.whatsapp_number import WhatsAppNumber

    number = db.get(WhatsAppNumber, number_id)
    if not number or (scope and number.admin_id != scope):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "WhatsApp number not found")
    return number
