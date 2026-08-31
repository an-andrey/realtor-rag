from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "db" / "londono_properties.db"
DEFAULT_CHROMA_PATH = PROJECT_ROOT / "db" / "chroma"
DEFAULT_HANDOFF_DB_PATH = PROJECT_ROOT / "db" / "lead_handoffs.db"
DEFAULT_COLLECTION_NAME = "landono_properties"
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_NOMINATIM_USER_AGENT = "landono-realtor-assistant-poc/0.1"
DEFAULT_OPENAI_MODEL = "openai:gpt-4o-mini"
