"""What every source provides.

A source fetches records and returns them as plain dicts ("items"). Required keys:

    id      unique and stable within the source (a record or notice number)
    title   short label as the publisher wrote it
    date    ISO date (YYYY-MM-DD) the record was filed, posted or takes effect

Optional keys the rest of the pipeline understands:

    url     link to the original record
    org     who filed it or who it is about (public body, employer, business)
    place   street address or location as written
    text    raw text for Claude to read (not stored)
    details short "label: value" facts shown in the sheet
    prerated  True when the item already carries importance/category/why_it_matters
              (e.g. imported from another pipeline), so Claude doesn't re-rate it

`guidance` is appended to Claude's instructions: what in this source is
newsworthy, in an editor's terms.
"""

from __future__ import annotations

from typing import Protocol

from ..config import Config
from ..http import Http


class Source(Protocol):
    name: str          # short key, used for data/<name>.jsonl
    label: str         # human name shown to journalists
    guidance: str
    default_enabled: bool

    def fetch(self, cfg: Config, http: Http, seen: set[str]) -> list[dict]:
        """Return current items. `seen` holds ids already recorded, so a source can
        skip fetching detail pages for them."""
        ...

    def probe(self, cfg: Config, http: Http) -> dict[str, bytes]:
        """Raw responses (filename -> bytes) for checking the parser against the live site."""
        ...
