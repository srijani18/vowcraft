"""Resolving an action's attachments into bytes — SPEC-002 §5.1.

The one security-relevant thing here is the ownership re-check. An `attachments` entry is a
document *id* carried in a payload, and a payload is editable, so the id reaching this code
is untrusted input. Every lookup is filtered by `user_id` in the query itself, which means a
forged or guessed id reads as absent rather than as somebody else's document. Getting that
wrong would attach another account's requirements document to an outbound email — the worst
outcome this system can produce, and one no downstream guardrail would catch, because a
guardrail sees a well-formed payload either way.

Adapters deliberately do not do this themselves: they talk to providers and nothing else, so
they never touch the database. The executor resolves attachments and hands the adapter bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from vowcraft_db import BrdDocument

from app.core.exceptions import unprocessable
from app.core.logging import logger
from app.domain.brd import brd_filename, brd_to_markdown

#: A generous ceiling rather than a tuned one. Gmail's own limit is 25 MB for the whole
#: message, and a requirements document rendered as Markdown is measured in kilobytes; this
#: exists so a pathological document cannot build a request that a provider will reject
#: after the executor has already claimed the item as EXECUTING.
_MAX_TOTAL_BYTES = 8 * 1024 * 1024
_MAX_ATTACHMENTS = 10

#: The kinds an attachment entry may name. A single-entry registry today, but the field is
#: shaped for more (a transcript export is the obvious next one), and an unknown kind must
#: fail loudly rather than being silently skipped — a silently dropped attachment means an
#: email that went out without the thing it was about.
_KNOWN_KINDS = ("brd",)


@dataclass(frozen=True)
class ResolvedAttachment:
    filename: str
    mime_type: str
    content: bytes


def _entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("attachments")
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


async def resolve_attachments(
    session, user_id: str, payload: dict[str, Any]
) -> list[ResolvedAttachment]:
    """Turns each `{"kind": ..., "documentId": ...}` entry into real bytes.

    Raises rather than skipping on anything it cannot resolve: an attachment the user
    selected and the recipient never received is a silent failure, and this system's whole
    claim is that what it reports is what happened.
    """
    entries = _entries(payload)
    if not entries:
        return []
    if len(entries) > _MAX_ATTACHMENTS:
        raise unprocessable(
            "too_many_attachments",
            f"An action can carry at most {_MAX_ATTACHMENTS} attachments.",
        )

    resolved: list[ResolvedAttachment] = []
    total = 0
    for entry in entries:
        kind = str(entry.get("kind") or "").strip().lower()
        if kind not in _KNOWN_KINDS:
            raise unprocessable(
                "unknown_attachment_kind",
                f"“{kind or 'unnamed'}” is not an attachment this can send.",
            )
        document_id = str(entry.get("documentId") or "").strip()
        if not document_id:
            raise unprocessable(
                "invalid_attachment", "An attachment is missing its document id."
            )

        attachment = await _resolve_brd(session, user_id, document_id)
        total += len(attachment.content)
        if total > _MAX_TOTAL_BYTES:
            raise unprocessable(
                "attachments_too_large",
                "Those attachments exceed the size a single message can carry.",
            )
        resolved.append(attachment)

    return resolved


async def _resolve_brd(session, user_id: str, document_id: str) -> ResolvedAttachment:
    # Ownership is in the WHERE clause, not a check after the fetch: the two are equivalent
    # only as long as nobody later reorders the code, and this is the one place where that
    # mistake would leak another account's document.
    row: Optional[BrdDocument] = await session.scalar(
        select(BrdDocument).where(
            BrdDocument.id == document_id, BrdDocument.user_id == user_id
        )
    )
    if row is None:
        # Deliberately does not distinguish "does not exist" from "not yours" — the
        # difference is only useful to someone probing for ids.
        raise unprocessable(
            "attachment_not_found",
            "That requirements document is no longer available to attach.",
        )
    if not isinstance(row.content, dict) or not row.content:
        raise unprocessable(
            "attachment_empty",
            f"“{row.title}” has no content yet, so there is nothing to attach.",
        )

    markdown = brd_to_markdown(row.content)
    logger.info(
        "attachment.resolved", kind="brd", documentId=document_id, bytes=len(markdown)
    )
    return ResolvedAttachment(
        filename=brd_filename(row.title, "md"),
        mime_type="text/markdown",
        content=markdown.encode("utf-8"),
    )
