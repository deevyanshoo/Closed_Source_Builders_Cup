"""Headline signal extraction (Gemini if a key is set, rules otherwise) and the news risk index.

The news index and its overlay on the forecast are heuristics. We have no labelled
history of news and price moves, so the effect size is a configured assumption,
not a trained one.
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from typing import Iterable, Literal

from pydantic import BaseModel, Field, ValidationError

from .settings import assumptions, get_settings, materials_config

log = logging.getLogger(__name__)

Direction = Literal["up", "down", "neutral"]


class HeadlineSignal(BaseModel):
    """Structured effect of one headline on one material's price."""

    material: str | None = None
    relevant: bool = False
    direction: Direction = "neutral"
    severity: float = Field(0.0, ge=0.0, le=1.0)
    horizon_months: int = Field(2, ge=1, le=6)
    reason: str = ""
    extractor: Literal["llm", "rules"] = "rules"


# term regex -> (weight, horizon class). Up = price up (supply shock), down = price down.
UP_TERMS: dict[str, tuple[float, str]] = {
    r"\bstrik(e|es|ing)\b": (0.5, "acute"),
    r"\b(walkout|lockout)s?\b": (0.4, "acute"),
    r"\bban(s|ned)?\b": (0.5, "structural"),
    r"\bshortages?\b": (0.5, "acute"),
    r"\boutages?\b": (0.5, "acute"),
    r"\bsanctions?\b": (0.5, "structural"),
    r"\bflood(s|ing|ed)?\b": (0.4, "acute"),
    r"\bcyclone\b": (0.4, "acute"),
    r"\bclosures?\b": (0.4, "acute"),
    r"\bhalt(s|ed|ing)?\b": (0.5, "acute"),
    r"\bshut(down|downs| down|s down)\b": (0.5, "acute"),
    r"\bsuspend(s|ed)?\b": (0.4, "acute"),
    r"\bexport (restriction|curb|duty|ban)s?\b": (0.6, "structural"),
    r"\btariff (increase|hike|rise)s?\b|\braises? tariffs?\b": (0.5, "structural"),
    r"\bdisrupt(s|ed|ion|ions)?\b": (0.4, "acute"),
    r"\bblockades?\b": (0.4, "acute"),
    r"\baccident\b|\bfatal\b": (0.3, "acute"),
    r"\bcutbacks?\b|\bcurtail(s|ed|ment)?\b": (0.4, "acute"),
    r"\bdeficit\b": (0.4, "structural"),
    r"\btighten(s|ing)?\b|\bsqueez(e|es|ing)\b": (0.3, "acute"),
    r"\bstockpiling\b": (0.3, "acute"),
    r"\bsupply (crisis|crunch|constraints?|squeeze|risks?|disruptions?)\b": (0.4, "acute"),
    r"\bproduction cuts?\b": (0.4, "acute"),
    r"\bstockpiles? (hit|fall|drop)s?\b.*\blow\b": (0.3, "acute"),
}
DOWN_TERMS: dict[str, tuple[float, str]] = {
    r"\bsurplus\b": (0.5, "structural"),
    r"\boversupply\b|\bglut\b": (0.5, "structural"),
    r"\bdemand (slump|slowdown|weakens|falls|drop)s?\b|\bslump(s|ed)?\b": (0.5, "structural"),
    r"\binventor(y|ies) (build|rise|rises|climb)s?\b|\bstockpiles? (rise|build|climb)s?\b": (0.4, "acute"),
    r"\btariff (cut|reduction)s?\b|\bcuts? tariffs?\b": (0.5, "structural"),
    r"\brestart(s|ed|ing)?\b|\bresum(e|es|ed|ing)\b|\breopen(s|ed|ing)?\b": (0.4, "acute"),
    r"\bramp(s|ing)? up\b": (0.3, "structural"),
    r"\bexpansion\b|\bnew capacity\b": (0.2, "structural"),
    r"\bsupply (rises|rising|increase)s?\b": (0.3, "structural"),
    r"\boutput cut\b": (0.4, "structural"),
    r"\bweak(er|ening)? demand\b": (0.4, "acute"),
    r"\bample supply\b|\bsupply recovery\b": (0.4, "acute"),
    r"\boutput (rose|rises|up|jumps)\b": (0.3, "acute"),
}
AMPLIFIERS = [r"\bmajor\b", r"\bglobal\b", r"\bworld'?s (largest|biggest)\b", r"\blargest\b",
              r"\d+\s?% of\b", r"\brecord\b"]
