"""Execution — SPEC-002 §5. The one path by which anything reaches a third party.

Twelve steps, in this order, and the order is the design:

1.  load the item
2.  merge and normalise the payload, before anything reads it
3.  **idempotency check — before the status gate**, deliberately (see below)
4.  status gate
5.  dependency gate
6.  validate the typed payload
7/8. guardrails and approval gate, recomputed server-side
9.  transition to EXECUTING, conditionally on the status we read
10. dispatch with bounded retry
11. record the result
12. audit — in the *same transaction* as 11

**Why idempotency comes before the status gate.** A replayed request arrives for an item that
is already EXECUTED. If the status gate ran first it would reject with "already executed" and
the caller would never learn the original external id — so a client retrying after a dropped
response has no way to reconcile. Checking the key first returns the prior result, which is
what a replay is *for*. This ordering was wrong in the first version of the TypeScript and
the fix is preserved here.

**Why 11 and 12 share a transaction.** An EXECUTED item with no audit row is a compliance
defect: the system claims something happened with no record of who authorised it. They commit
together or not at all.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from vowcraft_db import ActionItem, ExecutionAttempt, IntegrationAccount, Transcript, now_ms

from app.core.config import Settings
from app.core.crypto import default_idempotency_key, decrypt
from app.core.exceptions import AppError, conflict, not_found, unprocessable
from app.core.logging import logger
from app.core.retry import AttemptRecord, with_retry
from app.db.repositories.users import AuditRepository
from app.domain.action_item import compute_readiness
from app.domain.payload import field_label
from app.domain.risk import classify_risk, gate_satisfied
from app.integrations.adapters import adapter_for
from app.integrations.registry import provider_mode, resolve_provider
from app.integrations.types import ExecutionContext, ProviderError
from app.services.action_items import ActionItemService, evaluate, to_core
from app.services.credentials import CredentialService

#: Bump if the shape of `execution_result` (stored on the row and returned from
#: `execute()`) ever changes incompatibly — a port of EXECUTION_RESULT_VERSION from the
#: original TypeScript executor.
EXECUTION_RESULT_VERSION = 1


def _attempt_records(attempts: list[AttemptRecord]) -> list[dict[str, Any]]:
    return [
        {
            "n": a.n,
            "outcome": a.outcome,
            "durationMs": a.duration_ms,
            "errorCode": a.error_code,
            "errorMessage": a.error_message,
        }
        for a in attempts
    ]


class ExecutorService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.audit = AuditRepository(session)

    async def _access_token(self, user_id: str, provider_id: str) -> Optional[str]:
        account = await self.session.scalar(
            select(IntegrationAccount).where(
                IntegrationAccount.user_id == user_id,
                IntegrationAccount.provider == provider_id,
            )
        )
        if account is None or not account.access_token_enc or account.needs_reauth:
            return None
        try:
            from app.core.crypto import decrypt as _decrypt

            return _decrypt(
                account.access_token_enc,
                self.settings.APP_ENCRYPTION_KEY,
                f"oauth:{user_id}:{provider_id}",
            )
        except Exception:  # noqa: BLE001 — an undecryptable token is a missing token
            logger.error("execution.token_undecryptable", provider=provider_id, userId=user_id)
            return None

    async def execute(
        self,
        user_id: str,
        user_email: str,
        item_id: str,
        *,
        payload_override: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
        confirmed: bool = False,
        dry_run: bool = False,
        request_id: str = "",
    ) -> dict[str, Any]:
        log = logger.child(requestId=request_id, actionItemId=item_id)

        # ── 1. load
        row = await self.session.scalar(
            select(ActionItem)
            .join(Transcript, Transcript.id == ActionItem.transcript_id)
            .where(ActionItem.id == item_id, Transcript.user_id == user_id)
        )
        if row is None:
            raise not_found("That action item does not exist.")

        status = row.status.value if hasattr(row.status, "value") else str(row.status)

        # ── 2. payload merge, before anything reads it
        payload = {**(row.payload or {}), **(payload_override or {})}
        key = idempotency_key or default_idempotency_key(item_id, payload)

        # `inputs`/`core` are read-only and needed by both the replay path below (to report
        # a real risk assessment) and the main path, so they are loaded once, ahead of the
        # idempotency check.
        service = ActionItemService(self.session, self.settings)
        inputs = await service.load_inputs(user_id, user_email)
        core = to_core(row)
        core.payload = payload

        # ── 3. idempotency, BEFORE the status gate — see the module docstring
        prior = await self.session.scalar(
            select(ExecutionAttempt).where(
                ExecutionAttempt.action_item_id == item_id,
                ExecutionAttempt.idempotency_key == key,
                ExecutionAttempt.outcome == "SUCCESS",
            )
        )
        if prior is not None:
            log.info("execution.replayed", idempotencyKey=key, externalId=prior.external_id)
            # Report the real assessment rather than a placeholder: the caller renders this,
            # and a replayed HIGH-risk action is still a HIGH-risk action. classify_risk is
            # pure, so recomputing it costs nothing.
            replay_risk = classify_risk(core, payload, inputs.settings, user_email)
            stored = row.execution_result if isinstance(row.execution_result, dict) else None
            return {
                "ok": True,
                "replayed": True,
                "dryRun": False,
                "status": status,
                "idempotencyKey": key,
                "riskTier": replay_risk.tier,
                "riskFactors": replay_risk.factors,
                # The gate was already satisfied when this executed; a replay does not
                # re-open that question.
                "approvalGate": "AUTO",
                "warnings": [],
                "result": (
                    {**stored, "replayed": True}
                    if stored is not None
                    else {
                        "provider": prior.provider,
                        "mode": prior.mode,
                        "externalId": prior.external_id,
                        "externalUrl": prior.external_url,
                        "simulated": prior.mode == "mock",
                        "summary": "Previously executed with these details.",
                        "at": prior.finished_at.replace(tzinfo=timezone.utc).isoformat()
                        if prior.finished_at
                        else None,
                    }
                ),
            }

        rules, readiness, risk, gate, provider = evaluate(
            core, inputs, row.transcript_id, datetime.now(timezone.utc)
        )

        if provider is None:
            raise unprocessable(
                "no_provider",
                f"No integration is configured to handle a {core.action_type} action.",
            )
        adapter = adapter_for(provider.id)
        if adapter is None:
            raise unprocessable("no_provider", f"No adapter for provider {provider.id}.")

        mode = provider_mode(provider.id, self.settings)

        # ── 4. status gate
        if not dry_run and status not in ("APPROVED", "FAILED"):
            if status in ("EXECUTED", "EXECUTING"):
                raise conflict(
                    "already_executed" if status == "EXECUTED" else "in_progress",
                    "This action has already been executed."
                    if status == "EXECUTED"
                    else "This action is already being executed.",
                )
            raise conflict("not_approved", "Approve this action before executing it.")

        # ── 5. dependency gate — SPEC-002 §8
        if core.depends_on_id:
            blocker = await self.session.scalar(
                select(ActionItem).where(ActionItem.id == core.depends_on_id)
            )
            blocker_status = (
                blocker.status.value if blocker and hasattr(blocker.status, "value") else None
            )
            if blocker is not None and blocker_status != "EXECUTED":
                raise conflict(
                    "blocked_by_dependency",
                    f"This depends on “{blocker.description}”, which has not executed yet.",
                )

        # ── 6. validate the typed payload; the failure names the fields.
        # An adapter may also raise `ProviderError` directly for a hard business-rule
        # rejection that isn't "a field is missing" — SendGrid refusing a draft payload,
        # for instance (a port of the TypeScript schema's own `.refine()`). Surfacing its
        # message as-is matters: a generic "invalid payload" would bury the one thing the
        # reviewer needs to read, which is *why*, and what to do about it.
        try:
            missing = adapter.validate(payload)
        except ProviderError as exc:
            raise unprocessable("invalid_payload", exc.message) from exc
        if missing:
            labels = [field_label(core.action_type, m) for m in dict.fromkeys(missing)]
            raise unprocessable(
                "invalid_payload",
                f"{provider.display_name} needs: {', '.join(labels)}.",
                {"missingFields": list(dict.fromkeys(missing))},
            )

        # `preview()` is the same field-by-field description the confirmation modal shows
        # before anything is sent — SPEC-001 §11. Nested under `preview` as `{consequence,
        # fields, provider}`, distinct from the top-level risk/gate/warnings fields below.
        adapter_preview = adapter.preview(payload, time_zone=inputs.settings.time_zone, user_email=user_email)
        preview = {
            **adapter_preview,
            "providerName": provider.display_name,
            "mode": mode,
            "readiness": readiness.readiness,
            "violations": [
                {"ruleId": v.rule_id, "severity": v.severity, "message": v.message}
                for v in rules.violations
            ],
            "payload": payload,
        }

        warnings = [{"ruleId": v.rule_id, "message": v.message} for v in rules.warnings]

        # ── dry run exits here: never changes status, never consumes a key
        if dry_run:
            return {
                "ok": rules.passes,
                "dryRun": True,
                "replayed": False,
                "status": status,
                "idempotencyKey": key,
                "riskTier": risk.tier,
                "riskFactors": risk.factors,
                "approvalGate": gate.gate,
                "warnings": warnings,
                "preview": preview,
                "result": None,
            }

        # ── 7/8. guardrails and the approval gate, recomputed server-side (SPEC-003 §6).
        # Recomputed rather than trusted from the request: the UI enabled a button at some
        # earlier moment, and the payload may have changed since.
        if not rules.passes:
            await self.audit.record(
                event="action_item.guardrail_blocked", actor_id=user_id, action_item_id=item_id,
                request_id=request_id,
                metadata={
                    "rules": [f"{v.rule_id}:BLOCK" for v in rules.blocking],
                    "riskTier": risk.tier, "provider": provider.id,
                },
            )
            await self.session.commit()
            raise unprocessable(
                "guardrail_blocked",
                rules.blocking[0].message if rules.blocking
                else "A guardrail prevents this action from running.",
                {
                    "violations": [
                        {"ruleId": v.rule_id, "severity": v.severity, "message": v.message}
                        for v in rules.blocking
                    ]
                },
            )

        if not gate_satisfied(gate.gate, approved=status in ("APPROVED", "FAILED"), confirmed=confirmed):
            raise conflict(
                "approval_required",
                "This action needs explicit confirmation before it can execute.",
                {"gate": gate.gate, "riskTier": risk.tier},
            )

        await self.audit.record(
            event="action_item.execution_requested", actor_id=user_id, action_item_id=item_id,
            request_id=request_id,
            metadata={"provider": provider.id, "mode": mode, "riskTier": risk.tier,
                      "idempotencyKey": key},
        )

        # ── 9. transition to EXECUTING, conditional on the status we read.
        # A conditional UPDATE rather than a read-then-write: two concurrent requests would
        # both pass an application-level check, and only one may proceed.
        claimed = await self.session.execute(
            update(ActionItem)
            .where(ActionItem.id == item_id, ActionItem.status == status)
            .values(status="EXECUTING")
        )
        if claimed.rowcount == 0:
            raise conflict(
                "in_progress", "Another request is already executing this action."
            )
        await self.session.commit()

        # ── 10. dispatch with bounded retry
        ctx = ExecutionContext(
            mode=mode, user_email=user_email,
            time_zone=inputs.settings.time_zone, request_id=request_id, idempotency_key=key,
            access_token=await self._access_token(user_id, provider.id) if mode == "live" else None,
            api_key=(
                (await CredentialService(self.session, self.settings).resolve(
                    user_id, provider.credential_service
                )).api_key
                if mode == "live" and provider.credential_service
                else None
            ),
        )

        started = now_ms()
        attempt_number = 0

        async def dispatch(attempt: int):
            nonlocal attempt_number
            attempt_number = attempt
            # Every attempt is recorded before it runs, so a process that dies mid-dispatch
            # leaves a RUNNING row rather than no evidence at all.
            self.session.add(
                ExecutionAttempt(
                    action_item_id=item_id, idempotency_key=key, attempt_number=attempt,
                    provider=provider.id, mode=mode, outcome="RUNNING",
                )
            )
            await self.session.commit()
            return await adapter.execute(payload, ctx)

        outcome = await with_retry(
            dispatch,
            is_retryable=lambda e: isinstance(e, ProviderError) and e.retryable,
            retry_after_ms=lambda e: getattr(e, "retry_after_ms", None),
            on_attempt=lambda attempt, delay, err: log.warn(
                "execution.retrying", attempt=attempt, delayMs=delay,
                code=getattr(err, "code", None), providerDetail=getattr(err, "detail", None),
            ),
        )

        finished = now_ms()

        # ── 11 + 12. record and audit, in ONE transaction
        if outcome.ok:
            result = outcome.value
            await self.session.execute(
                update(ExecutionAttempt)
                .where(
                    ExecutionAttempt.action_item_id == item_id,
                    ExecutionAttempt.idempotency_key == key,
                    ExecutionAttempt.attempt_number == attempt_number,
                    ExecutionAttempt.outcome == "RUNNING",
                )
                .values(
                    outcome="SUCCESS", external_id=result.external_id,
                    external_url=result.external_url, finished_at=finished,
                    duration_ms=outcome.attempts[-1].duration_ms,
                )
            )
            execution_result = {
                # A port of ExecutionResultJson from the original TypeScript executor —
                # every field here is read somewhere (a DTO field, a toast, the audit
                # trail's "what was sent" record), not speculative richness.
                "version": EXECUTION_RESULT_VERSION,
                "outcome": "SUCCESS",
                "provider": provider.id,
                "mode": mode,
                "externalId": result.external_id,
                "externalUrl": result.external_url,
                # One sentence for the success toast and a future replay — see the
                # ProviderResult docstring in app/integrations/types.py.
                "summary": result.summary,
                # The label that keeps mock mode honest.
                "simulated": result.simulated,
                "startedAt": started.replace(tzinfo=timezone.utc).isoformat(),
                "finishedAt": finished.replace(tzinfo=timezone.utc).isoformat(),
                "durationMs": sum(a.duration_ms for a in outcome.attempts),
                "attempts": _attempt_records(outcome.attempts),
                "warnings": warnings,
                "payloadUsed": payload,
                "error": None,
                "detail": result.detail,
            }
            await self.session.execute(
                update(ActionItem)
                .where(ActionItem.id == item_id)
                .values(
                    status="EXECUTED", executed_at=finished, provider=provider.id,
                    execution_result=execution_result,
                    execution_attempts=ActionItem.execution_attempts + len(outcome.attempts),
                )
            )
            await self.audit.record(
                event="action_item.executed", actor_id=user_id, action_item_id=item_id,
                request_id=request_id,
                metadata={
                    "provider": provider.id, "mode": mode, "externalId": result.external_id,
                    "simulated": result.simulated, "attempts": len(outcome.attempts),
                    "riskTier": risk.tier, "idempotencyKey": key,
                },
            )
            await self.session.commit()
            log.info("execution.succeeded", provider=provider.id, mode=mode,
                     attempts=len(outcome.attempts))
            return {
                "ok": True, "replayed": False, "dryRun": False, "status": "EXECUTED",
                "idempotencyKey": key,
                "riskTier": risk.tier, "riskFactors": risk.factors, "approvalGate": gate.gate,
                "warnings": warnings,
                "result": execution_result,
            }

        # ── terminal failure
        error = outcome.error
        code = getattr(error, "code", "dispatch_failed")
        message = getattr(error, "message", None) or "The provider could not complete the action."
        await self.session.execute(
            update(ExecutionAttempt)
            .where(
                ExecutionAttempt.action_item_id == item_id,
                ExecutionAttempt.idempotency_key == key,
                ExecutionAttempt.attempt_number == attempt_number,
                ExecutionAttempt.outcome == "RUNNING",
            )
            .values(
                outcome="FAILED", error_code=code, error_message=message,
                finished_at=finished, duration_ms=outcome.attempts[-1].duration_ms,
            )
        )
        execution_result = {
            "version": EXECUTION_RESULT_VERSION,
            "outcome": "FAILED",
            "provider": provider.id,
            "mode": mode,
            "externalId": None,
            "externalUrl": None,
            "summary": message,
            "simulated": mode == "mock",
            "startedAt": started.replace(tzinfo=timezone.utc).isoformat(),
            "finishedAt": finished.replace(tzinfo=timezone.utc).isoformat(),
            "durationMs": sum(a.duration_ms for a in outcome.attempts),
            "attempts": _attempt_records(outcome.attempts),
            "warnings": warnings,
            "payloadUsed": payload,
            "error": {
                "code": code, "message": message,
                "retryable": getattr(error, "retryable", False),
                "uncertain": getattr(error, "uncertain", False),
            },
        }
        # FAILED, not back to APPROVED: the status records that a dispatch was attempted and
        # did not succeed, which is different from never having tried. FAILED can retry.
        await self.session.execute(
            update(ActionItem)
            .where(ActionItem.id == item_id)
            .values(
                status="FAILED", execution_result=execution_result,
                execution_attempts=ActionItem.execution_attempts + len(outcome.attempts),
            )
        )
        await self.audit.record(
            event="action_item.execution_failed", actor_id=user_id, action_item_id=item_id,
            request_id=request_id,
            metadata={
                "provider": provider.id, "mode": mode, "errorCode": code,
                "attempts": len(outcome.attempts), "idempotencyKey": key,
                # The provider's own words, for the operator only.
                "providerDetail": getattr(error, "detail", None),
            },
        )
        await self.session.commit()
        log.error("execution.failed", provider=provider.id, code=code,
                  attempts=len(outcome.attempts))
        raise AppError(
            502, "execution_failed", message,
            {"attempts": len(outcome.attempts), "provider": provider.id, "retryable": True},
        )
