"""
Google Sign-In (Google Identity Services "ID token" flow).

The browser shows Google's button, Google hands the browser a signed ID token
(a JWT), the frontend POSTs it to /auth/google, and this module verifies it:
signature against Google's public certs, audience == our GOOGLE_CLIENT_ID,
issuer == Google, not expired, and email_verified. No client secret is needed
for this flow.
"""
from dataclasses import dataclass

from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from app.core.config import settings

GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}

# Reused across calls so Google's certs are fetched over one pooled session.
_transport = google_requests.Request()


class GoogleAuthError(Exception):
    """Token is invalid, expired, for another app, or the email isn't verified."""


class GoogleUnavailableError(Exception):
    """Google's certificate endpoint couldn't be reached."""


@dataclass
class GoogleIdentity:
    sub: str
    email: str
    name: str | None
    picture: str | None


def verify_google_credential(credential: str) -> GoogleIdentity:
    try:
        claims = id_token.verify_oauth2_token(
            credential, _transport, settings.GOOGLE_CLIENT_ID, clock_skew_in_seconds=10
        )
    except google_exceptions.TransportError as e:
        raise GoogleUnavailableError(str(e)) from e
    except (ValueError, google_exceptions.GoogleAuthError) as e:
        raise GoogleAuthError(str(e)) from e

    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise GoogleAuthError("Token was not issued by Google")
    if not claims.get("email") or not claims.get("email_verified"):
        raise GoogleAuthError("Google account email is not verified")

    return GoogleIdentity(
        sub=claims["sub"],
        email=claims["email"].lower(),
        name=claims.get("name"),
        picture=claims.get("picture"),
    )
