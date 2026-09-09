"""Source-backed retrieval hints. They rank evidence for review, never establish a link."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

from .source_documents import article_url_key, cached_document_text
from .ingest_social import SOCIAL_PLATFORMS

NEWS = {"gdelt", *SOCIAL_PLATFORMS}
GENERIC_NAMES = {
    "unknown", "unidentified", "none", "null", "test", "test vessel", "vessel", "ship",
    "government", "military", "police", "president", "minister", "company", "school",
    "civilian", "civilians", "citizen", "citizens", "official", "officials", "general",
    "admiral", "captain", "commander",
}
# GKG can mislabel broad geographic actors and news publishers as organizations.
# These names alone do not make two reports about the same event.
BROAD_ENTITIES = {
    "united states", "united states of america", "united kingdom", "america", "britain",
    "england", "scotland", "wales", "northern ireland", "ireland", "european union",
    "iran", "iraq", "israel", "palestine", "saudi arabia", "united arab emirates", "oman",
    "qatar", "bahrain", "kuwait", "yemen", "syria", "lebanon", "jordan", "egypt",
    "china", "russia", "ukraine", "india", "pakistan", "bangladesh", "afghanistan",
    "japan", "south korea", "north korea", "taiwan", "indonesia", "malaysia", "singapore",
    "thailand", "philippines", "vietnam", "myanmar", "nepal", "sri lanka",
    "france", "germany", "italy", "spain", "portugal", "netherlands", "belgium",
    "poland", "sweden", "norway", "finland", "denmark", "switzerland", "austria",
    "greece", "turkey", "turkiye", "türkiye", "romania", "hungary", "serbia",
    "canada", "mexico", "brazil", "argentina", "colombia", "chile", "peru", "venezuela",
    "australia", "new zealand", "south africa", "nigeria", "kenya", "sudan", "ethiopia",
    "somalia", "uganda", "ghana", "tanzania", "zimbabwe", "zambia", "morocco", "algeria",
    "tunisia", "libya", "reuters", "associated press", "agence france presse",
}
TOPICS = {
    "maritime": {"ship", "ships", "vessel", "vessels", "tanker", "tankers", "maritime", "shipping", "naval", "frigate", "destroyer", "boat", "boats", "کشتی", "سفينة", "ناو"},
    "aviation": {"aircraft", "airplane", "airplanes", "plane", "planes", "aviation", "flight", "flights", "helicopter", "airstrike", "هواپیما", "طائرة"},
    "thermal": {"fire", "fires", "explosion", "explosions", "blast", "missile", "missiles", "airstrike", "burning", "انفجار", "موشک", "صاروخ"},
}


def normalize(value) -> str:
    return re.sub(r"[\W_]+", " ", unicodedata.normalize("NFKC", str(value)).casefold()).strip()


def values(value):
    return value if isinstance(value, list) else [value] if value is not None else []


@dataclass(frozen=True)
class Identity:
    phrase: str
    label: str
    kind: str
    maritime_context: bool = False


@dataclass
class Features:
    text: str
    tokens: set[str]
    entities: set[str]
    topics: set[str]
    identities: list[Identity] = field(default_factory=list)
    actors: set[str] = field(default_factory=set)


def features(record) -> Features:
    data = record.data
    entities = {normalize(value) for key in ("persons", "orgs")
                for value in values(data.get(key)) if value}
    entities = {value for value in entities if len(value) >= 3 and value not in GENERIC_NAMES | BROAD_ENTITIES}
    actors = {normalize(data[key]) for key in ("actor1", "actor2") if data.get(key)} - GENERIC_NAMES
    parts = [str(value) for key in ("actor1", "actor2", "persons", "orgs", "title", "headline", "text", "summary", "description", "themes", "keywords", "root_label")
             for value in values(data.get(key)) if value]
    if record.kind in NEWS:
        # URL slugs are explicitly source metadata, not fetched article facts. Their hints
        # must still be verified against the article by the adjudicator.
        parts.append(unquote(urlparse(str(data.get("url") or "")).path))
        parts.append(cached_document_text(str(data.get("url") or "")))
        parts.extend(f"{key} {data[key]}" for key in ("mmsi", "imo", "registration", "callsign", "hex", "vessel_name") if data.get(key))
    raw = " ".join(parts)
    # Allow the same registration written N-123AB or N123AB without merging ordinary words.
    compact = [value.replace("-", "") for value in re.findall(
        r"(?<![A-Za-z0-9])([A-Za-z0-9]{1,3}-[A-Za-z0-9]{2,6})(?![A-Za-z0-9])", raw)]
    text = " " + normalize(raw + " " + " ".join(compact)) + " "
    tokens = set(text.split())
    topics = {name for name, terms in TOPICS.items() if tokens & terms}
    identities = []
    if record.kind == "ais":
        mmsi = str(data.get("mmsi") or "")
        if re.fullmatch(r"[1-9]\d{8}", mmsi):
            identities.append(Identity(mmsi, f"MMSI {mmsi}", "identifier"))
        imo = str(data.get("imo") or "")
        if re.fullmatch(r"\d{7}", imo):
            identities.append(Identity(f"imo {imo}", f"IMO {imo}", "identifier"))
        name = normalize(data.get("name") or "")
        name = re.sub(r"^(?:m v|m t|mv|mt|ss)\s+", "", name)
        if len(name) >= 4 and name not in GENERIC_NAMES:
            identities.append(Identity(name, f"vessel name {name.upper()}", "name", len(name.split()) == 1))
    if record.kind == "adsb":
        for key in ("registration", "callsign", "hex"):
            value = normalize(data.get(key) or "").replace(" ", "")
            if len(value) >= 4 and value.isalnum() and value not in GENERIC_NAMES and (
                key == "registration" or any(char.isdigit() for char in value)
            ):
                # Unlabelled six-digit numbers are too ambiguous to treat as ICAO identifiers.
                phrase = f"icao {value}" if key == "hex" and value.isdigit() else value
                identities.append(Identity(phrase, f"{key} {value.upper()}", "identifier"))
    return Features(text, tokens, entities, topics, identities, actors)


def identity_matches(news: Features, asset: Features) -> list[Identity]:
    return [identity for identity in asset.identities
            if f" {identity.phrase} " in news.text
            and (not identity.maritime_context or "maritime" in news.topics)]


def match(left, right, a: Features, b: Features) -> tuple[str, list[str], list[str]]:
    """Priority tier, explicit entity matches, and human-readable retrieval reasons."""
    identities = identity_matches(a, b) if left.kind in NEWS else identity_matches(b, a) if right.kind in NEWS else []
    if identities:
        kind = "identifier" if any(value.kind == "identifier" for value in identities) else "name"
        labels = sorted({value.label for value in identities})
        return kind, labels, ["reported " + value for value in labels]
    if left.kind in NEWS and right.kind in NEWS:
        shared = (a.entities & b.entities) | {value for value in a.entities if f" {value} " in b.text} | {value for value in b.entities if f" {value} " in a.text}
        if shared:
            return "entity", sorted(shared)[:8], ["shared named entities: " + ", ".join(sorted(shared)[:4])]
        topics = a.topics & b.topics
        # CAMEO actors often name a whole country or broad group. Require relevant
        # reporting as well; an actor alone must not outrank a named person/organization.
        actors = (a.actors & b.actors) | {v for v in a.actors if f" {v} " in b.text} | {v for v in b.actors if f" {v} " in a.text}
        if topics and actors:
            return "topic", sorted(actors)[:8], ["relevant reporting: " + ", ".join(sorted(topics)),
                                                "shared report actors: " + ", ".join(sorted(actors)[:4])]
    else:
        report, other = (a, right.kind) if left.kind in NEWS else (b, left.kind)
        topic = {"ais": "maritime", "adsb": "aviation", "firms": "thermal"}.get(other)
        topics = {topic} if topic in report.topics and (left.kind in NEWS or right.kind in NEWS) else set()
    if topics:
        return "topic", [], ["relevant reporting: " + ", ".join(sorted(topics))]
    return "proximity", [], ["proximity only; no shared identity or relevant reporting"]


def source_key(record) -> str:
    if record.kind in NEWS:
        url = article_url_key(str(record.data.get("url") or ""))
        if url:
            return "article:" + url
    return f"{record.kind}:{record.id}"


def coverage_key(candidate) -> tuple[str, str]:
    """One report/asset pair, even when an article produces many GDELT event rows."""
    return tuple(sorted((source_key(candidate.left), source_key(candidate.right))))


def refresh_signature(candidate) -> str:
    """Material source changes, excluding routine moving-asset position updates."""
    records = []
    for record in (candidate.left, candidate.right):
        if record.kind == "ais":
            facts = {key: record.data.get(key) for key in ("mmsi", "imo", "name", "nav_status")}
        elif record.kind == "adsb":
            facts = {key: record.data.get(key) for key in ("hex", "registration", "callsign", "emergency", "military", "on_ground")}
        else:
            facts = {key: record.data.get(key) for key in ("text", "persons", "orgs", "themes", "keywords", "novelty", "frp")}
        records.append((record.kind, source_key(record), facts))
    payload = {"records": sorted(records, key=lambda value: (value[0], value[1])),
               "match_type": candidate.match_type, "matches": candidate.entity_overlap}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def select_batch(candidates, limit=12, proximity_limit=2, priority=None):
    """Semantic matches first, with source-pair diversity and a small exploration budget."""
    result, seen = [], set()
    rank = lambda candidate: (bool(priority(candidate)) if priority else False, candidate.candidate_score)
    for tier in ("identifier", "name", "entity", "topic", "proximity"):
        pool = [value for value in candidates if value.match_type == tier]
        cap = min(proximity_limit, limit) if tier == "proximity" else limit
        groups = {}
        for candidate in sorted(pool, key=rank, reverse=True):
            groups.setdefault(tuple(sorted((candidate.left.kind, candidate.right.kind))), []).append(candidate)
        added = 0
        while groups and len(result) < limit and added < cap:
            # Highest-ranked remaining modality group gets the first slot each round.
            for key in sorted(groups, key=lambda key: rank(groups[key][0]), reverse=True):
                group = groups[key]
                while group and coverage_key(group[0]) in seen:
                    group.pop(0)
                if group and len(result) < limit and added < cap:
                    candidate = group.pop(0)
                    result.append(candidate)
                    seen.add(coverage_key(candidate))
                    added += 1
                if not group:
                    del groups[key]
    return result
