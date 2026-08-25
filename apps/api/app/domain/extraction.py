"""The action-item extraction contract, grounding, and payload derivation — SPEC-010 §7.

Pure: no I/O, no database. The model call lives in ``services/extraction.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

ACTION_TYPES = ("CALENDAR", "TASK", "EMAIL", "REMINDER", "NONE")
PRIORITIES = ("HIGH", "MEDIUM", "LOW")
CONFIDENCES = ("HIGH", "MEDIUM", "LOW")

EXTRACTION_TOOL: dict[str, Any] = {
    "name": "record_meeting_outcomes",
    "description": (
        "Record the action items and decisions that were actually committed to in this "
        "meeting. Call this exactly once, with everything you found."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": ["string", "null"],
                "description": "Two or three sentences on what this meeting was for and what came out of it.",
            },
            "actions": {
                "type": "array",
                "maxItems": 60,
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "actionType": {"type": "string", "enum": list(ACTION_TYPES)},
                        "ownerName": {"type": ["string", "null"]},
                        "deadlineIso": {"type": ["string", "null"]},
                        "priority": {"type": "string", "enum": list(PRIORITIES)},
                        "confidence": {"type": "string", "enum": list(CONFIDENCES)},
                        "sourceTimestampMs": {"type": "integer", "minimum": 0},
                        "sourceQuote": {"type": "string"},
                        "reasoning": {"type": ["string", "null"]},
                        "supersededByIndex": {"type": ["integer", "null"]},
                        # EMAIL only — the message as it should actually be sent, drafted
                        # by the model that just read the transcript (rule 8). Before these
                        # existed the body was templated from the description plus the
                        # source quote, which produced a note *about* an email ("Send an
                        # email to Anjali… Context from the meeting: …") rather than an
                        # email anyone would send.
                        "emailSubject": {"type": ["string", "null"]},
                        "emailBody": {"type": ["string", "null"]},
                    },
                    "required": [
                        "description", "actionType", "ownerName", "deadlineIso", "priority",
                        "confidence", "sourceTimestampMs", "sourceQuote", "reasoning",
                        "supersededByIndex", "emailSubject", "emailBody",
                    ],
                },
            },
            "decisions": {
                "type": "array",
                "maxItems": 30,
                "items": {
                    "type": "object",
                    "properties": {
                        "statement": {"type": "string"},
                        "decidedBy": {"type": ["string", "null"]},
                        "sourceTimestampMs": {"type": "integer", "minimum": 0},
                        "sourceQuote": {"type": "string"},
                    },
                    "required": ["statement", "decidedBy", "sourceTimestampMs", "sourceQuote"],
                },
            },
        },
        "required": ["summary", "actions", "decisions"],
    },
}


class ExtractionInvalid(ValueError):
    def __init__(self, issues: list[str]) -> None:
        super().__init__("; ".join(issues[:8]))
        self.issues = issues


def validate_extraction(raw: Any) -> dict[str, Any]:
    """Enough validation to be safe to persist, not a full schema engine.

    Each field's bounds mirror the zod schema this replaces; the point is catching a
    provider that returned the wrong shape before it reaches the database, not re-deriving
    JSON Schema.
    """
    if not isinstance(raw, dict):
        raise ExtractionInvalid(["expected an object"])
    issues: list[str] = []
    actions: list[dict[str, Any]] = []
    for i, item in enumerate(raw.get("actions") or []):
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or "").strip()
        quote = str(item.get("sourceQuote") or "").strip()
        if len(description) < 3 or len(quote) < 3:
            issues.append(f"actions[{i}]: description/sourceQuote too short")
            continue
        action_type = item.get("actionType")
        if action_type not in ACTION_TYPES:
            action_type = "NONE"
        priority = item.get("priority") if item.get("priority") in PRIORITIES else "MEDIUM"
        confidence = item.get("confidence") if item.get("confidence") in CONFIDENCES else "MEDIUM"
        ts = item.get("sourceTimestampMs")
        actions.append(
            {
                "description": description[:500],
                "actionType": action_type,
                "ownerName": (str(item["ownerName"]).strip() or None) if item.get("ownerName") else None,
                "deadlineIso": (str(item["deadlineIso"]).strip() or None) if item.get("deadlineIso") else None,
                "priority": priority,
                "confidence": confidence,
                "sourceTimestampMs": max(0, int(ts)) if isinstance(ts, (int, float)) else 0,
                "sourceQuote": quote[:1000],
                "reasoning": (str(item["reasoning"]).strip() or None) if item.get("reasoning") else None,
                "supersededByIndex": (
                    int(item["supersededByIndex"])
                    if isinstance(item.get("supersededByIndex"), (int, float))
                    else None
                ),
                "emailSubject": (
                    (str(item["emailSubject"]).strip()[:200] or None)
                    if item.get("emailSubject")
                    else None
                ),
                "emailBody": (
                    (str(item["emailBody"]).strip()[:5000] or None)
                    if item.get("emailBody")
                    else None
                ),
            }
        )

    decisions: list[dict[str, Any]] = []
    for item in raw.get("decisions") or []:
        if not isinstance(item, dict):
            continue
        statement = str(item.get("statement") or "").strip()
        if len(statement) < 3:
            continue
        decisions.append(
            {
                "statement": statement[:500],
                "decidedBy": (str(item["decidedBy"]).strip() or None) if item.get("decidedBy") else None,
                "sourceTimestampMs": max(0, int(item.get("sourceTimestampMs") or 0)),
                "sourceQuote": str(item.get("sourceQuote") or "").strip()[:1000],
            }
        )

    return {
        "summary": (str(raw["summary"]).strip() or None) if raw.get("summary") else None,
        "actions": actions,
        "decisions": decisions,
    }


# ─────────────────────────────────────────────────────────────── grounding ──

_SMART_QUOTES = str.maketrans({
    "‘": "'", "’": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "‟": '"', "″": '"',
    "–": "-", "—": "-",
})


def _normalise(text: str) -> str:
    text = text.lower().translate(_SMART_QUOTES)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[.,;:!?]+$", "", text)
    return text.strip()


@dataclass
class SegmentBounds:
    start_ms: int
    end_ms: int


def ground_actions(
    actions: list[dict[str, Any]], segments: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int, int]:
    """Verify every citation against the transcript before anything is persisted.

    Substring match after normalisation, deliberately not fuzzy: the rule is "copied
    exactly", and a similarity threshold would quietly re-admit a paraphrase — which is
    the one thing a citation exists to rule out.
    """
    haystack = _normalise(" ".join(s["text"] for s in segments))
    bounds = [SegmentBounds(s["startMs"], s["endMs"]) for s in segments]
    last_end = bounds[-1].end_ms if bounds else 0

    verified = 0
    unverified = 0
    grounded: list[dict[str, Any]] = []

    for action in actions:
        needle = _normalise(action["sourceQuote"])
        found = len(needle) >= 8 and needle in haystack
        if found:
            verified += 1
        else:
            unverified += 1

        claimed = min(max(0, action["sourceTimestampMs"]), max(0, last_end))
        containing = next((b for b in bounds if b.start_ms <= claimed <= b.end_ms), None)
        if containing is None and bounds:
            containing = min(bounds, key=lambda b: abs(b.start_ms - claimed))

        grounded.append(
            {
                **action,
                "sourceTimestampMs": containing.start_ms if containing else claimed,
                "quoteVerified": found,
                # An unverifiable citation is exactly the case the LOW lane exists for.
                "confidence": action["confidence"] if found else "LOW",
            }
        )

    return grounded, verified, unverified


def parse_deadline(iso: Optional[str], recorded_at: datetime) -> Optional[datetime]:
    if not iso:
        return None
    try:
        parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=recorded_at.tzinfo)

    # A deadline more than a day before the meeting is a resolution error, not a fact.
    if parsed < recorded_at - timedelta(days=1):
        return None
    # Ten years out is a hallucinated year, not a plan.
    if parsed > recorded_at + timedelta(days=10 * 365):
        return None
    # `deadline` is `timestamp without time zone` — asyncpg refuses an aware value outright,
    # and merely stripping tzinfo (rather than converting first) would silently keep the
    # wall-clock time of whatever offset the model or `recorded_at` used instead of UTC.
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


# ─────────────────────────────────────────────────────────── payload derivation ──


@dataclass
class DerivationContext:
    team_members: list[dict[str, str]]
    owner_email: Optional[str]
    self_email: str


def _resolve_email(name: Optional[str], context: DerivationContext) -> Optional[str]:
    if not name:
        return None
    match = next(
        (m for m in context.team_members if m["name"].lower() == name.strip().lower()), None
    )
    return match["email"] if match else None


def _mentioned_others(action: dict[str, Any], context: DerivationContext) -> list[str]:
    haystack = f"{action['description']} {action['sourceQuote']}".lower()
    owner = (action.get("ownerName") or "").strip().lower()
    out = []
    for member in context.team_members:
        first = member["name"].split(" ")[0].lower()
        if len(first) < 3:
            continue
        if member["name"].lower() == owner:
            continue
        # Word-boundary match, so "Jo" does not match "job".
        if re.search(rf"\b{re.escape(first)}\b", haystack):
            out.append(member["email"])
    return out


def derive_owner_email(action: dict[str, Any], context: DerivationContext) -> Optional[str]:
    return _resolve_email(action.get("ownerName"), context)


def derive_payload(
    action: dict[str, Any], deadline: Optional[datetime], context: DerivationContext
) -> dict[str, Any]:
    others = _mentioned_others(action, context)
    action_type = action["actionType"]

    if action_type == "CALENDAR":
        title = re.sub(
            r"^(schedule|set up|book|arrange)\s+(the\s+|a\s+)?", "", action["description"],
            flags=re.IGNORECASE,
        ).strip() or action["description"]
        payload: dict[str, Any] = {"title": title, "description": action["sourceQuote"]}
        if deadline:
            payload["startsAt"] = deadline.isoformat()
            payload["durationMinutes"] = 30
        if others:
            payload["attendees"] = others
        return payload

    if action_type == "TASK":
        payload = {
            "title": action["description"],
            "notes": f"From the meeting: “{action['sourceQuote']}”",
        }
        if deadline:
            payload["dueAt"] = deadline.isoformat()
        if context.owner_email:
            payload["assignee"] = context.owner_email
        return payload

    if action_type == "EMAIL":
        # The model drafts the message itself (rule 8): it has the transcript and can tell
        # what was actually being asked. The template below survives only as a fallback for
        # a provider that returned nothing — it restates the action and quotes the meeting,
        # which reads as a note *about* an email rather than one anybody would send.
        drafted_subject = (action.get("emailSubject") or "").strip()
        drafted_body = (action.get("emailBody") or "").strip()
        return {
            **({"to": others} if others else {}),
            "subject": (drafted_subject or action["description"])[:120],
            "body": drafted_body
            or f"{action['description']}\n\nContext from the meeting:\n“{action['sourceQuote']}”\n",
            # Always a draft. An extracted email is the last thing that should send
            # itself, and draft is the LOW-risk end of the scale (SPEC-003 §3).
            "sendMode": "draft",
        }

    if action_type == "REMINDER":
        payload = {"message": action["description"], "channel": "self"}
        if deadline:
            payload["remindAt"] = deadline.isoformat()
        return payload

    return {}
