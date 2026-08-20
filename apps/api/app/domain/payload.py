"""Payload field specs and completeness — a port of ``src/domain/payload.ts``.

The schema is data, so the same declaration drives the edit form, the completeness check,
and the executor's validation. A field list duplicated across those three would drift, and
the drift would show up as an item that looks ready and fails on execute.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from app.domain.types import ActionType


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: str  # string | text | datetime | number | emails | enum
    required: bool
    min_items: Optional[int] = None
    options: Optional[tuple[str, ...]] = None
    help: Optional[str] = None


PAYLOAD_SCHEMA: dict[str, tuple[FieldSpec, ...]] = {
    "CALENDAR": (
        FieldSpec("title", "Event title", "string", True),
        FieldSpec("startsAt", "Starts at", "datetime", True),
        FieldSpec("durationMinutes", "Duration (min)", "number", True),
        FieldSpec("attendees", "Attendees", "emails", True, min_items=1),
        FieldSpec("location", "Location", "string", False),
        FieldSpec("description", "Agenda", "text", False),
        FieldSpec("timeZone", "Time zone", "string", False),
    ),
    "TASK": (
        FieldSpec("title", "Task title", "string", True),
        FieldSpec("dueAt", "Due", "datetime", False),
        FieldSpec("assignee", "Assignee", "string", False),
        FieldSpec("notes", "Notes", "text", False),
        FieldSpec("projectId", "Project / database id", "string", False),
    ),
    "EMAIL": (
        FieldSpec("to", "To", "emails", True, min_items=1),
        FieldSpec("subject", "Subject", "string", True),
        FieldSpec("body", "Body", "text", True),
        FieldSpec("cc", "Cc", "emails", False),
        FieldSpec(
            "sendMode", "Send mode", "enum", False, options=("draft", "send"),
            help="Drafts are LOW risk; sending externally is HIGH risk.",
        ),
    ),
    "REMINDER": (
        FieldSpec("message", "Message", "string", True),
        FieldSpec("remindAt", "Remind at", "datetime", True),
        FieldSpec("channel", "Channel", "enum", True, options=("self", "slack", "email")),
        FieldSpec("target", "Target (channel or address)", "string", False),
    ),
    "NONE": (),
}

# Deliberately permissive, matching the original: the only authority on whether an address
# exists is delivery to it, and a strict pattern rejects valid addresses.
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")


def is_valid_email(value: str) -> bool:
    return bool(_EMAIL.match(value.strip()))


def to_email_list(value: Any) -> list[str]:
    """Normalise the several shapes a recipient field arrives in.

    A model may emit a string, a comma-separated string, or a list. Accepting all three
    here means neither the risk classifier nor the executor has to guess.
    """
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in re.split(r"[,;]", value)]
    elif isinstance(value, (list, tuple)):
        parts = [str(p).strip() for p in value]
    else:
        return []
    return [p for p in parts if p]


def email_domain(address: str) -> Optional[str]:
    at = address.strip().lower().rfind("@")
    if at < 0 or at == len(address.strip()) - 1:
        return None
    return address.strip().lower()[at + 1 :]


def _is_present(value: Any, spec: FieldSpec) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple)):
        return len(value) >= (spec.min_items or 1)
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return value == value and value not in (float("inf"), float("-inf"))
    return True


def missing_fields(action_type: str, payload: dict[str, Any]) -> list[str]:
    """Required keys with no usable value.

    Drives ``NEEDS_CLARIFICATION`` rather than raising at extraction time: an incomplete
    item is a normal outcome of a conversation that did not say everything, and it is more
    useful on the board with a note than rejected on arrival.
    """
    specs = PAYLOAD_SCHEMA.get(action_type, ())
    return [s.key for s in specs if s.required and not _is_present(payload.get(s.key), s)]


def field_label(action_type: str, key: str) -> str:
    for spec in PAYLOAD_SCHEMA.get(action_type, ()):
        if spec.key == key:
            return spec.label
    return key
