import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import admin_scope_filter, get_owned_number
from app.db.session import get_db
from app.models.contact import Contact, ContactStatus
from app.schemas.contact import (
    AddNoteRequest, AddTagRequest, ContactCreate, ContactImportRequest, ContactImportResult,
    ContactOut, ContactUpdate, ImportRowError,
)
from app.services.phone import normalize_phone
from app.services.whatsapp_service import to_e164

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("")
def list_contacts(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, le=100),
    status_filter: ContactStatus | None = Query(None, alias="status"),
    search: str | None = None,
    db: Session = Depends(get_db),
    scope: uuid.UUID | None = Depends(admin_scope_filter),
):
    q = db.query(Contact)
    if scope:
        q = q.filter(Contact.admin_id == scope)
    if status_filter:
        q = q.filter(Contact.status == status_filter)
    if search:
        q = q.filter(Contact.name.ilike(f"%{search}%") | Contact.phone.ilike(f"%{search}%"))
    total = q.count()
    items = q.order_by(Contact.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    return {
        "items": [ContactOut.model_validate(c) for c in items],
        "total": total, "page": page, "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
    }


@router.post("/import", response_model=ContactImportResult)
def import_contacts(payload: ContactImportRequest, db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    """
    Bulk add contacts (pasted list or CSV parsed by the frontend). Numbers are
    normalised to +E.164; existing contacts (same number, same admin) get the
    new tags instead of a duplicate row. Every row gets `tag` so a campaign can
    target exactly this list.
    """
    if not payload.consent:
        raise HTTPException(400, "Confirm these contacts agreed to receive WhatsApp messages from you")
    number = get_owned_number(db, payload.whatsapp_number_id, scope)
    batch_tag = (payload.tag or "").strip()[:60] or None

    existing = {to_e164(c.phone): c for c in db.query(Contact).filter(Contact.admin_id == number.admin_id).all()}
    seen: set[str] = set()
    created = updated = dupes = opted_out = 0
    invalid: list[ImportRowError] = []

    for i, row in enumerate(payload.rows, start=1):
        phone, reason = normalize_phone(row.phone, payload.default_country_code)
        if not phone:
            invalid.append(ImportRowError(row=i, value=row.phone[:40], reason=reason))
            continue
        if phone in seen:
            dupes += 1
            continue
        seen.add(phone)
        tags = [t.strip()[:40] for t in row.tags if t and t.strip()]
        if batch_tag:
            tags.append(batch_tag)
        name = (row.name or "").strip()[:120]

        contact = existing.get(phone)
        if contact:
            merged = list(dict.fromkeys([*(contact.tags or []), *tags]))
            if merged != (contact.tags or []):
                contact.tags = merged
            if name and contact.name in (contact.phone, phone, ""):
                contact.name = name
            if contact.opted_out:
                opted_out += 1
            updated += 1
        else:
            contact = Contact(
                name=name or phone, phone=phone, whatsapp_number_id=number.id, admin_id=number.admin_id,
                status=ContactStatus.NEW, tags=list(dict.fromkeys(tags)), source="Imported list",
            )
            db.add(contact)
            existing[phone] = contact
            created += 1

    db.commit()
    return ContactImportResult(
        created=created, updated=updated, duplicates_in_file=dupes, opted_out=opted_out,
        invalid=invalid[:500], tag=batch_tag,
    )


@router.post("", response_model=ContactOut)
def create_contact(payload: ContactCreate, db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    # The contact belongs to whoever owns the number. Previously this used
    # `scope`, which is None for a Super Admin -> NOT NULL violation (500).
    number = get_owned_number(db, payload.whatsapp_number_id, scope)
    contact = Contact(**payload.model_dump(), admin_id=number.admin_id)
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


@router.get("/{contact_id}", response_model=ContactOut)
def get_contact(contact_id: uuid.UUID, db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    contact = db.get(Contact, contact_id)
    if not contact or (scope and contact.admin_id != scope):
        raise HTTPException(404, "Contact not found")
    return contact


@router.patch("/{contact_id}", response_model=ContactOut)
def update_contact(contact_id: uuid.UUID, payload: ContactUpdate, db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    contact = db.get(Contact, contact_id)
    if not contact or (scope and contact.admin_id != scope):
        raise HTTPException(404, "Contact not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(contact, field, value)
    db.commit()
    db.refresh(contact)
    return contact


@router.post("/{contact_id}/notes", response_model=ContactOut)
def add_note(contact_id: uuid.UUID, payload: AddNoteRequest, db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    contact = db.get(Contact, contact_id)
    if not contact or (scope and contact.admin_id != scope):
        raise HTTPException(404, "Contact not found")
    contact.notes = f"{contact.notes}\n{payload.note}" if contact.notes else payload.note
    db.commit()
    db.refresh(contact)
    return contact


@router.post("/{contact_id}/tags", response_model=ContactOut)
def add_tag(contact_id: uuid.UUID, payload: AddTagRequest, db: Session = Depends(get_db), scope: uuid.UUID | None = Depends(admin_scope_filter)):
    contact = db.get(Contact, contact_id)
    if not contact or (scope and contact.admin_id != scope):
        raise HTTPException(404, "Contact not found")
    if payload.tag not in contact.tags:
        contact.tags = [*contact.tags, payload.tag]
    db.commit()
    db.refresh(contact)
    return contact
