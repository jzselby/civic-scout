"""Runtime settings, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _list(value: str | None) -> tuple[str, ...]:
    return tuple(v.strip() for v in (value or "").split(",") if v.strip())


@dataclass
class Config:
    # Sources to run; empty means every source enabled by default.
    sources: tuple[str, ...] = ()
    # Items dated before this many days ago are recorded as seen but not reported,
    # so a source's first run doesn't report its whole history.
    days_back: int = 14
    data_dir: Path = Path("data")
    reports_dir: Path = Path("reports")
    debug_dir: Path = Path("debug")
    model: str = "claude-opus-5-5"
    google_sheet_id: str | None = None
    google_service_account_json: str | None = None
    # Per-source settings (comma-separated env vars); empty means the source's defaults.
    pmn_bodies: tuple[str, ...] = ()
    slcbuilding_url: str = "https://raw.githubusercontent.com/jzselby/slcbuilding/main/data/permits.jsonl"
    http_timeout: int = 60
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ
        return cls(
            sources=_list(env.get("SOURCES")),
            days_back=int(env.get("DAYS_BACK") or 14),
            data_dir=Path(env.get("DATA_DIR", "data")),
            reports_dir=Path(env.get("REPORTS_DIR", "reports")),
            debug_dir=Path(env.get("DEBUG_DIR", "debug")),
            model=env.get("SUMMARY_MODEL") or "claude-opus-5-5",
            google_sheet_id=env.get("GOOGLE_SHEET_ID") or None,
            google_service_account_json=env.get("GOOGLE_SERVICE_ACCOUNT_JSON") or None,
            pmn_bodies=_list(env.get("PMN_BODIES")),
            slcbuilding_url=env.get("SLCBUILDING_URL") or cls.slcbuilding_url,
            http_timeout=int(env.get("HTTP_TIMEOUT") or 60),
        )
