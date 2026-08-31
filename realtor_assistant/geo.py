from __future__ import annotations

import json
import math
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DEFAULT_DB_PATH, DEFAULT_NOMINATIM_USER_AGENT
from .env import load_project_env


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


@dataclass(frozen=True)
class Coordinates:
    latitude: float
    longitude: float
    label: str
    source: str


LANDMARKS = [
    {
        "name": "McGill University",
        "aliases": ["mcgill", "mcgill university"],
        "latitude": 45.5048,
        "longitude": -73.5772,
        "kind": "university",
    },
    {
        "name": "Concordia University Sir George Williams Campus",
        "aliases": ["concordia", "concordia university"],
        "latitude": 45.4971,
        "longitude": -73.5788,
        "kind": "university",
    },
    {
        "name": "Université de Montréal",
        "aliases": ["udem", "universite de montreal", "université de montréal"],
        "latitude": 45.5049,
        "longitude": -73.6146,
        "kind": "university",
    },
    {
        "name": "UQAM",
        "aliases": ["uqam", "universite du quebec a montreal", "université du québec à montréal"],
        "latitude": 45.5152,
        "longitude": -73.5614,
        "kind": "university",
    },
]


def connect_writable(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_location_schema(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    with connect_writable(db_path) as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(properties)").fetchall()
        }
        additions = {
            "latitude": "REAL",
            "longitude": "REAL",
            "geocoded_at": "TEXT",
            "geocoding_provider": "TEXT",
            "geocoding_query": "TEXT",
        }
        for column, column_type in additions.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE properties ADD COLUMN {column} {column_type}")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS location_cache (
                query TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                source TEXT NOT NULL,
                raw_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS landmarks (
                landmark_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                aliases_json TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                kind TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        seed_landmarks(conn)


def seed_landmarks(conn: sqlite3.Connection) -> None:
    now = _utc_now()
    for landmark in LANDMARKS:
        conn.execute(
            """
            INSERT INTO landmarks (name, aliases_json, latitude, longitude, kind, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                aliases_json = excluded.aliases_json,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                kind = excluded.kind
            """,
            (
                landmark["name"],
                json.dumps(landmark["aliases"], ensure_ascii=False),
                landmark["latitude"],
                landmark["longitude"],
                landmark["kind"],
                now,
            ),
        )


def resolve_location(
    query: str,
    db_path: Path | str = DEFAULT_DB_PATH,
    allow_external_geocode: bool = True,
) -> Coordinates | None:
    ensure_location_schema(db_path)
    normalized = _normalize_query(query)
    with connect_writable(db_path) as conn:
        landmark = _find_landmark(conn, normalized)
        if landmark:
            return landmark

        cached = conn.execute(
            "SELECT label, latitude, longitude, source FROM location_cache WHERE query = ?",
            (normalized,),
        ).fetchone()
        if cached:
            return Coordinates(
                latitude=cached["latitude"],
                longitude=cached["longitude"],
                label=cached["label"],
                source=cached["source"],
            )

    if not allow_external_geocode:
        return None

    result = geocode_with_nominatim(query)
    if result is None:
        return None

    with connect_writable(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO location_cache
                (query, label, latitude, longitude, source, raw_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized,
                result.label,
                result.latitude,
                result.longitude,
                result.source,
                result.raw_json,
                _utc_now(),
            ),
        )
    return Coordinates(result.latitude, result.longitude, result.label, result.source)


@dataclass(frozen=True)
class GeocodeResult:
    latitude: float
    longitude: float
    label: str
    source: str
    raw_json: str


def geocode_with_nominatim(query: str) -> GeocodeResult | None:
    load_project_env()
    params = urllib.parse.urlencode(
        {
            "q": query,
            "format": "jsonv2",
            "limit": 1,
            "countrycodes": "ca",
            "addressdetails": 1,
        }
    )
    user_agent = os.getenv("LANDONO_NOMINATIM_USER_AGENT", DEFAULT_NOMINATIM_USER_AGENT)
    request = urllib.request.Request(
        f"{NOMINATIM_URL}?{params}",
        headers={"User-Agent": user_agent},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not payload:
        return None

    first = payload[0]
    return GeocodeResult(
        latitude=float(first["lat"]),
        longitude=float(first["lon"]),
        label=first.get("display_name") or query,
        source="nominatim",
        raw_json=json.dumps(first, ensure_ascii=False),
    )


def geocode_missing_properties(
    db_path: Path | str = DEFAULT_DB_PATH,
    limit: int | None = None,
    sleep_seconds: float = 1.1,
) -> int:
    ensure_location_schema(db_path)
    sql = """
        SELECT centris_id, geocode_address, address
        FROM properties
        WHERE latitude IS NULL OR longitude IS NULL
        ORDER BY centris_id
    """
    values: list[Any] = []
    if limit is not None:
        sql += " LIMIT ?"
        values.append(limit)

    with connect_writable(db_path) as conn:
        rows = conn.execute(sql, values).fetchall()

    updated = 0
    for row in rows:
        query = row["geocode_address"] or row["address"]
        if not query:
            continue

        result = resolve_location(query, db_path=db_path, allow_external_geocode=True)
        if result is None:
            continue

        with connect_writable(db_path) as conn:
            conn.execute(
                """
                UPDATE properties
                SET latitude = ?, longitude = ?, geocoded_at = ?,
                    geocoding_provider = ?, geocoding_query = ?
                WHERE centris_id = ?
                """,
                (
                    result.latitude,
                    result.longitude,
                    _utc_now(),
                    result.source,
                    query,
                    row["centris_id"],
                ),
            )
        updated += 1
        time.sleep(sleep_seconds)
    return updated


def haversine_km(
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
) -> float:
    radius_km = 6371.0088
    lat1 = math.radians(origin_latitude)
    lat2 = math.radians(destination_latitude)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(destination_longitude - origin_longitude)
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _find_landmark(conn: sqlite3.Connection, normalized_query: str) -> Coordinates | None:
    rows = conn.execute(
        "SELECT name, aliases_json, latitude, longitude FROM landmarks"
    ).fetchall()
    for row in rows:
        aliases = json.loads(row["aliases_json"])
        candidates = [row["name"], *aliases]
        if any(_normalize_query(candidate) == normalized_query for candidate in candidates):
            return Coordinates(row["latitude"], row["longitude"], row["name"], "landmark")
    return None


def _normalize_query(query: str) -> str:
    return " ".join(query.strip().casefold().split())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    ensure_location_schema()
    count = geocode_missing_properties()
    print(f"Geocoded {count} properties")
