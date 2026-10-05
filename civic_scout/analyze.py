"""Claude: rate each new item for newsworthiness, then write the day's briefing."""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

Importance = Literal["high", "medium", "low"]
Category = Literal[
    "Development / housing", "Business", "Jobs / economy", "Government / politics", "Budget / taxes",
    "Education", "Public safety / courts", "Transportation", "Environment / water", "Alcohol / licensing",
    "Health", "Other",
]

RATE_PROMPT = """\
You are an editor screening new public records from Salt Lake City, Salt Lake County
and the state of Utah for a newsroom. Reporters on every beat use your ratings to
decide what to look at today, so judge news value for a general local audience:
money, power, safety, housing, jobs, change to neighborhoods, and anything likely to
draw public interest or controversy. Routine paperwork should stay out of their way.

Each input item has `source`, `key`, `title`, `date`, and may have `org` (who filed
it or who it's about), `place`, `details` and `text` (raw text of the record).

Return one entry per input item, with the same `key`:
- headline: a plain-English headline of at most 15 words saying what is new.
- importance: "high" (a reporter should look today), "medium" (worth a look),
  or "low" (routine). Use the source-specific guidance below.
- category: the single best fit.
- why_it_matters: one or two sentences giving the news angle: who, what, how much,
  where, when. For low items, a few words such as "Routine meeting minutes."
- names: businesses, developers, employers, officials or organizations named in the
  record that a reporter might cross-check (not the public body itself).
- places: street addresses in the record, as written (e.g. "707 W Genesee Ave").

Only state facts present in the record. Never guess numbers or names.

Source-specific guidance:

{guidance}
"""

BRIEF_PROMPT = """\
You write a morning briefing for a Utah newsroom from today's newly found public
records across many sources: meeting agendas, liquor licenses, layoff notices,
business licenses, building permits and planning applications. Reporters on every
beat read it, so lead with what matters most to the public.

You get the high- and medium-importance items (already rated, with headlines and
the news angle) and `connections`: items that share an address or a name with
another record, possibly from a different source or an earlier date.

Write GitHub-flavored Markdown with these sections:
**Top stories**: the 3 to 8 most newsworthy items, most important first. Each is one
bullet: a bold short headline, then one or two sentences with the specifics, and the
source and link in the form ([source label](url)).
**Connections**: where records from different sources point to the same project,
business or place (e.g. a planning application, a permit and a liquor license at
one address), explain what they add up to. Only connections that tell a reporter
something; omit the section if none do.
**Coming up**: meetings and hearings in the next two weeks worth attending, with
dates. Omit if none.
**Also notable**: up to 8 one-line bullets for the rest of the medium/high items.

Be concise and factual; don't speculate beyond the records. Don't repeat an item
in two sections.
"""


class ItemNotes(BaseModel):
    key: str
    headline: str
    importance: Importance
    category: Category
    why_it_matters: str
    names: list[str] = Field(default_factory=list)
    places: list[str] = Field(default_factory=list)


class Ratings(BaseModel):
    items: list[ItemNotes]


# Records per request are capped by payload size (~4 chars/token), well under the context window.
MAX_PAYLOAD_CHARS = 900_000
ITEM_TEXT_CHARS = 60_000


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _payload(item: dict) -> dict:
    out = {k: item.get(k) for k in ("source", "title", "date", "org", "place", "details") if item.get(k)}
    out["key"] = f"{item['source']}:{item['id']}"
    if item.get("text"):
        text = item["text"]
        out["text"] = text if len(text) <= ITEM_TEXT_CHARS else text[:ITEM_TEXT_CHARS] + " [...]"
    for extra in ("workers",):
        if item.get(extra) is not None:
            out[extra] = item[extra]
    return out


def _batches(payloads: list[dict]) -> list[list[dict]]:
    batches: list[list[dict]] = [[]]
    size = 0
    for p in payloads:
        n = len(json.dumps(p))
        if batches[-1] and size + n > MAX_PAYLOAD_CHARS:
            batches.append([])
            size = 0
        batches[-1].append(p)
        size += n
    return batches