LONG_HORIZON = [r"\blong[- ]term\b", r"\bdecade\b", r"\byears\b", r"\b20[3-9]\d\b"]


def detect_materials(text: str) -> list[str]:
    t = text.lower()
    found = []
    for key, cfg in materials_config()["materials"].items():
        if any(re.search(rf"\b{re.escape(k.lower())}", t) for k in cfg["keywords"]):
            found.append(key)
    return found


def _match(terms: dict[str, tuple[float, str]], text: str) -> list[tuple[str, float, str]]:
    hits = []
    for pattern, (w, kind) in terms.items():
        m = re.search(pattern, text)
        if m:
            hits.append((m.group(0), w, kind))
    return hits


def extract_rules(headline: str, material: str | None = None) -> HeadlineSignal:
    """Lexicon-based extraction. Transparent and deterministic, but shallow."""
    text = headline.lower()
    mats = detect_materials(headline)
    mat = material or (mats[0] if mats else None)
    up, down = _match(UP_TERMS, text), _match(DOWN_TERMS, text)
    # Special case: "output cut" by a consumer (e.g. steel mills) lowers ore demand, not supply.
    if any(h[0] == "output cut" for h in down):
        up = [h for h in up if not h[0].startswith("cut")]
    up_s, down_s = sum(h[1] for h in up), sum(h[1] for h in down)
    if up_s > down_s:
        direction, hits = "up", up
    elif down_s > up_s:
        direction, hits = "down", down
    else:
        direction, hits = "neutral", up + down
    amp = 0.15 * sum(bool(re.search(a, text)) for a in AMPLIFIERS)
    net = abs(up_s - down_s)
    severity = 0.0 if direction == "neutral" else round(min(1.0, net + amp), 2)
    on_topic = mat is not None and (mat in mats or material is not None)
    relevant = bool(on_topic and direction != "neutral")
    if any(re.search(p, text) for p in LONG_HORIZON):
        horizon = 6
    elif any(h[2] == "structural" for h in hits):
        horizon = 3
    else:
        horizon = 2
    if not relevant:
        why = "No price-moving terms for a tracked material were found." if on_topic else \
            "Headline does not mention a tracked material."
    else:
        terms = ", ".join(sorted({h[0] for h in hits}))
        side = "supply-side shock, prices tend to rise" if direction == "up" else \
            "easing supply or weaker demand, prices tend to fall"
        why = f"Matched terms: {terms} ({side})."
    return HeadlineSignal(material=mat, relevant=relevant, direction=direction, severity=severity,
                          horizon_months=horizon, reason=why, extractor="rules")


class _LLMItem(BaseModel):
    index: int
    material: str | None
    relevant: bool
    direction: Direction
    severity: float = Field(ge=0.0, le=1.0)
    horizon_months: int = Field(ge=1, le=6)
    reason: str


