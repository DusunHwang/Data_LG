"""Application version helpers."""

from functools import lru_cache
from pathlib import Path


@lru_cache()
def get_app_version() -> str:
    version_file = Path(__file__).resolve().parents[3] / "VERSION"
    try:
        return version_file.read_text(encoding="utf-8").strip() or "0.1"
    except OSError:
        return "0.1"
