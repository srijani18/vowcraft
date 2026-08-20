"""The five provider adapters — SPEC-002 §4.

Each has the same shape: validate, then branch on mode. The mock branch returns a
deterministic fake with ``simulated=True``; the live branch makes the real call.

Mock mode exists so the whole approval workflow is demonstrable without connecting four
OAuth apps, and it is honest about itself rather than pretending. A simulated result that
looked real would undermine the one thing this system sells: that you can trust what it
tells you happened.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

from app.domain.payload import is_valid_email, missing_fields, to_email_list
from app.domain.timeutil import human_time, read_date, read_number, read_string
from app.integrations.types import ExecutionContext, ProviderError, ProviderResult

_TIMEOUT = 20.0


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else f"{value[: limit - 1]}…"


def _mock_id(provider: str, ctx: ExecutionContext) -> str:
    """Deterministic from the idempotency key, so a replayed mock returns the same id.

    A random id would make mock mode behave differently from live mode on the one property
    the executor cares most about.
    """
    digest = hashlib.sha256(f"{provider}:{ctx.idempotency_key}".encode()).hexdigest()[:16]
    return f"mock_{provider}_{digest}"


def _require(payload: dict[str, Any], action_type: str) -> list[str]:
    return missing_fields(action_type, payload)


async def _post(url: str, *, token: Optional[str], json: dict, provider: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(
                url,
                headers={"authorization": f"Bearer {token}"} if token else {},
                json=json,
            )
    except httpx.TimeoutException as exc:
        raise ProviderError(
            "timeout", f"{provider} did not respond in time.", retryable=True
        ) from exc
    except httpx.HTTPError as exc:
        raise ProviderError(
            "unreachable", f"{provider} was unreachable.", retryable=True
        ) from exc

    if response.status_code >= 400:
        retry_after = response.headers.get("retry-after")
        raise ProviderError(
            f"http_{response.status_code}",
            # Written for the user; the body goes to `detail`.
            f"{provider} rejected the request."
            if response.status_code < 500
            else f"{provider} had a server error.",
            retryable=response.status_code in (408, 429, 500, 502, 503, 504),
            status=response.status_code,
            detail=response.text[:2000],
            retry_after_ms=int(float(retry_after) * 1000) if retry_after and retry_after.isdigit() else None,
        )
    return response.json() if response.content else {}


class GoogleCalendarAdapter:
    id = "google_calendar"
    display_name = "Google Calendar"
    capability = "CALENDAR"

    def validate(self, payload: dict[str, Any]) -> list[str]:
        missing = _require(payload, "CALENDAR")
        for address in to_email_list(payload.get("attendees")):
            if not is_valid_email(address):
                missing.append("attendees")
                break
        return missing

    def preview(self, payload: dict[str, Any], *, time_zone: str, user_email: str) -> dict[str, Any]:
        tz = read_string(payload.get("timeZone")) or time_zone
        start = read_date(payload.get("startsAt"))
        minutes = read_number(payload.get("durationMinutes")) or 30
        end = start + timedelta(minutes=minutes) if start else None
        attendees = to_email_list(payload.get("attendees"))
        fields = [
            {"label": "Title", "value": read_string(payload.get("title")) or ""},
            {
                "label": "When",
                "value": f"{human_time(start, tz)} – {human_time(end, tz)} ({tz})" if start and end else "",
            },
            {"label": "Duration", "value": f"{int(minutes)} min"},
            {"label": "Attendees", "value": ", ".join(attendees)},
        ]
        location = read_string(payload.get("location"))
        if location:
            fields.append({"label": "Location", "value": location})
        return {
            "provider": self.id,
            "consequence": (
                f"Creates a calendar event and emails an invitation to {len(attendees)} "
                f"{'person' if len(attendees) == 1 else 'people'}."
            ),
            "fields": fields,
        }

    async def execute(self, payload: dict[str, Any], ctx: ExecutionContext) -> ProviderResult:
        start = read_date(payload.get("startsAt"))
        minutes = read_number(payload.get("durationMinutes")) or 30
        if start is None:
            raise ProviderError("invalid_payload", "The start time is not a usable date.")
        end = start + timedelta(minutes=minutes)
        attendees = to_email_list(payload.get("attendees"))
        title = read_string(payload.get("title")) or ""
        # No weekday here, unlike `human_time` — matches the TS original's summary format
        # exactly ("21 Aug, 10:00"), distinct from the weekday-carrying "When" preview field.
        local_start = start.astimezone(ZoneInfo(ctx.time_zone))
        when = f"{local_start.day} {local_start.strftime('%b')}, {local_start.strftime('%H:%M')}"
        summary = (
            f'Created "{title}" on {when} '
            f"for {len(attendees)} guest{'' if len(attendees) == 1 else 's'}."
        )

        if ctx.mode == "mock":
            return ProviderResult(
                external_id=_mock_id(self.id, ctx), summary=summary, simulated=True,
                external_url=f"https://calendar.google.com/calendar/r/eventedit/{_mock_id(self.id, ctx)}",
                detail={"title": title, "startsAt": start.isoformat(), "attendees": attendees},
            )

        if not ctx.access_token:
            raise ProviderError(
                "not_connected",
                "Google Calendar is not connected. Connect it in Settings → Integrations.",
            )
        body = await _post(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events?sendUpdates=all",
            token=ctx.access_token, provider="Google Calendar",
            json={
                "summary": title,
                "description": read_string(payload.get("description")),
                "location": read_string(payload.get("location")),
                "start": {"dateTime": start.isoformat(), "timeZone": ctx.time_zone},
                "end": {"dateTime": end.isoformat(), "timeZone": ctx.time_zone},
                "attendees": [{"email": a} for a in attendees],
            },
        )
        return ProviderResult(
            external_id=str(body.get("id") or _mock_id(self.id, ctx)),
            summary=summary,
            external_url=body.get("htmlLink"),
        )


class NotionAdapter:
    id = "notion"
    display_name = "Notion"
    capability = "TASK"

    def validate(self, payload: dict[str, Any]) -> list[str]:
        return _require(payload, "TASK")

    def preview(self, payload: dict[str, Any], *, time_zone: str, user_email: str) -> dict[str, Any]:
        assignee = read_string(payload.get("assignee"))
        due = read_date(payload.get("dueAt"))
        notes = read_string(payload.get("notes"))
        fields = [{"label": "Title", "value": read_string(payload.get("title")) or ""}]
        if due:
            local = due.astimezone(ZoneInfo(time_zone))
            fields.append({"label": "Due", "value": f"{local.day} {local.strftime('%b')} {local.year}"})
        if assignee:
            fields.append({"label": "Assignee", "value": assignee})
        if notes:
            fields.append({"label": "Notes", "value": _truncate(notes, 180)})
        return {
            "provider": self.id,
            "consequence": (
                f"Creates a Notion page in your task database"
                f"{f' assigned to {assignee}' if assignee else ''}."
            ),
            "fields": fields,
        }

    async def execute(self, payload: dict[str, Any], ctx: ExecutionContext) -> ProviderResult:
        title = read_string(payload.get("title")) or ""
        summary = f'Created Notion task "{title}".'
        if ctx.mode == "mock":
            return ProviderResult(
                external_id=_mock_id(self.id, ctx), summary=summary, simulated=True,
                detail={"title": title, "assignee": payload.get("assignee")},
            )
        if not ctx.access_token:
            raise ProviderError(
                "not_connected", "Notion is not connected. Connect it in Settings → Integrations."
            )
        database_id = read_string(payload.get("projectId"))
        if not database_id:
            raise ProviderError(
                "missing_database",
                "Notion needs a database id. Add it to the task, or set a default in Settings.",
            )
        due = read_date(payload.get("dueAt"))
        properties: dict[str, Any] = {
            "Name": {"title": [{"text": {"content": title or "Task"}}]}
        }
        if due:
            properties["Due"] = {"date": {"start": due.date().isoformat()}}
        body = await _post(
            "https://api.notion.com/v1/pages", token=ctx.access_token, provider="Notion",
            json={"parent": {"database_id": database_id}, "properties": properties},
        )
        return ProviderResult(external_id=str(body.get("id") or ""), summary=summary, external_url=body.get("url"))


class GmailAdapter:
    id = "gmail"
    display_name = "Gmail"
    capability = "EMAIL"

    def validate(self, payload: dict[str, Any]) -> list[str]:
        missing = _require(payload, "EMAIL")
        for key in ("to", "cc", "bcc"):
            for address in to_email_list(payload.get(key)):
                if not is_valid_email(address):
                    missing.append(key)
                    break
        return missing

    def preview(self, payload: dict[str, Any], *, time_zone: str, user_email: str) -> dict[str, Any]:
        to = to_email_list(payload.get("to"))
        cc = to_email_list(payload.get("cc"))
        recipients = to + cc
        send_mode = read_string(payload.get("sendMode")) or "draft"
        body = read_string(payload.get("body")) or ""
        fields = [
            {"label": "Mode", "value": "Send now" if send_mode == "send" else "Save as draft"},
            {"label": "To", "value": ", ".join(to)},
        ]
        if cc:
            fields.append({"label": "Cc", "value": ", ".join(cc)})
        fields.append({"label": "Subject", "value": read_string(payload.get("subject")) or ""})
        fields.append({"label": "Body", "value": _truncate(body, 400)})
        return {
            "provider": self.id,
            "consequence": (
                f"Sends an email immediately to {len(recipients)} "
                f"recipient{'' if len(recipients) == 1 else 's'}. This cannot be undone."
                if send_mode == "send"
                else "Saves a draft in your Gmail. Nothing is sent until you send it."
            ),
            "fields": fields,
        }

    async def execute(self, payload: dict[str, Any], ctx: ExecutionContext) -> ProviderResult:
        send_mode = read_string(payload.get("sendMode")) or "draft"
        to = to_email_list(payload.get("to"))
        subject = read_string(payload.get("subject")) or ""
        summary = (
            f'Sent "{subject}" to {", ".join(to)}.'
            if send_mode == "send"
            else f'Drafted "{subject}" to {", ".join(to)}.'
        )
        if ctx.mode == "mock":
            return ProviderResult(
                external_id=_mock_id(self.id, ctx), summary=summary, simulated=True,
                detail={"sendMode": send_mode, "to": to, "subject": subject},
            )
        if not ctx.access_token:
            raise ProviderError(
                "not_connected", "Gmail is not connected. Connect it in Settings → Integrations."
            )

        import base64

        headers = [
            f"To: {', '.join(to)}",
            f"Subject: {subject}",
        ]
        cc = to_email_list(payload.get("cc"))
        if cc:
            headers.append(f"Cc: {', '.join(cc)}")
        raw = base64.urlsafe_b64encode(
            ("\r\n".join(headers) + "\r\n\r\n" + (read_string(payload.get("body")) or "")).encode()
        ).decode().rstrip("=")

        # Drafts and sends are different endpoints, and the distinction is the difference
        # between LOW and HIGH risk. It is preserved exactly.
        if send_mode == "send":
            body = await _post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                token=ctx.access_token, provider="Gmail", json={"raw": raw},
            )
            return ProviderResult(external_id=str(body.get("id") or ""), summary=summary)
        body = await _post(
            "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
            token=ctx.access_token, provider="Gmail", json={"message": {"raw": raw}},
        )
        return ProviderResult(
            external_id=str(body.get("id") or ""), summary=summary, detail={"sendMode": "draft"}
        )


class SendGridAdapter:
    id = "sendgrid"
    display_name = "SendGrid"
    capability = "EMAIL"

    def validate(self, payload: dict[str, Any]) -> list[str]:
        # A port of the TypeScript schema's `.refine((v) => v.sendMode !== 'draft', ...)`:
        # a validation-time rejection, not a dispatch-time one, so it fires on a dry run
        # too — the same reasoning as the identical check in `execute()` below applies at
        # the moment the reviewer opens the confirmation modal, not just when they click
        # through it.
        if (read_string(payload.get("sendMode")) or "draft") == "draft":
            raise ProviderError(
                "drafts_unsupported",
                "SendGrid cannot save drafts, only send. Switch this action to Gmail, or "
                "change the send mode to 'send' and approve it again.",
            )
        return GmailAdapter().validate(payload)

    def preview(self, payload: dict[str, Any], *, time_zone: str, user_email: str) -> dict[str, Any]:
        to = to_email_list(payload.get("to"))
        cc = to_email_list(payload.get("cc"))
        bcc = to_email_list(payload.get("bcc"))
        recipients = to + cc + bcc
        from_address = read_string(payload.get("from")) or os.environ.get("SENDGRID_FROM_EMAIL") or user_email
        body = read_string(payload.get("body")) or ""
        fields = [{"label": "From", "value": from_address}, {"label": "To", "value": ", ".join(to)}]
        if cc:
            fields.append({"label": "Cc", "value": ", ".join(cc)})
        if bcc:
            fields.append({"label": "Bcc", "value": ", ".join(bcc)})
        fields.append({"label": "Subject", "value": read_string(payload.get("subject")) or ""})
        fields.append({"label": "Body", "value": _truncate(body, 400)})
        return {
            "provider": self.id,
            "consequence": (
                f"Delivers an email immediately to {len(recipients)} "
                f"recipient{'' if len(recipients) == 1 else 's'} from {from_address}. "
                "This cannot be undone."
            ),
            "fields": fields,
        }

    async def execute(self, payload: dict[str, Any], ctx: ExecutionContext) -> ProviderResult:
        # A draft `sendMode` never reaches here: `validate()` above refuses it, and the
        # executor always validates before dispatching.
        to = to_email_list(payload.get("to"))
        subject = read_string(payload.get("subject")) or ""
        summary = f'Sent "{subject}" to {", ".join(to)} via SendGrid.'
        if ctx.mode == "mock":
            return ProviderResult(
                external_id=_mock_id(self.id, ctx), summary=summary, simulated=True,
                detail={"sendMode": "send", "to": to},
            )
        if not ctx.api_key:
            raise ProviderError(
                "not_connected", "SendGrid needs an API key. Add one in Settings → API keys."
            )
        await _post(
            "https://api.sendgrid.com/v3/mail/send", token=ctx.api_key, provider="SendGrid",
            json={
                "personalizations": [{"to": [{"email": a} for a in to]}],
                "from": {"email": ctx.user_email},
                "subject": subject,
                "content": [{"type": "text/plain", "value": read_string(payload.get("body")) or ""}],
            },
        )
        # SendGrid returns 202 with an empty body; the message id is in a header we do not
        # get back from the helper, so the idempotency key stands in as the reference.
        return ProviderResult(external_id=f"sg_{ctx.idempotency_key[3:19]}", summary=summary)


class SlackAdapter:
    id = "slack"
    display_name = "Slack"
    capability = "REMINDER"

    def validate(self, payload: dict[str, Any]) -> list[str]:
        missing = _require(payload, "REMINDER")
        channel = read_string(payload.get("channel"))
        if channel in ("slack", "email") and not read_string(payload.get("target")):
            missing.append("target")
        return missing

    def preview(self, payload: dict[str, Any], *, time_zone: str, user_email: str) -> dict[str, Any]:
        channel = read_string(payload.get("channel"))
        target = read_string(payload.get("target"))
        where = "you, as a direct message" if channel == "self" else (target or channel or "")
        remind_at = read_date(payload.get("remindAt"))
        fields = [{"label": "Message", "value": read_string(payload.get("message")) or ""}]
        if remind_at:
            fields.append({"label": "When", "value": human_time(remind_at, time_zone)})
        fields.append({"label": "Destination", "value": where})
        return {
            "provider": self.id,
            "consequence": f"Schedules a Slack message to {where}.",
            "fields": fields,
        }

    async def execute(self, payload: dict[str, Any], ctx: ExecutionContext) -> ProviderResult:
        remind_at = read_date(payload.get("remindAt"))
        summary = f"Scheduled a Slack reminder for {remind_at.isoformat().replace('+00:00', 'Z')}." if remind_at else "Scheduled a Slack reminder."
        if ctx.mode == "mock":
            return ProviderResult(
                external_id=_mock_id(self.id, ctx), summary=summary, simulated=True,
                detail={"channel": payload.get("channel"), "message": payload.get("message")},
            )
        if not ctx.access_token:
            raise ProviderError(
                "not_connected", "Slack is not connected. Connect it in Settings → Integrations."
            )
        body = await _post(
            "https://slack.com/api/chat.postMessage", token=ctx.access_token, provider="Slack",
            json={
                "channel": read_string(payload.get("target")) or read_string(payload.get("channel")),
                "text": read_string(payload.get("message")) or "",
            },
        )
        # Slack answers 200 with `ok: false` for application errors, so the status code alone
        # is not enough to know whether it worked.
        if not body.get("ok"):
            raise ProviderError(
                "slack_rejected",
                "Slack rejected the message. The channel may not exist, or the app may not be "
                "in it.",
                detail=str(body)[:500],
            )
        return ProviderResult(external_id=str(body.get("ts") or ""), summary=summary)


ADAPTERS: dict[str, Any] = {
    "google_calendar": GoogleCalendarAdapter(),
    "notion": NotionAdapter(),
    "gmail": GmailAdapter(),
    "sendgrid": SendGridAdapter(),
    "slack": SlackAdapter(),
}


def adapter_for(provider_id: str):
    return ADAPTERS.get(provider_id)