def extract_llm(headlines: list[str], material: str | None = None) -> list[HeadlineSignal]:
    """Batch extraction with Gemini structured JSON output. Raises on any failure."""
    from google import genai  # imported lazily so the app works without the package
    from google.genai import types

    key = get_settings().gemini_api_key
    if not key:
        raise RuntimeError("GEMINI_API_KEY not set")
    mats = list(materials_config()["materials"].keys())
    numbered = "\n".join(f"{i}. {h}" for i, h in enumerate(headlines))
    prompt = (
        "You are a commodity analyst for an Indian manufacturer. For each numbered headline, "
        f"decide which material it affects (one of {mats}, or null), whether it is relevant to that "
        "material's price over the next 6 months, the likely PRICE direction (up, down, neutral), "
        "severity from 0 to 1, horizon in months (1 to 6), and a one-sentence reason. "
        "Mining discoveries ('drill strike'), stock tips and opinion pieces are usually not relevant.\n"
        + (f"Assume the material is {material} if unclear.\n" if material else "")
        + numbered
    )
    client = genai.Client(api_key=key)
    resp = client.models.generate_content(
        model=assumptions()["news"]["gemini_model"],
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json",
                                           response_schema=list[_LLMItem], temperature=0.0),
    )
    raw = json.loads(resp.text)
    items = {it.index: it for it in (_LLMItem.model_validate(r) for r in raw)}
    out = []
    for i, h in enumerate(headlines):
        it = items.get(i)
        if it is None:
            out.append(extract_rules(h, material))
            continue
        mat = it.material if it.material in mats else material
        out.append(HeadlineSignal(material=mat, relevant=it.relevant and mat is not None,
                                  direction=it.direction, severity=it.severity,
                                  horizon_months=it.horizon_months, reason=it.reason, extractor="llm"))
    return out


def extract_many(headlines: list[str], material: str | None = None) -> list[HeadlineSignal]:
    """Use Gemini when configured, otherwise (or on any error) the rules extractor."""
    if get_settings().gemini_api_key and headlines:
        try:
            out: list[HeadlineSignal] = []
            for start in range(0, len(headlines), 40):
                out.extend(extract_llm(headlines[start:start + 40], material))
            return out
        except (Exception, ValidationError) as exc:  # noqa: BLE001
            log.warning("Gemini extraction failed, falling back to rules: %s", exc)
    return [extract_rules(h, material) for h in headlines]


def extract_one(headline: str, material: str | None = None) -> HeadlineSignal:
    return extract_many([headline], material)[0]


def _sign(direction: str) -> int:
    return {"up": 1, "down": -1}.get(direction, 0)


def contribution(direction: str, severity: float, age_days: float, half_life: float) -> float:
    return _sign(direction) * float(severity) * 0.5 ** (max(age_days, 0.0) / half_life)


def news_index(items: Iterable[dict], now: datetime | None = None) -> tuple[float, list[dict]]:
    """Severity-weighted, exponentially decayed sum of directions, squashed to [-1, 1].

    Returns (index, items sorted by absolute contribution, each with 'contribution').
    """
    cfg = assumptions()["news"]
    now = now or datetime.now(timezone.utc)
    scored = []
    for it in items:
        if not it.get("relevant"):
            continue
        seen = datetime.fromisoformat(str(it["seen_at"]))
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        age = (now - seen).total_seconds() / 86400
        c = contribution(it.get("direction", "neutral"), it.get("severity", 0.0), age, cfg["half_life_days"])
        scored.append({**it, "age_days": round(age, 1), "contribution": round(c, 4)})
    scored.sort(key=lambda s: abs(s["contribution"]), reverse=True)
    # One story syndicated by many outlets should count once: keep the strongest item per story.
    stories: list[set[str]] = []
    for s in scored:
        toks = _content_tokens(s.get("title", ""))
        dup = any(_jaccard(toks, t) >= STORY_JACCARD for t in stories) if toks else False
        s["duplicate_of_story"] = dup
        if not dup:
            stories.append(toks)
    total = sum(s["contribution"] for s in scored if not s["duplicate_of_story"])
    idx = math.tanh(total / cfg["index_scale"])
    return float(max(-1.0, min(1.0, idx))), scored


STORY_JACCARD = 0.3
_STOP = {"with", "from", "after", "amid", "over", "into", "that", "this", "their", "price", "prices",
         "says", "said", "will", "could", "more", "than", "as", "ahead", "while", "under", "about"}


def _content_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z][a-z']{3,}", title.lower())
    mat_words = {w for cfg in materials_config()["materials"].values() for k in cfg["keywords"] for w in k.split()}
    return {w.rstrip("s'") for w in words if w not in _STOP and w not in mat_words}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0
