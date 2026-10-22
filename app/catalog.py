"""Curated demo inventory plus deterministic search and date logic.

This module is intentionally deterministic: it is what keeps the MCP hot path
under Alexa+'s 500 ms round-trip budget. The LLMs add narrative and intent
understanding; they never sit between a request and the catalog lookup.
"""

from __future__ import annotations

import hashlib
import json
import re
from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

SEASON_MONTHS = {
    "spring": (3, 4, 5),
    "summer": (6, 7, 8),
    "autumn": (9, 10, 11),
    "winter": (12, 1, 2),
}


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, dict]:
    with (DATA_DIR / "catalog.json").open(encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def load_scenes() -> list[dict]:
    with (DATA_DIR / "scenes.json").open(encoding="utf-8") as handle:
        return json.load(handle)


def destinations() -> list[dict]:
    return [
        {
            "id": key,
            "name": value["name"],
            "country": value["country"],
            "tagline": value["tagline"],
            "emoji": value["emoji"],
            "palette": value["palette"],
            "image": value["image"],
            "stay_count": len(value["stays"]),
            "experience_count": len(value["experiences"]),
        }
        for key, value in load_catalog().items()
    ]


def destination(destination_id: str | None) -> dict | None:
    if not destination_id:
        return None
    value = load_catalog().get(destination_id.lower())
    if value is None:
        return None
    return {"id": destination_id.lower(), **value}


def find_destination(text: str | None) -> dict | None:
    if not text:
        return None
    haystack = text.lower()
    for key, value in load_catalog().items():
        aliases = {key, value["name"].lower(), value["country"].lower()}
        aliases.update(term.lower() for term in value.get("aliases", []))
        if any(alias in haystack for alias in aliases):
            return {"id": key, **value}
    return None


def scene(scene_id: str | None) -> dict | None:
    if not scene_id:
        return None
    for item in load_scenes():
        if item["id"] == scene_id:
            return item
    return None


def default_scene() -> dict:
    return load_scenes()[0]


def match_scene(
    show: str | None = None,
    timestamp_seconds: int | None = None,
    text: str | None = None,
) -> tuple[dict, float, str]:
    """Resolve a scene from a show/timestamp pair or free text.

    Returns (scene, confidence, explanation).
    """
    scenes = load_scenes()

    if show:
        show_key = _norm(show)
        candidates = [item for item in scenes if _norm(item["show"]) == show_key]
        if not candidates:
            candidates = [item for item in scenes if show_key in _norm(item["show"])]
        if candidates:
            chosen = min(
                candidates,
                key=lambda item: abs(item["timestamp_seconds"] - (timestamp_seconds or 0)),
            )
            delta = abs(chosen["timestamp_seconds"] - (timestamp_seconds or 0))
            confidence = max(0.55, 0.99 - delta / 3600)
            return chosen, round(confidence, 2), "matched show and nearest scene timestamp"

    if text:
        haystack = _norm(text)
        best: tuple[dict, float, str] | None = None
        for item in scenes:
            score = 0.0
            hits: list[str] = []
            for term in item["match_terms"]:
                if _norm(term) in haystack:
                    score += 2.0 if len(term) > 4 else 1.0
                    hits.append(term)
            if _norm(item["show"]) in haystack:
                score += 3.0
                hits.append("show")
            dest = load_catalog().get(item["destination_id"], {})
            if _norm(dest.get("name", "")) in haystack:
                score += 4.0
                hits.append("destination")
            if _norm(item["dish"]) in haystack:
                score += 2.0
                hits.append("dish")
            if score <= 0:
                continue
            confidence = min(0.99, 0.5 + score / 12)
            explanation = "matched " + ", ".join(dict.fromkeys(hits)) if hits else "semantic match"
            if best is None or score > best[1]:
                best = (item, confidence, explanation)
        if best:
            return best[0], round(best[1], 2), best[2]

    return default_scene(), 0.42, "no direct match; using the featured viewing moment"


def scene_payload(item: dict, confidence: float = 0.95) -> dict:
    dest = destination(item["destination_id"]) or {}
    stay = _find_venue(item["destination_id"], item.get("venue_id"))
    exp = _find_venue(item["destination_id"], item.get("experience_id"), "experiences")
    dine = _find_venue(item["destination_id"], item.get("dining_id"), "dining")
    return {
        "id": item["id"],
        "show": item["show"],
        "episode": item["episode"],
        "timestamp": item["timestamp_label"],
        "timestamp_seconds": item["timestamp_seconds"],
        "scene_title": item["scene_title"],
        "summary": item["summary"],
        "dish": item["dish"],
        "confidence": confidence,
        "destination": {
            "id": dest.get("id"),
            "name": dest.get("name"),
            "country": dest.get("country"),
            "tagline": dest.get("tagline"),
            "emoji": dest.get("emoji"),
            "palette": dest.get("palette"),
            "image": dest.get("image"),
        },
        "anchors": {
            "stay": _compact(stay),
            "experience": _compact(exp),
            "dining": _compact(dine),
        },
        "visual": item.get("visual"),
    }


def _find_venue(destination_id: str, venue_id: str | None, bucket: str = "stays") -> dict | None:
    if not venue_id:
        return None
    dest = load_catalog().get(destination_id, {})
    for item in dest.get(bucket, []):
        if item["id"] == venue_id:
            return item
    return None


def _compact(item: dict | None) -> dict | None:
    if not item:
        return None
    keys = (
        "id",
        "name",
        "kind",
        "neighborhood",
        "rating",
        "price_per_night",
        "price_per_person",
        "blurb",
        "image",
        "signature_dish",
        "cuisine",
    )
    return {key: item[key] for key in keys if key in item}


# --------------------------------------------------------------------------- dates


def resolve_dates(
    text: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    *,
    today: date | None = None,
) -> dict:
    """Turn conversational date language into a concrete weekend.

    Supports ISO dates, "next spring", "this autumn", "next weekend", "in May",
    "next month", and a sensible default.
    """
    today = today or date.today()

    if start_date:
        start = _parse_iso(start_date)
        if start is None:
            start = _next_friday(today + timedelta(days=14))
        end = _parse_iso(end_date) if end_date else start + timedelta(days=2)
        if end <= start:
            end = start + timedelta(days=2)
        return _date_result(start, end, "dates supplied by the guest")

    label = (text or "").lower()

    iso = re.search(
        r"(20\d{2}-\d{2}-\d{2})\s*(?:to|through|until|-|–)\s*(20\d{2}-\d{2}-\d{2})", label
    )
    if iso:
        return _date_result(
            _parse_iso(iso.group(1)), _parse_iso(iso.group(2)), "dates from your request"
        )

    match = re.search(r"\bnext\s+(spring|summer|autumn|fall|winter)\b", label)
    if match:
        season = "autumn" if match.group(1) == "fall" else match.group(1)
        year = today.year if today < date(today.year, 3, 1) else today.year + 1
        start = _season_friday(season, year)
        return _date_result(start, start + timedelta(days=2), f"a {season} weekend")

    match = re.search(r"\bthis\s+(spring|summer|autumn|fall|winter)\b", label)
    if match:
        season = "autumn" if match.group(1) == "fall" else match.group(1)
        start = _season_friday(season, today.year)
        if start <= today:
            start = _season_friday(season, today.year + 1)
        return _date_result(start, start + timedelta(days=2), f"a {season} weekend")

    if "next weekend" in label:
        start = _next_friday(today + timedelta(days=7))
        return _date_result(start, start + timedelta(days=2), "next weekend")

    if "this weekend" in label:
        start = _next_friday(today)
        if start <= today:
            start += timedelta(days=7)
        return _date_result(start, start + timedelta(days=2), "this weekend")

    if "next month" in label:
        year = today.year + (1 if today.month == 12 else 0)
        month = 1 if today.month == 12 else today.month + 1
        start = _nth_friday(year, month, 2)
        return _date_result(start, start + timedelta(days=2), "a weekend next month")

    if "next week" in label:
        start = _next_friday(today + timedelta(days=7))
        return _date_result(start, start + timedelta(days=2), "next week")

    month_match = re.search(r"\bin\s+([a-z]+)\b", label)
    if month_match and month_match.group(1) in MONTHS:
        month = MONTHS[month_match.group(1)]
        year = today.year if month >= today.month else today.year + 1
        start = _nth_friday(year, month, 2)
        if start <= today:
            start = _nth_friday(year + 1, month, 2)
        return _date_result(
            start, start + timedelta(days=2), f"a weekend in {month_match.group(1).title()}"
        )

    start = _next_friday(today + timedelta(days=18))
    return _date_result(start, start + timedelta(days=2), "a long weekend about three weeks out")


def _date_result(start: date, end: date, label: str) -> dict:
    if end <= start:
        end = start + timedelta(days=2)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "nights": (end - start).days,
        "label": f"{start.strftime('%b %-d')}–{end.strftime('%b %-d, %Y')}",
        "reason": label,
        "season": season_for(start),
    }


