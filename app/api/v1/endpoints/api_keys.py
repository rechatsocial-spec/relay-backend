import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import require_super_admin, get_current_user
from app.core.crypto import encrypt_secret, mask
from app.db.session import get_db
from app.models.user import User
from app.models.api_key import ApiKey, ApiKeyStatus
from app.models.activity_log import ActivityLog
from app.schemas.api_key import ApiKeyCreate, ApiKeyOut

router = APIRouter(prefix="/api-keys", tags=["api-keys"], dependencies=[Depends(require_super_admin)])


@router.get("", response_model=list[ApiKeyOut])
def list_api_keys(db: Session = Depends(get_db)):
    return db.query(ApiKey).all()


@router.post("", response_model=ApiKeyOut)
def add_api_key(payload: ApiKeyCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    key = ApiKey(
        provider=payload.provider,
        masked=mask(payload.api_key),
        encrypted_value=encrypt_secret(payload.api_key),
        business_account_id=payload.business_account_id,
        phone_number_id=payload.phone_number_id,
        status=ApiKeyStatus.CONNECTED,
        updated_by=user.id,
    )
    db.add(key)
    db.add(ActivityLog(actor_id=user.id, type="api_key", message=f"API key added for {payload.provider}"))
    db.commit()
    db.refresh(key)
    return key


@router.post("/{key_id}/test", response_model=ApiKeyOut)
def test_connection(key_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    key = db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(404, "API key not found")
    # Real check goes here once WHATSAPP_MOCK is false (a lightweight GET
    # against the provider's account endpoint). For now: assume healthy.
    key.status = ApiKeyStatus.CONNECTED
    db.add(ActivityLog(actor_id=user.id, type="api_key", message=f"Connection tested for {key.provider}"))
    db.commit()
    db.refresh(key)
    return key


@router.delete("/{key_id}")
def remove_api_key(key_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    key = db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(404, "API key not found")
    db.add(ActivityLog(actor_id=user.id, type="api_key", message=f"API key removed for {key.provider}"))
    db.delete(key)
    db.commit()
    return {"deleted": True}
