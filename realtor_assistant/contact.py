from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DEFAULT_HANDOFF_DB_PATH


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_DIGIT_RE = re.compile(r"\d")


@dataclass(frozen=True)
class ValidatedContact:
    contact_token: str
    contact_name: str
    contact_email: str | None
    contact_phone: str | None
    preferred_contact_method: str | None
    capture_source: str


def ensure_contact_schema(db_path: Path | str = DEFAULT_HANDOFF_DB_PATH) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contact_tokens (
                contact_token TEXT PRIMARY KEY,
                contact_name TEXT NOT NULL,
                contact_email TEXT,
                contact_phone TEXT,
                preferred_contact_method TEXT,
                capture_source TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


def create_contact_token(
    *,
    contact_name: str,
    contact_email: str | None = None,
    contact_phone: str | None = None,
    preferred_contact_method: str | None = None,
    capture_source: str = "application_capture",
    db_path: Path | str = DEFAULT_HANDOFF_DB_PATH,
) -> dict[str, Any]:
    ensure_contact_schema(db_path)
    errors = validate_contact_fields(contact_name, contact_email, contact_phone)
    if errors:
        return {"status": "validation_failed", "errors": errors}

    normalized_phone = normalize_phone(contact_phone) if contact_phone else None
    token = str(uuid.uuid4())
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO contact_tokens (
                contact_token, contact_name, contact_email, contact_phone,
                preferred_contact_method, capture_source, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                token,
                contact_name.strip(),
                contact_email.strip().lower() if contact_email else None,
                normalized_phone,
                preferred_contact_method,
                capture_source,
                _utc_now(),
            ),
        )

    return {
        "status": "ok",
        "contact_token": token,
        "contact_name": contact_name.strip(),
        "contact_email": contact_email.strip().lower() if contact_email else None,
        "contact_phone": normalized_phone,
        "preferred_contact_method": preferred_contact_method,
        "capture_source": capture_source,
    }


def get_validated_contact(
    contact_token: str,
    db_path: Path | str = DEFAULT_HANDOFF_DB_PATH,
) -> ValidatedContact | None:
    ensure_contact_schema(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT contact_token, contact_name, contact_email, contact_phone,
                   preferred_contact_method, capture_source
            FROM contact_tokens
            WHERE contact_token = ?
            """,
            (contact_token,),
        ).fetchone()
    if row is None:
        return None
    return ValidatedContact(
        contact_token=row["contact_token"],
        contact_name=row["contact_name"],
        contact_email=row["contact_email"],
        contact_phone=row["contact_phone"],
        preferred_contact_method=row["preferred_contact_method"],
        capture_source=row["capture_source"],
    )


def validate_contact_fields(
    contact_name: str | None,
    contact_email: str | None,
    contact_phone: str | None,
) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not contact_name or len(contact_name.strip()) < 2:
        errors["contact_name"] = "Name is required."
    if not contact_email and not contact_phone:
        errors["contact"] = "At least one of email or phone is required."
    if contact_email and not EMAIL_RE.match(contact_email.strip()):
        errors["contact_email"] = "Email address is not valid."
    if contact_phone:
        digits = "".join(PHONE_DIGIT_RE.findall(contact_phone))
        if len(digits) < 10 or len(digits) > 15:
            errors["contact_phone"] = "Phone number must contain 10 to 15 digits."
    return errors


def normalize_phone(contact_phone: str | None) -> str | None:
    if not contact_phone:
        return None
    digits = "".join(PHONE_DIGIT_RE.findall(contact_phone))
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if contact_phone.strip().startswith("+"):
        return "+" + digits
    return digits


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
