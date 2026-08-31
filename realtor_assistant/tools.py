from __future__ import annotations

from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from .db import (
    MAX_RECOMMENDATIONS,
    ListingSearchParams,
    NearbyListingSearchParams,
    get_listing_details,
    inspect_listing_pool,
    search_listings,
    search_listings_near,
    suggest_filter_facets,
)
from .handoff import create_broker_handoff
from .vectorstore import semantic_property_search


class SearchListingsInput(BaseModel):
    city: str | None = Field(default=None, description="City, borough, or region text.")
    transaction_type: Literal["buy", "rent"] | None = Field(default=None)
    min_price: float | None = None
    max_price: float | None = None
    min_bedrooms: int | None = None
    min_bathrooms: int | None = None
    property_type: str | None = None
    feature_terms: list[str] = Field(default_factory=list)
    nearby_terms: list[str] = Field(default_factory=list)
    text_query: str | None = None
    limit: int = Field(default=MAX_RECOMMENDATIONS, le=MAX_RECOMMENDATIONS)


class InspectListingPoolInput(BaseModel):
    city: str | None = Field(default=None, description="City, borough, or region text.")
    transaction_type: Literal["buy", "rent"] | None = Field(default=None)
    min_price: float | None = None
    max_price: float | None = None
    min_bedrooms: int | None = None
    min_bathrooms: int | None = None
    property_type: str | None = None
    feature_terms: list[str] = Field(default_factory=list)
    nearby_terms: list[str] = Field(default_factory=list)
    text_query: str | None = None


class ListingDetailsInput(BaseModel):
    centris_id: str


class SemanticSearchInput(BaseModel):
    query: str = Field(description="Natural-language location, lifestyle, or listing question.")
    n_results: int = Field(default=5, ge=1, le=8)


class NearbyListingsInput(SearchListingsInput):
    location_query: str = Field(description="Landmark, campus, station, or address to search near.")
    radius_km: float = Field(default=5, gt=0, le=50)


class FilterFacetsInput(BaseModel):
    city: str | None = None
    transaction_type: Literal["buy", "rent"] | None = None


class HandoffInput(BaseModel):
    contact_token: str = Field(
        description="Validated contact token created by application code. Do not invent this value."
    )
    message: str = Field(description="What the broker should know before contacting the lead.")
    centris_ids: list[str] = Field(default_factory=list)
    broker_id: str | None = Field(
        default=None,
        description="Optional broker_id from the database. Do not invent this value.",
    )
    channel: str | None = Field(default=None, description="sms, instagram, website, facebook, etc.")
    intent: Literal[
        "book_showing",
        "broker_followup",
        "needs_advice",
        "sell_property",
        "rent_out_property",
    ] = "broker_followup"
    lead_type: Literal["buyer", "renter", "seller", "landlord", "undecided"] = "undecided"


@tool(args_schema=SearchListingsInput)
def search_landono_listings(**kwargs) -> list[dict]:
    """Search Landono listings with strict filters. Use this before recommending properties."""
    params = ListingSearchParams(
        city=kwargs.get("city"),
        transaction_type=kwargs.get("transaction_type"),
        min_price=kwargs.get("min_price"),
        max_price=kwargs.get("max_price"),
        min_bedrooms=kwargs.get("min_bedrooms"),
        min_bathrooms=kwargs.get("min_bathrooms"),
        property_type=kwargs.get("property_type"),
        feature_terms=tuple(kwargs.get("feature_terms") or ()),
        nearby_terms=tuple(kwargs.get("nearby_terms") or ()),
        text_query=kwargs.get("text_query"),
        limit=kwargs.get("limit") or MAX_RECOMMENDATIONS,
    )
    return search_listings(params)


@tool(args_schema=InspectListingPoolInput)
def inspect_landono_listing_pool(**kwargs) -> dict:
    """Count matching Landono listings and return useful facets before asking for optional filters."""
    params = ListingSearchParams(
        city=kwargs.get("city"),
        transaction_type=kwargs.get("transaction_type"),
        min_price=kwargs.get("min_price"),
        max_price=kwargs.get("max_price"),
        min_bedrooms=kwargs.get("min_bedrooms"),
        min_bathrooms=kwargs.get("min_bathrooms"),
        property_type=kwargs.get("property_type"),
        feature_terms=tuple(kwargs.get("feature_terms") or ()),
        nearby_terms=tuple(kwargs.get("nearby_terms") or ()),
        text_query=kwargs.get("text_query"),
    )
    return inspect_listing_pool(params)


@tool(args_schema=ListingDetailsInput)
def get_landono_listing_details(centris_id: str) -> dict | None:
    """Get full read-only details, brokers, features, and images for one listing."""
    return get_listing_details(centris_id)


@tool(args_schema=SemanticSearchInput)
def semantic_landono_property_search(query: str, n_results: int = 5) -> list[dict]:
    """Use Chroma RAG for fuzzy questions about location, lifestyle, proximity, and listing text."""
    return semantic_property_search(query=query, n_results=n_results)


@tool(args_schema=NearbyListingsInput)
def find_landono_listings_near(**kwargs) -> dict:
    """Find filtered listings within a straight-line radius of a landmark, campus, station, or address."""
    filters = ListingSearchParams(
        city=kwargs.get("city"),
        transaction_type=kwargs.get("transaction_type"),
        min_price=kwargs.get("min_price"),
        max_price=kwargs.get("max_price"),
        min_bedrooms=kwargs.get("min_bedrooms"),
        min_bathrooms=kwargs.get("min_bathrooms"),
        property_type=kwargs.get("property_type"),
        feature_terms=tuple(kwargs.get("feature_terms") or ()),
        nearby_terms=tuple(kwargs.get("nearby_terms") or ()),
        text_query=kwargs.get("text_query"),
        limit=MAX_RECOMMENDATIONS,
    )
    return search_listings_near(
        NearbyListingSearchParams(
            location_query=kwargs["location_query"],
            radius_km=kwargs.get("radius_km") or 5,
            filters=filters,
        )
    )


@tool(args_schema=FilterFacetsInput)
def suggest_landono_filters(city: str | None = None, transaction_type: str | None = None) -> dict:
    """Suggest useful narrowing filters such as price range, property type, and nearby options."""
    return suggest_filter_facets(city=city, transaction_type=transaction_type)


@tool(args_schema=HandoffInput)
def prepare_broker_handoff(**kwargs) -> dict:
    """Persist a lead handoff using broker contact details selected only from the database."""
    return create_broker_handoff(**kwargs)


LANDONO_TOOLS = [
    inspect_landono_listing_pool,
    search_landono_listings,
    get_landono_listing_details,
    semantic_landono_property_search,
    find_landono_listings_near,
    suggest_landono_filters,
    prepare_broker_handoff,
]
