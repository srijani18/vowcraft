"""Turns a transcribed recording into action items — SPEC-010 §7, §8.

Every citation is verified against the transcript before anything is persisted
(``domain/extraction.ground_actions``): a paraphrased or fabricated quote is downgraded to
LOW confidence rather than trusted, because a citation that cannot be checked is worse than
no citation at all — it looks like evidence.

Only untouched proposals are ever replaced on re-extraction. Anything a human has already
approved, rejected or deferred survives, because discarding a reviewer's decision because a
model was re-run would be indefensible (SPEC-010 §8).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from voice2brd_db import ActionItem, Decision, Segment, Speaker, TeamMember, Transcript, User, UserSettings

from app.core.config import get_settings
from app.core.logging import logger
from app.db.repositories.users import AuditRepository
from app.db.session import session_factory
from app.domain.extraction import (
    ExtractionInvalid,
    derive_owner_email,
    derive_payload,
    ground_actions,
    parse_deadline,
    validate_extraction,
    DerivationContext,
    EXTRACTION_TOOL,
)
from app.prompts.extraction import SYSTEM_PROMPT, build_transcript_text, build_user_prompt
from app.services.credentials import CredentialService
from app.services.llm import ExtractionError, LlmService, sample_extract


async def _load_segments(session: AsyncSession, transcript_id: str) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(
            select(Segment)
            .where(Segment.transcript_id == transcript_id)
            .order_by(Segment.start_ms)
        )
    ).all()
    out = []
    for row in rows:
        speaker = None
        if row.speaker_id:
            speaker_row = await session.get(Speaker, row.speaker_id)
            speaker = speaker_row.label if speaker_row else None
        out.append({"startMs": row.start_ms, "endMs": row.end_ms, "text": row.text, "speakerLabel": speaker})
    return out


async def extract_into(transcript_id: str, user_id: str, request_id: str) -> dict[str, Any]:
    """Runs extraction on an already-transcribed transcript.

    Separated from the upload pipeline so ``POST /:id/extract`` can retry this stage alone
    (SPEC-010 §8), which is the whole reason a failed extraction is recoverable without
    re-transcribing.
    """
    settings = get_settings()
    log = logger.child(requestId=request_id, transcriptId=transcript_id)

    async with session_factory()() as session:
        transcript = await session.scalar(
            select(Transcript).where(Transcript.id == transcript_id, Transcript.user_id == user_id)
        )
        if transcript is None:
            log.error("extraction.no_transcript")
            return {"ok": False, "created": 0, "skipped": 0, "unverifiedQuotes": 0, "error": "not found"}

        settings_row = await session.scalar(select(UserSettings).where(UserSettings.user_id == user_id))
        team_rows = (await session.scalars(select(TeamMember).where(TeamMember.user_id == user_id))).all()
        user = await session.get(User, user_id)
        segments = await _load_segments(session, transcript_id)

        if not segments:
            # A port of src/server/ingest/service.ts's `not_transcribed` guard — extraction
            # on an empty transcript would send the LLM nothing to read and either error
            # opaquely or fabricate content, neither of which is the honest answer here.
            log.error("extraction.not_transcribed")
            return {
                "ok": False, "created": 0, "skipped": 0, "unverifiedQuotes": 0,
                "error": "That transcript has no text yet.", "errorCode": "not_transcribed",
            }

        time_zone = settings_row.time_zone if settings_row else "Asia/Kolkata"
        recorded_at = transcript.recorded_at or transcript.created_at_
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)

        llm = LlmService(CredentialService(session, settings), settings)
        provider_name = "unknown"
        attempted_model: Optional[str] = None

        try:
            # `allow_sample=True`: with nothing configured, the bundled fixture stands in
            # so a fresh instance still produces a complete result — transcript, action
            # items, decisions, guardrails, the board — rather than ending at zero
            # actions, which reads as broken rather than unconfigured.
            provider, api_key, source = await llm.resolve(user_id, allow_sample=True)
            provider_name = provider.id
            attempted_model = provider.model
            log.info("extraction.extracting", provider=provider.id, model=provider.model, source=source)

            user_prompt = build_user_prompt(
                title=transcript.title, recorded_at=recorded_at, time_zone=time_zone,
                team_members=[{"name": t.name, "email": t.email} for t in team_rows],
                language=transcript.language,
                transcript=build_transcript_text(segments),
            )
            if provider.id == "sample":
                raw = await sample_extract(user_prompt)
            else:
                raw, _ = await llm.call_tool(
                    user_id, system=SYSTEM_PROMPT, user_prompt=user_prompt,
                    tool=EXTRACTION_TOOL, log=log,
                )
            extracted = validate_extraction(raw)
        except (ExtractionError, ExtractionInvalid) as exc:
            message = exc.message if isinstance(exc, ExtractionError) else str(exc)
            code = exc.code if isinstance(exc, ExtractionError) else "invalid_extraction"
            status = getattr(exc, "status", None)
            retryable = getattr(exc, "retryable", False)
            log.error(
                "extraction.extract_failed", code=code, status=status, retryable=retryable,
                providerDetail=getattr(exc, "detail", None) or str(exc),
            )
            transcript.extract_error = message
            transcript.extract_error_code = code
            transcript.extract_model = attempted_model
            await session.commit()
            try:
                # Audited outside the row-update transaction, deliberately: there is no
                # state change to commit alongside it, and an audit write failing here
                # must not turn a handled extraction failure into an unhandled one.
                await AuditRepository(session).record(
                    event="transcript.extraction_failed", actor_type="AGENT", actor_id=user_id,
                    request_id=request_id,
                    metadata={
                        "transcriptId": transcript_id, "provider": provider_name, "model": attempted_model,
                        "extractionStatus": "FAILED", "errorCode": code,
                        "httpStatus": status, "retryable": retryable,
                    },
                )
                await session.commit()
            except Exception:  # noqa: BLE001 — see the comment above
                log.error("extraction.extract_audit_failed")
            return {
                "ok": False, "created": 0, "skipped": 0, "unverifiedQuotes": 0,
                "error": message, "errorCode": code, "provider": provider_name, "model": attempted_model,
            }

        # ── verify every citation before anything is persisted
        grounded, verified, unverified = ground_actions(extracted["actions"], segments)
        if unverified:
            log.warn("extraction.unverified_quotes", verified=verified, unverified=unverified)

        context = DerivationContext(
            team_members=[{"name": t.name, "email": t.email} for t in team_rows],
            owner_email=None, self_email=user.email if user else "",
        )

        # Only untouched proposals are replaced — anything a human already decided on
        # survives (SPEC-010 §8).
        await session.execute(
            delete(ActionItem).where(ActionItem.transcript_id == transcript_id, ActionItem.status == "PROPOSED")
        )
        await session.execute(delete(Decision).where(Decision.transcript_id == transcript_id))
        await session.flush()

        for decision in extracted["decisions"]:
            session.add(
                Decision(
                    transcript_id=transcript_id, statement=decision["statement"],
                    decided_by=decision["decidedBy"], source_timestamp_ms=decision["sourceTimestampMs"],
                    source_quote=decision["sourceQuote"],
                )
            )

        # Two passes: create everything, then wire up supersession, since the second needs
        # ids that only exist after the first.
        ids: list[str] = []
        for action in grounded:
            deadline = parse_deadline(action.get("deadlineIso"), recorded_at)
            owner_email = derive_owner_email(action, context)
            payload = derive_payload(action, deadline, DerivationContext(
                team_members=context.team_members, owner_email=owner_email, self_email=context.self_email,
            ))
            reasoning = action.get("reasoning") or ""
            if not action["quoteVerified"]:
                note = (
                    "The quoted sentence could not be matched against the transcript, so "
                    "confidence was lowered — check the source before approving."
                )
                reasoning = f"{reasoning} {note}".strip() if reasoning else note

            row = ActionItem(
                transcript_id=transcript_id, description=action["description"],
                action_type=action["actionType"], owner_name=action.get("ownerName"),
                owner_email=owner_email, deadline=deadline, priority=action["priority"],
                confidence=action["confidence"], source_timestamp_ms=action["sourceTimestampMs"],
                source_quote=action["sourceQuote"], reasoning=reasoning or None, payload=payload,
            )
            session.add(row)
            await session.flush()
            ids.append(row.id)

        for index, action in enumerate(grounded):
            target = action.get("supersededByIndex")
            # Guard the index: a model pointing at itself or past the end would otherwise
            # create a self-superseding item that can never execute.
            if target is None or target == index or target < 0 or target >= len(ids):
                continue
            row = await session.get(ActionItem, ids[index])
            row.superseded_by_id = ids[target]

        if extracted.get("summary"):
            transcript.summary = extracted["summary"]

        await AuditRepository(session).record(
            event="transcript.extracted", actor_type="AGENT", actor_id=user_id, request_id=request_id,
            metadata={
                "transcriptId": transcript_id, "provider": provider_name, "model": attempted_model,
                "extractionStatus": "SUCCEEDED",
                "actions": len(ids), "decisions": len(extracted["decisions"]),
                "quotesVerified": verified, "quotesUnverified": unverified,
            },
        )

        transcript.extract_provider = provider_name
        transcript.extract_model = attempted_model
        transcript.extracted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        transcript.extract_error = None
        transcript.extract_error_code = None
        await session.commit()

        return {
            "ok": True, "created": len(ids), "skipped": 0, "unverifiedQuotes": unverified,
            "provider": provider_name, "model": attempted_model,
        }