def _parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _next_friday(value: date) -> date:
    delta = (4 - value.weekday()) % 7
    return value + timedelta(days=delta)


def _nth_friday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    first_friday = _next_friday(first)
    return first_friday + timedelta(days=7 * (n - 1))


def _season_friday(season: str, year: int) -> date:
    months = SEASON_MONTHS[season]
    if season == "winter" and months[0] == 12:
        return _nth_friday(year, 12, 2)
    # A pleasant mid-season weekend.
    month = months[1] if len(months) > 2 else months[0]
    return _nth_friday(year, month, 2)


def season_for(value: date | str) -> str:
    if isinstance(value, str):
        value = date.fromisoformat(value)
    month = value.month
    for season, months in SEASON_MONTHS.items():
        if month in months:
            return season
    return "spring"


def parse_budget(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"\$?\s*([\d][\d,]*(?:\.\d+)?)\s*(k|thousand)?\b", text.lower())
    if not match:
        return None
    raw = match.group(1).replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    if match.group(2):
        value *= 1000
    if value < 200:
        return None
    return int(value * 100)


def money(cents: int | None) -> str:
    if cents is None:
        return "—"
    return f"${cents / 100:,.0f}"


# --------------------------------------------------------------------------- search


def search_stays(
    destination_id: str,
    start: date | str,
    end: date | str,
    guests: int = 2,
    budget_cents: int | None = None,
    style: str | None = None,
) -> list[dict]:
    dest = destination(destination_id)
    if not dest:
        return []
    start_d, end_d = _as_dates(start, end)
    nights = max(1, (end_d - start_d).days)
    season = season_for(start_d)
    ranked: list[tuple[float, dict]] = []
    for stay in dest["stays"]:
        if guests > stay.get("max_guests", 2):
            continue
        if nights < stay.get("min_nights", 1):
            continue
        score = float(stay.get("rating", 4.0)) * 2
        if season in stay.get("seasons", []):
            score += 2
        tags = set(stay.get("tags", []))
        if style:
            for token in re.split(r"[,\s/]+", style.lower()):
                if token and any(token in tag for tag in tags):
                    score += 3
        total = stay["price_per_night"] * nights
        if budget_cents:
            ratio = total / max(budget_cents, 1)
            if ratio <= 0.45:
                score += 3
            elif ratio <= 0.65:
                score += 1
            elif ratio > 0.9:
                score -= 4
        if stay["id"] == dest["stays"][0]["id"]:
            score += 1.5
        ranked.append((score, stay))
    ranked.sort(key=lambda item: item[0], reverse=True)
    results = []
    for _, stay in ranked[:3]:
        item = dict(stay)
        item["nights"] = nights
        item["total_cents"] = stay["price_per_night"] * nights
        item["total_label"] = money(item["total_cents"])
        item["price_label"] = f"{money(stay['price_per_night'])} / night"
        item["match_reason"] = _stay_reason(stay, season, style)
        results.append(item)
    return results


