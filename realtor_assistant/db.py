from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import DEFAULT_DB_PATH
from .geo import haversine_km, resolve_location


MAX_RECOMMENDATIONS = 5


@dataclass(frozen=True)
class ListingSearchParams:
    city: str | None = None
    transaction_type: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    min_bedrooms: int | None = None
    min_bathrooms: int | None = None
    property_type: str | None = None
    feature_terms: tuple[str, ...] = ()
    nearby_terms: tuple[str, ...] = ()
    text_query: str | None = None
    limit: int = MAX_RECOMMENDATIONS


@dataclass(frozen=True)
class NearbyListingSearchParams:
    location_query: str
    radius_km: float = 5
    filters: ListingSearchParams = ListingSearchParams()


def connect_readonly(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path).resolve()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_transaction_type(value: str | None) -> str | None:
    if not value:
        return None

    cleaned = value.strip().lower()
    if cleaned in {"buy", "sale", "sell", "purchase", "seller", "for sale"}:
        return "buy"
    if cleaned in {"rent", "rental", "lease", "for rent"}:
        return "rent"
    return cleaned


def search_listings(
    params: ListingSearchParams,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    limit = max(1, min(params.limit, MAX_RECOMMENDATIONS))
    where, values = _listing_where_clause(params)

    sql = """
        SELECT
            p.centris_id, p.address, p.street_address, p.city_region, p.price,
            p.price_amount, p.bedrooms, p.bathrooms, p.powder_rooms,
            p.property_type, p.transaction_type, p.url, p.description,
            p.latitude, p.longitude,
            GROUP_CONCAT(DISTINCT CASE WHEN pf.key = 'proximity' THEN pf.value END) AS nearby,
            GROUP_CONCAT(DISTINCT CASE WHEN pf.key IN ('parking', 'garage', 'pool', 'available_services')
                THEN pf.label || ': ' || pf.value END) AS highlights
        FROM properties p
        LEFT JOIN property_feature_values pf ON pf.centris_id = p.centris_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += """
        GROUP BY p.centris_id
        ORDER BY p.price_amount IS NULL, p.price_amount ASC
        LIMIT ?
    """
    values.append(limit)

    with connect_readonly(db_path) as conn:
        return [_clean_listing(row) for row in conn.execute(sql, values).fetchall()]


def count_listings(
    params: ListingSearchParams,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> int:
    where, values = _listing_where_clause(params)
    sql = "SELECT COUNT(DISTINCT p.centris_id) AS count FROM properties p"
    if where:
        sql += " WHERE " + " AND ".join(where)

    with connect_readonly(db_path) as conn:
        row = conn.execute(sql, values).fetchone()
    return int(row["count"] if row else 0)


def inspect_listing_pool(
    params: ListingSearchParams,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    return {
        "count": count_listings(params, db_path=db_path),
        "facets": suggest_filter_facets(
            db_path=db_path,
            city=params.city,
            transaction_type=params.transaction_type,
        ),
    }


def get_listing_details(
    centris_id: str,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict[str, Any] | None:
    with connect_readonly(db_path) as conn:
        listing = conn.execute(
            "SELECT * FROM properties WHERE centris_id = ?",
            (centris_id,),
        ).fetchone()
        if listing is None:
            return None

        brokers = conn.execute(
            """
            SELECT b.broker_id, b.name, b.title, b.phone, b.email, b.profile_url
            FROM brokers b
            JOIN property_brokers pb ON pb.broker_id = b.broker_id
            WHERE pb.centris_id = ?
            ORDER BY pb.sort_order
            """,
            (centris_id,),
        ).fetchall()
        features = conn.execute(
            """
            SELECT section_title, label, value
            FROM property_feature_values
            WHERE centris_id = ?
            ORDER BY section_title, sort_order
            """,
            (centris_id,),
        ).fetchall()
        images = conn.execute(
            """
            SELECT image_url
            FROM property_images
            WHERE centris_id = ?
            ORDER BY sort_order
            LIMIT 8
            """,
            (centris_id,),
        ).fetchall()

    data = _clean_listing(listing)
    data["brokers"] = [dict(row) for row in brokers]
    data["features"] = [dict(row) for row in features]
    data["image_urls"] = [row["image_url"] for row in images]
    return data


def search_listings_near(
    params: NearbyListingSearchParams,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    origin = resolve_location(params.location_query, db_path=db_path)
    if origin is None:
        return {
            "status": "location_not_found",
            "location_query": params.location_query,
            "listings": [],
        }

    broad_filters = ListingSearchParams(
        city=params.filters.city,
        transaction_type=params.filters.transaction_type,
        min_price=params.filters.min_price,
        max_price=params.filters.max_price,
        min_bedrooms=params.filters.min_bedrooms,
        min_bathrooms=params.filters.min_bathrooms,
        property_type=params.filters.property_type,
        feature_terms=params.filters.feature_terms,
        nearby_terms=params.filters.nearby_terms,
        text_query=params.filters.text_query,
        limit=MAX_RECOMMENDATIONS,
    )
    candidates = _search_listings_for_distance(broad_filters, db_path=db_path)
    listings: list[dict[str, Any]] = []
    for listing in candidates:
        if listing.get("latitude") is None or listing.get("longitude") is None:
            continue
        distance = haversine_km(
            origin.latitude,
            origin.longitude,
            float(listing["latitude"]),
            float(listing["longitude"]),
        )
        if distance <= params.radius_km:
            listing["distance_km"] = round(distance, 2)
            listings.append(listing)

    listings.sort(key=lambda item: (item["distance_km"], item.get("price_amount") or 0))
    return {
        "status": "ok",
        "origin": {
            "label": origin.label,
            "latitude": origin.latitude,
            "longitude": origin.longitude,
            "source": origin.source,
        },
        "radius_km": params.radius_km,
        "listings": listings[:MAX_RECOMMENDATIONS],
    }


def suggest_filter_facets(
    db_path: Path | str = DEFAULT_DB_PATH,
    city: str | None = None,
    transaction_type: str | None = None,
) -> dict[str, Any]:
    where: list[str] = []
    aliased_where: list[str] = []
    values: list[Any] = []
    if city:
        where.append("LOWER(city_region) LIKE ?")
        aliased_where.append("LOWER(p.city_region) LIKE ?")
        values.append(f"%{city.strip().lower()}%")
    if transaction_type:
        where.append("transaction_type = ?")
        aliased_where.append("p.transaction_type = ?")
        values.append(normalize_transaction_type(transaction_type))
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    aliased_where_sql = " AND " + " AND ".join(aliased_where) if aliased_where else ""

    with connect_readonly(db_path) as conn:
        cities = conn.execute(
            f"SELECT city_region AS value, COUNT(*) AS count FROM properties{where_sql} "
            "GROUP BY city_region ORDER BY count DESC LIMIT 10",
            values,
        ).fetchall()
        prices = conn.execute(
            f"SELECT MIN(price_amount) AS min_price, MAX(price_amount) AS max_price FROM properties{where_sql}",
            values,
        ).fetchone()
        types = conn.execute(
            f"SELECT property_type AS value, COUNT(*) AS count FROM properties{where_sql} "
            "GROUP BY property_type ORDER BY count DESC",
            values,
        ).fetchall()
        proximity = conn.execute(
            """
            SELECT pf.value, COUNT(*) AS count
            FROM property_feature_values pf
            JOIN properties p ON p.centris_id = pf.centris_id
            WHERE pf.key = 'proximity'
            """
            + aliased_where_sql
            + " GROUP BY pf.value ORDER BY count DESC LIMIT 10",
            values,
        ).fetchall()

    return {
        "cities": [dict(row) for row in cities],
        "price_range": dict(prices) if prices else {},
        "property_types": [dict(row) for row in types],
        "nearby_options": [dict(row) for row in proximity],
    }


def listing_document(row: sqlite3.Row | dict[str, Any]) -> str:
    data = dict(row)
    parts = [
        f"Centris ID: {data.get('centris_id')}",
        f"Address: {data.get('address')}",
        f"City/region: {data.get('city_region')}",
        f"Transaction: {data.get('transaction_type')}",
        f"Price: {data.get('price')}",
        f"Bedrooms: {data.get('bedrooms')}",
        f"Bathrooms: {data.get('bathrooms')}",
        f"Property type: {data.get('property_type')}",
        f"Description: {data.get('description') or ''}",
        f"Addendums: {data.get('addendums') or ''}",
        f"Nearby: {_jsonish(data.get('nearby_json'))}",
        f"Features: {_jsonish(data.get('features_json'))}",
    ]
    return "\n".join(part for part in parts if part)


def _search_listings_for_distance(
    params: ListingSearchParams,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    uncapped_params = ListingSearchParams(**{**params.__dict__, "limit": 1000})
    where: list[str] = ["p.latitude IS NOT NULL", "p.longitude IS NOT NULL"]
    values: list[Any] = []

    if uncapped_params.city:
        where.append("LOWER(p.city_region) LIKE ?")
        values.append(f"%{uncapped_params.city.strip().lower()}%")
    if uncapped_params.transaction_type:
        where.append("p.transaction_type = ?")
        values.append(normalize_transaction_type(uncapped_params.transaction_type))
    if uncapped_params.min_price is not None:
        where.append("p.price_amount >= ?")
        values.append(uncapped_params.min_price)
    if uncapped_params.max_price is not None:
        where.append("p.price_amount <= ?")
        values.append(uncapped_params.max_price)
    if uncapped_params.min_bedrooms is not None:
        where.append("CAST(NULLIF(p.bedrooms, '') AS INTEGER) >= ?")
        values.append(uncapped_params.min_bedrooms)
    if uncapped_params.min_bathrooms is not None:
        where.append("CAST(NULLIF(p.bathrooms, '') AS INTEGER) >= ?")
        values.append(uncapped_params.min_bathrooms)
    if uncapped_params.property_type:
        where.append("LOWER(p.property_type) LIKE ?")
        values.append(f"%{uncapped_params.property_type.strip().lower()}%")

    for term in uncapped_params.feature_terms:
        where.append(
            "EXISTS ("
            "SELECT 1 FROM property_feature_values pf "
            "WHERE pf.centris_id = p.centris_id "
            "AND LOWER(COALESCE(pf.value, '') || ' ' || COALESCE(pf.label, '')) LIKE ?"
            ")"
        )
        values.append(f"%{term.strip().lower()}%")

    sql = """
        SELECT
            p.centris_id, p.address, p.street_address, p.city_region, p.price,
            p.price_amount, p.bedrooms, p.bathrooms, p.powder_rooms,
            p.property_type, p.transaction_type, p.url, p.description,
            p.latitude, p.longitude
        FROM properties p
        WHERE
    """ + " AND ".join(where)

    with connect_readonly(db_path) as conn:
        return [_clean_listing(row) for row in conn.execute(sql, values).fetchall()]


def _listing_where_clause(params: ListingSearchParams) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    values: list[Any] = []

    if params.city:
        where.append("LOWER(p.city_region) LIKE ?")
        values.append(f"%{params.city.strip().lower()}%")
    if params.transaction_type:
        where.append("p.transaction_type = ?")
        values.append(normalize_transaction_type(params.transaction_type))
    if params.min_price is not None:
        where.append("p.price_amount >= ?")
        values.append(params.min_price)
    if params.max_price is not None:
        where.append("p.price_amount <= ?")
        values.append(params.max_price)
    if params.min_bedrooms is not None:
        where.append("CAST(NULLIF(p.bedrooms, '') AS INTEGER) >= ?")
        values.append(params.min_bedrooms)
    if params.min_bathrooms is not None:
        where.append("CAST(NULLIF(p.bathrooms, '') AS INTEGER) >= ?")
        values.append(params.min_bathrooms)
    if params.property_type:
        where.append("LOWER(p.property_type) LIKE ?")
        values.append(f"%{params.property_type.strip().lower()}%")
    if params.text_query:
        text = f"%{params.text_query.strip().lower()}%"
        where.append(
            "(LOWER(COALESCE(p.address, '')) LIKE ? "
            "OR LOWER(COALESCE(p.description, '')) LIKE ? "
            "OR LOWER(COALESCE(p.addendums, '')) LIKE ?)"
        )
        values.extend([text, text, text])

    for term in params.feature_terms:
        where.append(
            "EXISTS ("
            "SELECT 1 FROM property_feature_values pf "
            "WHERE pf.centris_id = p.centris_id "
            "AND LOWER(COALESCE(pf.value, '') || ' ' || COALESCE(pf.label, '')) LIKE ?"
            ")"
        )
        values.append(f"%{term.strip().lower()}%")

    for term in params.nearby_terms:
        where.append(
            "EXISTS ("
            "SELECT 1 FROM property_feature_values pf "
            "WHERE pf.centris_id = p.centris_id "
            "AND pf.key = 'proximity' "
            "AND LOWER(COALESCE(pf.value, '')) LIKE ?"
            ")"
        )
        values.append(f"%{term.strip().lower()}%")

    return where, values


def iter_listing_documents(db_path: Path | str = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    with connect_readonly(db_path) as conn:
        rows = conn.execute("SELECT * FROM properties ORDER BY centris_id").fetchall()
    return [
        {
            "id": row["centris_id"],
            "text": listing_document(row),
            "metadata": {
                "centris_id": row["centris_id"],
                "city_region": row["city_region"] or "",
                "transaction_type": row["transaction_type"] or "",
                "price_amount": row["price_amount"] or 0,
                "property_type": row["property_type"] or "",
                "url": row["url"] or "",
            },
        }
        for row in rows
    ]


def _clean_listing(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in ("nearby", "highlights"):
        if key in data and isinstance(data[key], str):
            data[key] = [item for item in data[key].split(",") if item]
    data["short_description"] = _squash(data.get("description") or "")[:320]
    return data


def _jsonish(value: Any) -> str:
    if not value:
        return ""
    if not isinstance(value, str):
        return str(value)
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return _squash(value)
    return _squash(json.dumps(parsed, ensure_ascii=False))


def _squash(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
