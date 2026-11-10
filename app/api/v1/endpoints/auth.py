from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.google_auth import GoogleAuthError, GoogleUnavailableError, verify_google_credential
from app.core.pages import hidden_pages_for
from app.core.security import verify_password, create_access_token
from app.db.session import get_db
from app.models.activity_log import ActivityLog
from app.models.user import User, UserStatus
from app.schemas.auth import LoginRequest, TokenResponse, GoogleLoginRequest, AuthConfigOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_for(user: User) -> TokenResponse:
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is inactive")
    token = create_access_token(subject=str(user.id), role=user.role.value)
    return TokenResponse(access_token=token, role=user.role.value, user_id=str(user.id), name=user.name,
                         hidden_pages=hidden_pages_for(user))


@router.get("/config", response_model=AuthConfigOut)
def auth_config():
    """Public: tells the login screen which sign-in methods are available.
    The Google Client ID is not a secret — it's embedded in every page that
    shows a Google button."""
    return AuthConfigOut(google_client_id=settings.GOOGLE_CLIENT_ID or None)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(func.lower(User.email) == payload.email.lower()).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    return _token_for(user)


@router.post("/google", response_model=TokenResponse)
def login_with_google(payload: GoogleLoginRequest, db: Session = Depends(get_db)):
    """
    Sign in with a Google ID token. Only works for people who already have an
    account here (matched by verified email) — there is no self-signup, so a
    Super Admin still decides who gets in.
    """
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google sign-in is not configured on the server")
    try:
        identity = verify_google_credential(payload.credential)
    except GoogleUnavailableError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Couldn't reach Google to verify sign-in. Try again.")
    except GoogleAuthError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Google sign-in failed or expired. Try again.")

    user = db.query(User).filter(func.lower(User.email) == identity.email).first()
    if not user:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"No CRM account exists for {identity.email}. Ask your Super Admin to add you.",
        )
    response = _token_for(user)
    db.add(ActivityLog(actor_id=user.id, type="auth", message=f"{user.name} signed in with Google"))
    db.commit()
    return response
