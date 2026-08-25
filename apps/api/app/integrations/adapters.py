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


async def _request(
    method: str,
    url: str,
    *,
    token: Optional[str],
    provider: str,
    json: Optional[dict] = None,
    extra_headers: Optional[dict[str, str]] = None,
) -> dict:
    """One transport for every adapter, so error classification is defined once.

    ``extra_headers`` exists for providers that require a header beyond auth. Notion is the
    case that forced it: it mandates ``Notion-Version`` on *every* request and answers
    ``400 missing_version`` without one, so every Notion execution failed. The TypeScript
    adapter always sent it; the port dropped it, and this helper had no way to pass it."""
    headers: dict[str, str] = {}
    if token:
        headers["authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.request(method, url, headers=headers, json=json)
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


async def _post(
    url: str,
    *,
    token: Optional[str],
    json: dict,
    provider: str,
    extra_headers: Optional[dict[str, str]] = None,
) -> dict:
    return await _request(
        "POST", url, token=token, provider=provider, json=json, extra_headers=extra_headers
    )


async def _get(
    url: str,
    *,
    token: Optional[str],
    provider: str,
    extra_headers: Optional[dict[str, str]] = None,
) -> dict:
    return await _request("GET", url, token=token, provider=provider, extra_headers=extra_headers)


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


#: Notion pins behaviour to a dated API version and requires it on every request. Kept
#: identical to the TypeScript adapter's own constant and to the catalogue's `verify`
#: recipe, so all three agree on which contract this code is written against.
_NOTION_VERSION = "2022-06-28"

#: Preference order for the column a due date is written to. A database may name it
#: anything, so fall back to whichever date column exists rather than insisting on one
#: spelling; `None` means the database has no date column and the due date is dropped.
_NOTION_DUE_NAMES = ("due", "due date", "deadline", "date")


def _first_date_property(properties: dict[str, Any]) -> Optional[str]:
    dates = [name for name, spec in properties.items() if spec.get("type") == "date"]
    if not dates:
        return None
    by_lower = {name.lower(): name for name in dates}
    for candidate in _NOTION_DUE_NAMES:
        if candidate in by_lower:
            return by_lower[candidate]
    # A single unambiguous date column is almost certainly the due date; several unnamed
    # ones are a guess this code should not make silently, so take the first consistently.
    return dates[0]


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
        # The per-action id wins; the vault's `taskDatabaseId` is the default behind it.
        # That default is the normal path, not the exception — extraction has no way to
        # know a Notion database id, so `projectId` is essentially always absent.
        database_id = read_string(payload.get("projectId")) or read_string(
            ctx.provider_config.get("taskDatabaseId")
        )
        if not database_id:
            raise ProviderError(
                "missing_database",
                "Notion needs a database id. Set the task database id on the Notion "
                "workspace entry in Settings → API keys, or put one on this action.",
            )
        # Read the database's own schema rather than assuming it matches a template.
        # This used to hardcode "Name" and "Due", which fails on any real workspace: a
        # Notion title property is named "Name" only by default and is routinely renamed,
        # and a date column may simply not exist — which produced
        # `validation_error: "Due is not a property that exists."` and no created page.
        # Notion identifies properties by name in a write, so the names have to be
        # discovered; matching on `type` is the only stable way to find them.
        schema = await _get(
            f"https://api.notion.com/v1/databases/{database_id}",
            token=ctx.access_token, provider="Notion",
            extra_headers={"Notion-Version": _NOTION_VERSION},
        )
        schema_properties: dict[str, Any] = schema.get("properties") or {}
        title_property = next(
            (name for name, spec in schema_properties.items() if spec.get("type") == "title"),
            None,
        )
        if title_property is None:
            # Every Notion database has exactly one title property, so this means the id
            # points at something that is not a database (a plain page, most likely).
            raise ProviderError(
                "not_a_database",
                "That Notion id does not look like a database. Check the task database id "
                "in Settings → API keys — the id must come from a database URL, before the "
                "'?'.",
            )
        date_property = _first_date_property(schema_properties)

        due = read_date(payload.get("dueAt"))
        properties: dict[str, Any] = {
            title_property: {"title": [{"text": {"content": title or "Task"}}]}
        }
        # A due date is dropped rather than fatal when the database has nowhere to put it:
        # losing one field is a far better outcome than refusing to create the task at all.
        if due and date_property:
            properties[date_property] = {"date": {"start": due.date().isoformat()}}
        body = await _post(
            "https://api.notion.com/v1/pages", token=ctx.access_token, provider="Notion",
            json={"parent": {"database_id": database_id}, "properties": properties},
            extra_headers={"Notion-Version": _NOTION_VERSION},
        )
        return ProviderResult(external_id=str(body.get("id") or ""), summary=summary, external_url=body.get("url"))


def _multipart_message(headers: list[str], body: str, attachments: list[Any]) -> str:
    """A `multipart/mixed` message carrying the body and each attachment.

    Hand-rolled rather than via ``email.mime``: the surrounding code already produces a
    header block and base64-urlsafe encodes the whole thing for Gmail's `raw` field, and
    threading that through ``EmailMessage`` would mean two different assembly paths for the
    same message. The boundary is derived from the content so the same inputs produce the
    same bytes — the executor's idempotency key is computed from the payload, and a random
    boundary would make a replayed send differ from the original for no reason.
    """
    import base64
    import hashlib

    digest = hashlib.sha256(
        (body + "".join(getattr(a, "filename", "") for a in attachments)).encode("utf-8")
    ).hexdigest()[:24]
    boundary = f"==_vowcraft_{digest}"

    parts = [
        *headers,
        "MIME-Version: 1.0",
        f'Content-Type: multipart/mixed; boundary="{boundary}"',
        "",
        f"--{boundary}",
        'Content-Type: text/plain; charset="utf-8"',
        "Content-Transfer-Encoding: 7bit",
        "",
        body,
    ]
    for attachment in attachments:
        encoded = base64.b64encode(attachment.content).decode()
        # Wrapped at 76 characters: RFC 2045 caps a base64 line at 76, and Gmail rejects a
        # single unwrapped multi-kilobyte line.
        wrapped = "\r\n".join(encoded[i : i + 76] for i in range(0, len(encoded), 76))
        parts += [
            f"--{boundary}",
            f'Content-Type: {attachment.mime_type}; charset="utf-8"; name="{attachment.filename}"',
            "Content-Transfer-Encoding: base64",
            f'Content-Disposition: attachment; filename="{attachment.filename}"',
            "",
            wrapped,
        ]
    parts.append(f"--{boundary}--")
    return "\r\n".join(parts)


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
        # Named in the preview because the confirmation modal is the last point at which a
        # human can notice the wrong document is about to leave. `preview` has no database,
        # so it reports the count and the ids the payload carries; the executor resolves the
        # titles. A silent attachment would defeat the modal's purpose.
        attachment_entries = payload.get("attachments")
        if isinstance(attachment_entries, list) and attachment_entries:
            fields.append(
                {
                    "label": "Attachments",
                    "value": f"{len(attachment_entries)} document"
                    f"{'' if len(attachment_entries) == 1 else 's'}",
                }
            )
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
                detail={
                    "sendMode": send_mode, "to": to, "subject": subject,
                    # Attachments are resolved before dispatch even in mock mode, so a
                    # simulated run reports what a real one would actually carry.
                    "attachments": [getattr(a, "filename", "") for a in ctx.attachments],
                },
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
        body_text = read_string(payload.get("body")) or ""

        if ctx.attachments:
            message = _multipart_message(headers, body_text, ctx.attachments)
        else:
            # Kept as the plain single-part form rather than always going multipart: a
            # one-part MIME message is what every existing draft looks like, and changing
            # the shape of mail that needs no attachment would be a gratuitous difference.
            message = "\r\n".join(headers) + "\r\n\r\n" + body_text

        raw = base64.urlsafe_b64encode(message.encode("utf-8")).decode().rstrip("=")

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


def _sendgrid_from(
    payload: dict[str, Any], *, provider_config: Optional[dict[str, str]], user_email: str
) -> str:
    """The From address, in one place so preview and execute cannot drift apart again.

    Order: an explicit `from` on the action, then the vault's `fromEmail`, then the
    environment, then the user's own login as a last resort. That last fallback is almost
    always wrong for SendGrid — it refuses any sender it has not verified — but failing with
    SendGrid's own "does not match a verified Sender Identity" is more informative than
    refusing to send with a message of our own invention.

    `provider_config` is None on the preview path, which has no database access; the vault
    tier is therefore only consulted at execution. The preview still shows the same answer
    whenever the address comes from the action or the environment.
    """
    explicit = read_string(payload.get("from"))
    if explicit:
        return explicit
    if provider_config:
        configured = (provider_config.get("fromEmail") or "").strip()
        if configured:
            return configured
    return (os.environ.get("SENDGRID_FROM_EMAIL") or "").strip() or user_email


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
        # Preview and execute must agree: the confirmation modal's entire promise is that it
        # shows the payload the *same code path* will send. They disagreed — preview resolved
        # this chain while execute hardcoded the user's login — so the modal named one sender
        # and SendGrid was handed another (which it then refused, being unverified).
        from_address = _sendgrid_from(payload, provider_config=None, user_email=user_email)
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
                "from": {
                    "email": _sendgrid_from(
                        payload, provider_config=ctx.provider_config, user_email=ctx.user_email
                    )
                },
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
