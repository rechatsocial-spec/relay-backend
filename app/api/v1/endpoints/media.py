"""
Serves files shown in the chat. Links are signed (see services/media.py), so <img>/<audio> tags work
without a login header and nobody can guess a link.
"""
import uuid
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.conversation import Message
from app.models.media import MediaFile
from app.services.media import verify

router = APIRouter(prefix="/media", tags=["media"])


def _inline(name: str | None) -> dict:
    safe = (name or "file").replace('"', "").replace("\n", " ")
    return {"Content-Disposition": f'inline; filename="{safe}"', "Cache-Control": "private, max-age=86400"}


@router.get("/m/{message_id}")
def received_file(message_id: uuid.UUID, t: str = Query(""), db: Session = Depends(get_db)):
    """A file someone sent us: fetched from the WhatsApp service on demand (the key never reaches the browser)."""
    if not verify(str(message_id), t):
        raise HTTPException(403, "Invalid link")
    msg = db.get(Message, message_id)
    if not msg or not msg.media_source:
        raise HTTPException(404, "File not found")
    headers = {}
    if urlparse(msg.media_source).hostname == urlparse(settings.UNIPILE_BASE_URL).hostname:
        headers["X-API-KEY"] = settings.UNIPILE_API_KEY  # only ever sent to the service itself
    try:
        r = httpx.get(msg.media_source, headers=headers, timeout=30, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(502, "The file could not be fetched")
    ctype = msg.media_type or r.headers.get("content-type", "application/octet-stream")
    return Response(content=r.content, media_type=ctype, headers=_inline(msg.media_name))


@router.get("/{file_id}")
def stored_file(file_id: uuid.UUID, t: str = Query(""), db: Session = Depends(get_db)):
    if not verify(str(file_id), t):
        raise HTTPException(403, "Invalid link")
    f = db.get(MediaFile, file_id)
    if not f:
        raise HTTPException(404, "File not found")
    return Response(content=f.data, media_type=f.content_type, headers=_inline(f.filename))