def _call(client: anthropic.Anthropic, model: str, system: str, prompt: str, output_format=None,
          effort: str = "medium"):
    """One streamed request; returns the final message, or None on any API failure."""
    kwargs = dict(
        model=model,
        max_tokens=64000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        thinking={"type": "adaptive"},
        output_config={"effort": effort},
        # On a safety-classifier decline, retry server-side on Anthropic's recommended fallback model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if output_format is not None:
        kwargs["output_format"] = output_format
    try:
        with client.beta.messages.stream(**kwargs) as stream:
            response = stream.get_final_message()
    except anthropic.AuthenticationError:
        log.error("Anthropic API key rejected")
        return None
    except anthropic.APIConnectionError as exc:
        log.error("Could not reach the Anthropic API (%s)", exc)
        return None
    except anthropic.APIStatusError as exc:
        log.error("Anthropic API error %s: %s", exc.status_code, exc.message)
        return None
    usage = getattr(response, "usage", None)
    if usage is not None:
        log.info("Claude usage: %s input tokens, %s output tokens", usage.input_tokens, usage.output_tokens)
    if response.stop_reason == "refusal":
        log.warning("Claude declined the request")
        return None
    if response.stop_reason == "max_tokens":
        log.warning("Claude's response was cut off")
        return None
    return response


def rate(items: list[dict], guidance: str, model: str) -> dict[str, ItemNotes]:
    """Notes for each item, keyed 'source:id'. Empty if Claude is unavailable."""
    if not items or not available():
        if items:
            log.info("No ANTHROPIC_API_KEY set; items will not be rated")
        return {}
    client = anthropic.Anthropic()
    system = RATE_PROMPT.format(guidance=guidance)
    notes: dict[str, ItemNotes] = {}
    batches = _batches([_payload(i) for i in items])
    for n, batch in enumerate(batches, 1):
        log.info("Rating batch %d of %d (%d items)", n, len(batches), len(batch))
        prompt = f"{len(batch)} new records:\n\n" + json.dumps(batch, indent=1, sort_keys=True)
        response = _call(client, model, system, prompt, output_format=Ratings)
        if response is None or response.parsed_output is None:
            continue
        for note in response.parsed_output.items:
            notes[note.key] = note
    return notes


def brief(items: list[dict], connections: dict[str, list[dict]], labels: dict[str, str], today: date,
          model: str) -> str | None:
    """Markdown briefing across sources, or None if Claude is unavailable or nothing qualifies."""
    notable = [i for i in items if i.get("importance") in ("high", "medium")]
    if not notable or not available():
        return None
    payload = []
    for i in sorted(notable, key=lambda i: i.get("importance") != "high"):
        key = f"{i['source']}:{i['id']}"
        entry = {
            "key": key, "source": labels.get(i["source"], i["source"]), "importance": i["importance"],
            "category": i.get("category"), "headline": i.get("headline") or i.get("title"),
            "why_it_matters": i.get("why_it_matters"), "date": i.get("date"), "org": i.get("org"),
            "place": i.get("place"), "url": i.get("url"),
        }
        if key in connections:
            entry["connections"] = [
                {"source": labels.get(c["source"], c["source"]), "title": c["title"], "date": c["date"],
                 "url": c["url"], "matched_on": c["via"]}
                for c in connections[key][:6]
            ]
        payload.append({k: v for k, v in entry.items() if v})
    prompt = (f"Today is {today:%A, %B %d, %Y}. {len(items)} new records today, "
              f"{len(notable)} rated high or medium:\n\n" + json.dumps(payload, indent=1))
    response = _call(anthropic.Anthropic(), model, BRIEF_PROMPT, prompt)
    if response is None:
        return None
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()
    # The report already has a title; drop one if Claude adds its own.
    if text.startswith("# "):
        text = text.split("\n", 1)[1].strip() if "\n" in text else ""
    return text or None