def search_experiences(
    destination_id: str,
    guests: int = 2,
    style: str | None = None,
    limit: int = 2,
) -> list[dict]:
    dest = destination(destination_id)
    if not dest:
        return []
    ranked: list[tuple[float, dict]] = []
    for exp in dest["experiences"]:
        score = 1.0
        tags = set(exp.get("tags", []))
        if style:
            for token in re.split(r"[,\s/]+", style.lower()):
                if token and any(token in tag for tag in tags):
                    score += 2
        score += exp.get("rating", 0) / 5
        if any(tag in tags for tag in ("food", "local")):
            score += 1
        ranked.append((score, exp))
    ranked.sort(key=lambda item: item[0], reverse=True)
    results = []
    for _, exp in ranked[:limit]:
        item = dict(exp)
        item["guest_total_cents"] = exp["price_per_person"] * guests
        item["price_label"] = f"{money(exp['price_per_person'])} / person"
        item["duration_label"] = f"{exp['duration_hours']} hours"
        results.append(item)
    return results


def dining_for(destination_id: str, style: str | None = None) -> dict | None:
    dest = destination(destination_id)
    if not dest or not dest.get("dining"):
        return None
    choices = dest["dining"]
    if style:
        style_l = style.lower()
        for choice in choices:
            if any(tag in style_l for tag in choice.get("tags", [])):
                return choice
    return choices[0]


