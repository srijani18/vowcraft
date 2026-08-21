"""Enum types, mirroring ``prisma/schema.prisma`` exactly.

Two things must line up with the live database:

1. **The Postgres type name.** Prisma created types called ``ActionStatus``,
   ``RiskTier`` and so on. ``native_enum`` with a matching ``name`` binds to those
   rather than creating a second, differently-named type.
2. **The member values.** Postgres enums are ordered and validated; a value absent from
   the type raises at insert time. So these lists are not merely convenient constants —
   they are the contract.

``create_type=False`` on the SQLAlchemy side: the types already exist, and letting
SQLAlchemy try to create them makes every migration fail on a duplicate type.
"""

from __future__ import annotations

import enum


class TranscriptSource(str, enum.Enum):
    UPLOAD = "UPLOAD"
    MICROPHONE = "MICROPHONE"
    URL = "URL"
    TEAMS = "TEAMS"
    MEET = "MEET"
    # A shared browser tab's audio, captured via getDisplayMedia — SPEC-013. Deliberately
    # not MEET/TEAMS: those stay reserved for a possible future vendor-bot integration, and
    # reusing either here would misrepresent a Zoom (or any other) tab capture as Google's
    # or Microsoft's own. Not MICROPHONE either — that's mic-only capture (SPEC-014),
    # semantically distinct from a shared tab's mixed system audio.
    TAB_CAPTURE = "TAB_CAPTURE"


class TranscriptStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


class ActionType(str, enum.Enum):
    CALENDAR = "CALENDAR"
    TASK = "TASK"
    EMAIL = "EMAIL"
    REMINDER = "REMINDER"
    NONE = "NONE"


class Priority(str, enum.Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Confidence(str, enum.Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ActionStatus(str, enum.Enum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


class AttemptOutcome(str, enum.Enum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class RiskTier(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ApprovalGate(str, enum.Enum):
    AUTO = "AUTO"
    EXPLICIT_APPROVAL = "EXPLICIT_APPROVAL"
    EXPLICIT_APPROVAL_WITH_CONFIRMATION = "EXPLICIT_APPROVAL_WITH_CONFIRMATION"


class ApprovalState(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ActorType(str, enum.Enum):
    USER = "USER"
    SYSTEM = "SYSTEM"
    AGENT = "AGENT"


class CredentialModule(str, enum.Enum):
    TRANSCRIPTION = "TRANSCRIPTION"
    EXTRACTION = "EXTRACTION"
    TRANSLATION = "TRANSLATION"
    EMBEDDING = "EMBEDDING"
    INTEGRATION = "INTEGRATION"


class CredentialStatus(str, enum.Enum):
    UNVERIFIED = "UNVERIFIED"
    VALID = "VALID"
    INVALID = "INVALID"


class BusyKind(str, enum.Enum):
    BUSY = "BUSY"
    FOCUS = "FOCUS"
    LUNCH = "LUNCH"
    OOO = "OOO"


class BrdStatus(str, enum.Enum):
    DRAFTING = "DRAFTING"
    READY = "READY"
    FAILED = "FAILED"
