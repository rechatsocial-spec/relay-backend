from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleLoginRequest(BaseModel):
    # The ID token (JWT) Google Identity Services hands the browser.
    credential: str = Field(min_length=20, max_length=8192)


class AuthConfigOut(BaseModel):
    google_client_id: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    user_id: str
    name: str
    hidden_pages: list[str] = []
