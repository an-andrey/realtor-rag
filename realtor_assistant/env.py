from __future__ import annotations

from dotenv import load_dotenv

from .config import PROJECT_ROOT


def load_project_env() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
