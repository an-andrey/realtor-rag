from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DEFAULT_DB_PATH, DEFAULT_HANDOFF_DB_PATH
from .contact import get_validated_contact
from .db import connect_readonly


def ensure_handoff_schema(db_path: Path | str = DEFAULT_HANDOFF_DB_PATH) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lead_handoffs (
                handoff_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                intent TEXT NOT NULL,
                lead_type TEXT NOT NULL DEFAULT 'buyer',
                channel TEXT,
                contact_name TEXT,
                contact_email TEXT,
                contact_phone TEXT,
                preferred_contact_method TEXT,
                message TEXT NOT NULL,
                centris_ids_json TEXT NOT NULL,
                broker_id TEXT,
                broker_name TEXT,
                broker_phone TEXT,
                broker_email TEXT,
                source_payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(lead_handoffs)").fetchall()
        }
        if "lead_type" not in columns:
            conn.execute(
                "ALTER TABLE lead_handoffs ADD COLUMN lead_type TEXT NOT NULL DEFAULT 'buyer'"
            )


def create_broker_handoff(
    *,
    message: str,
    intent: str,
    lead_type: str = "buyer",
    contact_token: str | None = None,
    channel: str | None = None,
    centris_ids: list[str] | None = None,
    broker_id: str | None = None,
    listings_db_path: Path | str = DEFAULT_DB_PATH,
    handoff_db_path: Path | str = DEFAULT_HANDOFF_DB_PATH,
) -> dict[str, Any]:
    ensure_handoff_schema(handoff_db_path)
    if not contact_token:
        return {
            "status": "contact_required",
            "message": "A validated contact token is required before creating a broker handoff.",
        }

    contact = get_validated_contact(contact_token, db_path=handoff_db_path)
    if contact is None:
        return {
            "status": "invalid_contact_token",
            "message": "The provided contact token was not found. Capture contact details again.",
        }

    centris_ids = centris_ids or []
    broker = _select_broker(
        centris_ids=centris_ids,
        broker_id=broker_id,
        listings_db_path=listings_db_path,
    )
    status = "pending_delivery" if broker else "needs_manual_routing"
    handoff_id = str(uuid.uuid4())
    payload = {
        "handoff_id": handoff_id,
        "status": status,
        "intent": intent,
        "lead_type": lead_type,
        "channel": channel,
        "contact_name": contact.contact_name,
        "contact_email": contact.contact_email,
        "contact_phone": contact.contact_phone,
        "preferred_contact_method": contact.preferred_contact_method,
        "contact_token": contact.contact_token,
        "contact_capture_source": contact.capture_source,
        "message": message,
        "centris_ids": centris_ids,
        "broker": broker,
        "created_at": _utc_now(),
    }

    with sqlite3.connect(handoff_db_path) as conn:
        conn.execute(
            """
            INSERT INTO lead_handoffs (
                handoff_id, status, intent, lead_type, channel, contact_name, contact_email,
                contact_phone, preferred_contact_method, message, centris_ids_json,
                broker_id, broker_name, broker_phone, broker_email,
                source_payload_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                handoff_id,
                status,
                intent,
                lead_type,
                channel,
                contact.contact_name,
                contact.contact_email,
                contact.contact_phone,
                contact.preferred_contact_method,
                message,
                json.dumps(centris_ids),
                broker["broker_id"] if broker else None,
                broker["name"] if broker else None,
                broker["phone"] if broker else None,
                broker["email"] if broker else None,
                json.dumps(payload, ensure_ascii=False),
                payload["created_at"],
            ),
        )

    payload["delivery_note"] = (
        "Broker contact was selected from the listings database."
        if broker
        else "No broker was deterministically selected. Route this lead to an admin queue."
    )
    return payload


def _select_broker(
    *,
    centris_ids: list[str],
    broker_id: str | None,
    listings_db_path: Path | str,
) -> dict[str, Any] | None:
    with connect_readonly(listings_db_path) as conn:
        if broker_id:
            row = conn.execute(
                """
                SELECT broker_id, name, title, phone, email, profile_url
                FROM brokers
                WHERE broker_id = ?
                """,
                (broker_id,),
            ).fetchone()
            return dict(row) if row else None

        for centris_id in centris_ids:
            row = conn.execute(
                """
                SELECT b.broker_id, b.name, b.title, b.phone, b.email, b.profile_url
                FROM brokers b
                JOIN property_brokers pb ON pb.broker_id = b.broker_id
                WHERE pb.centris_id = ?
                ORDER BY pb.sort_order
                LIMIT 1
                """,
                (centris_id,),
            ).fetchone()
            if row:
                return dict(row)
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