def build_selection(
    destination_id: str,
    start: date | str,
    end: date | str,
    guests: int,
    budget_cents: int | None,
    style: str | None,
) -> dict:
    stays = search_stays(destination_id, start, end, guests, budget_cents, style)
    experiences = search_experiences(destination_id, guests, style, limit=2)
    dining = dining_for(destination_id, style)
    if not stays:
        # Relax the constraints rather than failing a voice request.
        dest = destination(destination_id)
        if dest:
            stays = [dict(dest["stays"][0])]
            stays[0]["nights"] = max(1, (_as_dates(start, end)[1] - _as_dates(start, end)[0]).days)
            stays[0]["total_cents"] = stays[0]["price_per_night"] * stays[0]["nights"]
            stays[0]["price_label"] = f"{money(stays[0]['price_per_night'])} / night"
            stays[0]["total_label"] = money(stays[0]["total_cents"])
            stays[0]["match_reason"] = "closest fit after relaxing availability"
    return {
        "stay": stays[0] if stays else None,
        "alternates": stays[1:],
        "experiences": experiences,
        "dining": dining,
    }


def plan_key(
    scene_id: str | None,
    destination_id: str,
    start: str,
    end: str,
    guests: int,
    budget_cents: int | None,
    style: str,
    dietary: str | None,
) -> str:
    payload = "|".join(
        str(part)
        for part in (scene_id, destination_id, start, end, guests, budget_cents, style, dietary)
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def _stay_reason(stay: dict, season: str, style: str | None) -> str:
    bits = []
    if stay["id"].startswith("stay_"):
        bits.append(f"{stay['neighborhood']} location")
    if season in stay.get("seasons", []):
        bits.append(f"available in {season}")
    if style and any(token in " ".join(stay.get("tags", [])) for token in style.lower().split()):
        bits.append("matches your style")
    if stay.get("rating", 0) >= 4.8:
        bits.append("guests rate it 4.8+")
    return ", ".join(bits[:3]) or "a strong fit for this trip"


def _as_dates(start: date | str, end: date | str) -> tuple[date, date]:
    start_d = date.fromisoformat(start) if isinstance(start, str) else start
    end_d = date.fromisoformat(end) if isinstance(end, str) else end
    return start_d, end_d


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def month_name(month: int) -> str:
    for name, number in MONTHS.items():
        if number == month and len(name) > 3:
            return name.title()
    return str(month)


def days_in_month(year: int, month: int) -> int:
    return monthrange(year, month)[1]
