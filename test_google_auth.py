"""
Tests for POST /auth/google without needing Google or a browser.

It mints real RS256-signed ID tokens with a throwaway key and points the
verifier at that key's certificate instead of Google's, so the actual
verification code (signature, audience, issuer, expiry, email_verified) runs.

    python test_google_auth.py
"""
import datetime as dt
import os
import sys
import tempfile
import time

db_file = os.path.join(tempfile.mkdtemp(), "google_test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{db_file}"
os.environ["SECRET_KEY"] = "test-secret"
CLIENT_ID = "test-client-id.apps.googleusercontent.com"
os.environ["GOOGLE_CLIENT_ID"] = CLIENT_ID

# Same SQLite type shims as run_dev_sqlite.py
import run_dev_sqlite  # noqa: E402
import sqlalchemy.dialects.postgresql as pg  # noqa: E402
pg.UUID = lambda as_uuid=False, *a, **k: run_dev_sqlite._SqliteUUID()
pg.ARRAY = lambda item_type=None, *a, **k: run_dev_sqlite._SqliteARRAY(item_type)

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from google.auth import crypt, jwt as gjwt  # noqa: E402
from google.oauth2 import id_token as gid  # noqa: E402

import app.seed as seed  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.main import app  # noqa: E402

seed.run()
client = TestClient(app)

# --- throwaway signing key + cert, standing in for Google's -----------------
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-google")])
now = dt.datetime.now(dt.timezone.utc)
cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(1).not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256()))
KID = "test-kid"
key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
signer = crypt.RSASigner.from_string(key_pem, key_id=KID)
real_fetch_certs = gid._fetch_certs
fake_certs = lambda request, url: {KID: cert.public_bytes(serialization.Encoding.PEM).decode()}  # noqa: E731


def token(**overrides):
    t = int(time.time())
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "1234567890",
              "email": "deepa@relaycrm.io", "email_verified": True, "name": "Deepa", "iat": t, "exp": t + 3600}
    claims.update(overrides)
    return gjwt.encode(signer, {k: v for k, v in claims.items() if v is not None}).decode()


passed = failed = 0


def check(cond, label):
    global passed, failed
    passed, failed = passed + bool(cond), failed + (not cond)
    print(("[OK] " if cond else "[FAIL] ") + label)


def google_login(credential):
    return client.post("/api/v1/auth/google", json={"credential": credential})


# --- tests ------------------------------------------------------------------
cfg = client.get("/api/v1/auth/config").json()
check(cfg["google_client_id"] == CLIENT_ID, "/auth/config exposes the Google client id")

gid._fetch_certs = fake_certs

r = google_login(token())
check(r.status_code == 200 and r.json()["role"] == "super_admin", "Valid Google token for existing Super Admin -> JWT")
me = client.get("/api/v1/admins/me", headers={"Authorization": f"Bearer {r.json().get('access_token')}"})
check(me.status_code == 200 and me.json()["email"] == "deepa@relaycrm.io", "Issued JWT works on protected routes")

r = google_login(token(email="Rahul.Sharma@RelayCRM.io"))
check(r.status_code == 200 and r.json()["role"] == "admin", "Email match is case-insensitive (Admin signs in)")

r = google_login(token(email="stranger@gmail.com"))
check(r.status_code == 403 and "Super Admin" in r.json()["detail"], "Unknown Google account is refused (no self-signup)")

check(google_login(token(email_verified=False)).status_code == 401, "Unverified Google email is refused")
check(google_login(token(aud="someone-elses-app.apps.googleusercontent.com")).status_code == 401, "Token minted for another app is refused")
check(google_login(token(iss="https://evil.example.com")).status_code == 401, "Token from a non-Google issuer is refused")
check(google_login(token(iat=int(time.time()) - 7200, exp=int(time.time()) - 3600)).status_code == 401, "Expired token is refused")
t = token()
check(google_login(t[:-6] + ("AAAAAA" if not t.endswith("AAAAAA") else "BBBBBB")).status_code == 401, "Tampered signature is refused")
check(google_login("not-a-jwt-at-all-xxxxxxxxxxxxx").status_code == 401, "Garbage credential is refused")

# deactivated user
admin_tok = google_login(token()).json()["access_token"]
admins = client.get("/api/v1/admins", headers={"Authorization": f"Bearer {admin_tok}"}).json()
victim = admins[1]
client.patch(f"/api/v1/admins/{victim['id']}", json={"status": "inactive"}, headers={"Authorization": f"Bearer {admin_tok}"})
check(google_login(token(email=victim["email"])).status_code == 403, "Deactivated user can't sign in with Google")

logs = client.get("/api/v1/activity-logs", params={"type": "auth"}, headers={"Authorization": f"Bearer {admin_tok}"}).json()
check(any("signed in with Google" in log["message"] for log in logs), "Google sign-ins are written to Activity Logs")

# Google unreachable -> 503 (uses the real cert fetch; fails offline / behind a blocking proxy)
gid._fetch_certs = lambda request, url: (_ for _ in ()).throw(__import__("google.auth.exceptions", fromlist=["x"]).TransportError("down"))
check(google_login(token()).status_code == 503, "Google unreachable -> 503, not a crash")
gid._fetch_certs = real_fetch_certs

settings.GOOGLE_CLIENT_ID = ""
check(google_login(token()).status_code == 503, "Google login disabled when GOOGLE_CLIENT_ID is empty")
check(client.get("/api/v1/auth/config").json()["google_client_id"] is None, "/auth/config hides Google when not configured")

check(client.post("/api/v1/auth/login", json={"email": "DEEPA@relaycrm.io", "password": "password123"}).status_code == 200,
      "Password login still works (now case-insensitive email)")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
