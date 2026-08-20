"""BRD documents — SPEC-014 §4, §6, §8.

Owns the database and the audit trail; ``services/llm.py`` owns the model call. The split
matters on the failure path: a generation that fails must still leave a row holding what the
user *said*, because the speech is the part they cannot reproduce.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from vowcraft_db import BrdDocument, BrdRevision

from app.core.config import Settings
from app.core.exceptions import AppError, bad_request, not_found, unprocessable
from app.core.logging import logger
from app.db.repositories.users import AuditRepository
from app.domain.brd import BrdInvalid, dropped_requirement_ids, validate_brd
from app.prompts.brd import (
    BRD_REVISION_SYSTEM_PROMPT,
    BRD_REVISION_TOOL,
    BRD_SYSTEM_PROMPT,
    BRD_TOOL,
    build_initial_prompt,
    build_revision_prompt,
)
from app.services.credentials import CredentialService
from app.services.llm import ExtractionError, LlmService

MIN_SPOKEN_CHARS = 10


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def _counts(content: Optional[dict]) -> dict[str, int]:
    if not content:
        return {"requirementCount": 0, "openQuestionCount": 0}
    return {
        "requirementCount": len(content.get("functionalRequirements", []))
        + len(content.get("nonFunctionalRequirements", [])),
        "openQuestionCount": len(content.get("openQuestions", [])),
    }


def _parse_stored(raw: Any, document_id: str) -> Optional[dict]:
    """Validate stored JSON on the way out.

    Validated rather than trusted: the column is `Json`, so a document written by an older
    schema version would otherwise flow into the UI and fail somewhere less explicable. A row
    that no longer parses reports as null content, which the UI renders as "unreadable".
    """
    if raw is None:
        return None
    try:
        return validate_brd(raw)
    except BrdInvalid as exc:
        logger.warn("brd.stored_content_invalid", documentId=document_id, issues=exc.issues[:5])
        return None


class BrdService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.llm = LlmService(CredentialService(session, settings), settings)
        self.audit = AuditRepository(session)

    def _summary(self, row: BrdDocument, revision_count: int) -> dict[str, Any]:
        content = _parse_stored(row.content, row.id)
        return {
            "id": row.id,
            "title": row.title,
            "status": row.status.value if hasattr(row.status, "value") else str(row.status),
            "provider": row.provider,
            "model": row.model,
            "lastError": row.last_error,
            "revisionCount": revision_count,
            **_counts(content),
            "createdAt": _iso(row.created_at_),
            "updatedAt": _iso(row.updated_at_),
        }

    async def list_documents(self, user_id: str) -> dict[str, Any]:
        rows = (
            await self.session.scalars(
                select(BrdDocument)
                .where(BrdDocument.user_id == user_id)
                .order_by(BrdDocument.updated_at_.desc())
                .limit(100)
            )
        ).all()
        out = []
        for row in rows:
            count = await self.session.scalar(
                select(func.count()).select_from(BrdRevision).where(BrdRevision.document_id == row.id)
            )
            out.append(self._summary(row, count or 0))
        return {"documents": out}

    async def get_document(self, user_id: str, document_id: str) -> dict[str, Any]:
        row = await self.session.scalar(
            select(BrdDocument)
            .where(BrdDocument.id == document_id, BrdDocument.user_id == user_id)
            .options(selectinload(BrdDocument.revisions))
        )
        if row is None:
            raise not_found("That document does not exist.")
        revisions = sorted(row.revisions, key=lambda r: r.ordinal)
        detail = self._summary(row, len(revisions))
        detail["content"] = _parse_stored(row.content, row.id)
        detail["revisions"] = [
            {
                "id": r.id,
                "ordinal": r.ordinal,
                # This turn alone — the answer to "what did I say that caused this change?".
                "spokenText": r.spoken_text,
                "changeSummary": r.change_summary,
                "createdAt": _iso(r.created_at_),
            }
            for r in revisions
        ]
        return detail

    def _check_spoken(self, spoken_text: str) -> str:
        # The upper bound is enforced by SpokenBody's Field(max_length=50_000) before this
        # ever runs — matching spokenTextSchema's own z.string().max(50_000), a request
        # over it is rejected outright, never truncated. Silently cutting off part of what
        # someone said would be exactly the kind of undisclosed loss this feature's whole
        # design exists to prevent.
        text = (spoken_text or "").strip()
        if len(text) < MIN_SPOKEN_CHARS:
            # Refused before a provider is called: it costs quota and produces a fabricated
            # document from nothing.
            raise bad_request(
                "spoken_text_too_short",
                "Say a little more — there is not enough here to write requirements from.",
            )
        return text

    def _failure_message(self, exc: Exception) -> str:
        if isinstance(exc, ExtractionError):
            return exc.message
        if isinstance(exc, AppError):
            return exc.message
        if isinstance(exc, BrdInvalid):
            return (
                "The provider returned a document that did not match the required shape. Try "
                "again, or switch provider in Settings → API keys."
            )
        return "The document could not be generated. Try again."

    async def create(self, user_id: str, spoken_text: str, request_id: str) -> dict[str, Any]:
        text = self._check_spoken(spoken_text)

        # Created *before* the model call, holding the transcript. If generation fails the
        # user still has what they said and can retry against it; losing a minute of speech
        # to a rate limit would be the most annoying failure available here.
        document = BrdDocument(user_id=user_id, title="Untitled requirement", status="DRAFTING")
        self.session.add(document)
        await self.session.flush()

        log = logger.child(requestId=request_id, documentId=document.id)
        try:
            raw, provider = await self.llm.call_tool(
                user_id, system=BRD_SYSTEM_PROMPT, user_prompt=build_initial_prompt(text),
                tool=BRD_TOOL, log=log,
            )
            content = validate_brd(raw)
        except Exception as exc:  # noqa: BLE001 — every failure keeps the transcript
            message = self._failure_message(exc)
            log.error(
                "brd.create_failed",
                code=getattr(exc, "code", "unknown"),
                providerDetail=getattr(exc, "detail", None) or str(exc),
            )
            document.status = "FAILED"
            document.last_error = message
            await self.session.flush()
            await self.audit.record(
                event="brd.generation_failed", actor_type="AGENT", actor_id=user_id,
                request_id=request_id,
                metadata={
                    "documentId": document.id, "stage": "create",
                    "errorCode": getattr(exc, "code", "unknown"),
                    "model": getattr(exc, "model", None),
                },
            )
            raise AppError(502, "brd_generation_failed", message, {"documentId": document.id})

        document.title = content["title"]
        document.status = "READY"
        document.content = content
        document.provider = provider.id
        document.model = provider.model
        document.last_error = None
        self.session.add(
            BrdRevision(
                document_id=document.id, ordinal=1, spoken_text=text, content=content,
                change_summary="Initial document.", provider=provider.id, model=provider.model,
            )
        )
        await self.audit.record(
            event="brd.created", actor_type="AGENT", actor_id=user_id, request_id=request_id,
            metadata={
                "documentId": document.id, "provider": provider.id, "model": provider.model,
                "functionalRequirements": len(content["functionalRequirements"]),
                "openQuestions": len(content["openQuestions"]),
            },
        )
        await self.session.flush()
        await self.session.refresh(document, ["updated_at_"])
        log.info("brd.created", provider=provider.id, model=provider.model)
        return await self.get_document(user_id, document.id)

    async def refine(
        self, user_id: str, document_id: str, spoken_text: str, request_id: str
    ) -> dict[str, Any]:
        text = self._check_spoken(spoken_text)
        row = await self.session.scalar(
            select(BrdDocument).where(
                BrdDocument.id == document_id, BrdDocument.user_id == user_id
            )
        )
        if row is None:
            raise not_found("That document does not exist.")

        current = _parse_stored(row.content, row.id)
        if current is None:
            raise unprocessable(
                "no_content_to_refine",
                "This document has no generated content yet, so there is nothing to refine. "
                "Record it again.",
            )

        top = await self.session.scalar(
            select(func.max(BrdRevision.ordinal)).where(BrdRevision.document_id == document_id)
        )
        next_ordinal = (top or 0) + 1
        log = logger.child(requestId=request_id, documentId=document_id)

        try:
            raw, provider = await self.llm.call_tool(
                user_id, system=BRD_REVISION_SYSTEM_PROMPT,
                user_prompt=build_revision_prompt(current, text),
                tool=BRD_REVISION_TOOL, log=log,
            )
            if not isinstance(raw, dict) or "document" not in raw:
                raise BrdInvalid(["document: missing from the revision result"])
            revised = validate_brd(raw["document"])
            change_summary = str(raw.get("changeSummary") or "").strip()
            if len(change_summary) < 3:
                raise BrdInvalid(["changeSummary: required — a silent revision is not acceptable"])
            if len(change_summary) > 1200:
                raise BrdInvalid(["changeSummary: too long (maximum 1200 characters)"])
        except Exception as exc:  # noqa: BLE001
            message = self._failure_message(exc)
            log.error(
                "brd.refine_failed",
                code=getattr(exc, "code", "unknown"),
                providerDetail=getattr(exc, "detail", None) or str(exc),
            )
            # The document keeps its previous content and stays READY: a failed refinement
            # must not damage a document that was fine a moment ago.
            row.last_error = message
            await self.session.flush()
            await self.audit.record(
                event="brd.generation_failed", actor_type="AGENT", actor_id=user_id,
                request_id=request_id,
                metadata={
                    "documentId": document_id, "stage": "refine",
                    "errorCode": getattr(exc, "code", "unknown"),
                    "model": getattr(exc, "model", None),
                },
            )
            raise AppError(502, "brd_generation_failed", message, {"documentId": document_id})

        dropped = dropped_requirement_ids(current, revised)
        if dropped:
            # The prompt forbids this; a prompt is not an enforcement mechanism. Recorded so
            # a lost requirement is at minimum visible (SPEC-014 §6.1).
            log.warn("brd.requirements_dropped", dropped=dropped, model=provider.model)

        row.title = revised["title"]
        row.status = "READY"
        row.content = revised
        row.provider = provider.id
        row.model = provider.model
        row.last_error = None
        self.session.add(
            BrdRevision(
                document_id=document_id, ordinal=next_ordinal, spoken_text=text, content=revised,
                change_summary=change_summary, provider=provider.id, model=provider.model,
            )
        )
        await self.audit.record(
            event="brd.revised", actor_type="AGENT", actor_id=user_id, request_id=request_id,
            metadata={
                "documentId": document_id, "ordinal": next_ordinal, "provider": provider.id,
                "model": provider.model,
                "functionalRequirements": len(revised["functionalRequirements"]),
                "openQuestions": len(revised["openQuestions"]),
                "droppedRequirementIds": dropped,
            },
        )
        await self.session.flush()
        await self.session.refresh(row, ["updated_at_"])
        return await self.get_document(user_id, document_id)

    async def rename(self, user_id: str, document_id: str, title: str, request_id: str) -> dict:
        row = await self.session.scalar(
            select(BrdDocument).where(
                BrdDocument.id == document_id, BrdDocument.user_id == user_id
            )
        )
        if row is None:
            raise not_found("That document does not exist.")
        before = row.title
        row.title = title.strip()
        await self.session.flush()
        await self.audit.record(
            event="brd.renamed", actor_id=user_id, request_id=request_id,
            before={"title": before}, after={"title": row.title},
            metadata={"documentId": document_id},
        )
        return await self.get_document(user_id, document_id)

    async def delete(self, user_id: str, document_id: str, request_id: str) -> dict:
        row = await self.session.scalar(
            select(BrdDocument).where(
                BrdDocument.id == document_id, BrdDocument.user_id == user_id
            )
        )
        if row is None:
            raise not_found("That document does not exist.")
        title = row.title
        # Revisions cascade at the database level.
        await self.session.delete(row)
        await self.audit.record(
            event="brd.deleted", actor_id=user_id, request_id=request_id,
            before={"title": title}, metadata={"documentId": document_id},
        )
        return {"ok": True}
