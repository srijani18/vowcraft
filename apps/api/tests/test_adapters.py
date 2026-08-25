"""Provider adapters — the wire format, not the routing (SPEC-002 §4).

Pure-unit: every HTTP call is stubbed, so nothing here touches a real provider or needs a
credential. What they pin is the shape of the *request* an adapter builds, which is the half
no amount of guardrail or approval testing exercises — the executor dispatches a malformed
request perfectly happily and then reports the provider's rejection as a provider problem.

Two real bugs motivated this file, both found only by a user hitting them:

* **``missing_version``.** Notion mandates a ``Notion-Version`` header on every request.
  The TypeScript adapter always sent it; the Python port dropped it and ``_post`` had no
  way to pass it, so *every* Notion execution failed.
* **``"Due is not a property that exists."``** The adapter hardcoded the property names
  ``Name`` and ``Due``. Neither is guaranteed: a Notion title property is called "Name"
  only by default and is routinely renamed, and a date column may not exist at all. So it
  worked against a database shaped like the author's and no other.
"""

from __future__ import annotations

import httpx
import pytest

from app.integrations.adapters import NotionAdapter, _NOTION_VERSION
from app.integrations.types import ExecutionContext, ProviderError

#: What a default Notion task database looks like.
DEFAULT_SCHEMA = {"properties": {"Name": {"type": "title"}, "Due": {"type": "date"}}}

PAYLOAD = {"title": "Write up the release notes", "dueAt": "2026-08-28T00:00:00Z"}


@pytest.fixture
def notion_stub(monkeypatch):
    """Installs a fake Notion. Returns the calls it received, split by method.

    Patches ``request`` rather than ``post`` because the adapter now reads the database
    schema before writing — the GET is part of the contract under test, not incidental.
    """

    def _install(schema=DEFAULT_SCHEMA, post_status: int = 200, post_body=None):
        calls: dict[str, list] = {"get": [], "post": []}

        async def _request(self, method, url, **kwargs):  # noqa: ANN001, ANN003
            entry = {
                "url": url,
                "headers": kwargs.get("headers") or {},
                "json": kwargs.get("json"),
            }
            if str(method).upper() == "GET":
                calls["get"].append(entry)
                return httpx.Response(200, json=schema, request=httpx.Request("GET", url))
            calls["post"].append(entry)
            return httpx.Response(
                post_status,
                json=post_body or {"id": "page-1", "url": "https://notion.so/page-1"},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx.AsyncClient, "request", _request)
        return calls

    return _install


def _ctx(**overrides) -> ExecutionContext:
    base = dict(
        mode="live",
        user_email="reviewer@acme.test",
        time_zone="UTC",
        request_id="req-1",
        idempotency_key="ik-1",
        access_token="secret_notion-token",
        provider_config={"taskDatabaseId": "db-1"},
    )
    base.update(overrides)
    return ExecutionContext(**base)


class TestNotionHeaders:
    async def test_every_request_carries_the_notion_version(self, notion_stub):
        """The regression guard. Without this header Notion 400s — and it is needed on the
        schema read as much as on the write."""
        calls = notion_stub()

        await NotionAdapter().execute(PAYLOAD, _ctx())

        assert calls["get"], "no schema read was made"
        assert calls["post"], "no page was created"
        for call in calls["get"] + calls["post"]:
            assert call["headers"].get("Notion-Version") == _NOTION_VERSION

    async def test_the_bearer_token_survives_alongside_it(self, notion_stub):
        """`extra_headers` merges with auth rather than replacing it — getting that wrong
        would trade a 400 for a 401."""
        calls = notion_stub()

        await NotionAdapter().execute(PAYLOAD, _ctx())

        assert calls["post"][0]["headers"]["authorization"] == "Bearer secret_notion-token"


class TestNotionDatabaseId:
    async def test_the_vault_default_becomes_the_parent(self, notion_stub):
        """Extraction cannot know a Notion database id, so the vault default is the normal
        path to a working request, not a rarely-taken fallback."""
        calls = notion_stub()

        await NotionAdapter().execute(PAYLOAD, _ctx())

        assert calls["post"][0]["json"]["parent"] == {"database_id": "db-1"}

    async def test_a_per_action_id_wins_over_the_vault_default(self, notion_stub):
        calls = notion_stub()

        await NotionAdapter().execute({**PAYLOAD, "projectId": "db-on-action"}, _ctx())

        assert calls["post"][0]["json"]["parent"] == {"database_id": "db-on-action"}

    async def test_no_id_anywhere_names_the_setting_that_fixes_it(self, notion_stub):
        """The message used to promise "set a default in Settings" for a field that did not
        exist. An error naming a nonexistent remedy is worse than a vague one."""
        notion_stub()

        with pytest.raises(ProviderError) as excinfo:
            await NotionAdapter().execute(PAYLOAD, _ctx(provider_config={}))

        assert excinfo.value.code == "missing_database"
        assert "Settings → API keys" in excinfo.value.message


class TestNotionSchemaDiscovery:
    async def test_the_title_property_is_found_by_type_not_by_name(self, notion_stub):
        """The bug this replaced: "Name" is only Notion's default, and renaming it is
        ordinary. Matching on `type == "title"` is the only stable identification."""
        calls = notion_stub(
            schema={"properties": {"Task": {"type": "title"}, "Due": {"type": "date"}}}
        )

        await NotionAdapter().execute(PAYLOAD, _ctx())

        properties = calls["post"][0]["json"]["properties"]
        assert "Task" in properties, "did not write to the renamed title property"
        assert "Name" not in properties
        assert properties["Task"]["title"][0]["text"]["content"] == PAYLOAD["title"]

    async def test_a_due_date_is_dropped_when_there_is_no_date_column(self, notion_stub):
        """The exact failure a user hit: a database with no date column produced
        `validation_error: "Due is not a property that exists."` and no page at all. Losing
        one field beats refusing to create the task."""
        calls = notion_stub(schema={"properties": {"Name": {"type": "title"}}})

        result = await NotionAdapter().execute(PAYLOAD, _ctx())

        properties = calls["post"][0]["json"]["properties"]
        assert list(properties) == ["Name"]
        assert result.external_id == "page-1", "the page should still have been created"

    async def test_a_differently_named_date_column_is_used(self, notion_stub):
        calls = notion_stub(
            schema={"properties": {"Name": {"type": "title"}, "Deadline": {"type": "date"}}}
        )

        await NotionAdapter().execute(PAYLOAD, _ctx())

        assert "Deadline" in calls["post"][0]["json"]["properties"]

    async def test_a_date_column_is_left_empty_when_the_action_has_no_due_date(
        self, notion_stub
    ):
        calls = notion_stub()

        await NotionAdapter().execute({"title": "No due date"}, _ctx())

        assert list(calls["post"][0]["json"]["properties"]) == ["Name"]

    async def test_an_id_that_is_not_a_database_says_so(self, notion_stub):
        """Every Notion database has exactly one title property. None means the id points
        at a page — most often the view id, or an inline database's parent."""
        notion_stub(schema={"properties": {"Tags": {"type": "multi_select"}}})

        with pytest.raises(ProviderError) as excinfo:
            await NotionAdapter().execute(PAYLOAD, _ctx())

        assert excinfo.value.code == "not_a_database"


class TestNotionFailureReporting:
    async def test_mock_mode_makes_no_request_at_all(self, notion_stub):
        """Mock mode's whole promise is that nothing leaves the process."""
        calls = notion_stub()

        result = await NotionAdapter().execute(PAYLOAD, _ctx(mode="mock"))

        assert result.simulated is True
        assert calls == {"get": [], "post": []}, "mock mode reached the network"

    async def test_a_missing_token_is_reported_as_not_connected(self, notion_stub):
        notion_stub()

        with pytest.raises(ProviderError) as excinfo:
            await NotionAdapter().execute(PAYLOAD, _ctx(access_token=None))

        assert excinfo.value.code == "not_connected"

    async def test_the_providers_body_stays_out_of_the_user_message(self, notion_stub):
        """SPEC-002 §3.4: provider bodies echo request detail and sometimes credential
        fragments. They belong in `detail`, which is logged, never rendered."""
        notion_stub(
            post_status=400,
            post_body={"code": "validation_error", "message": "secret_leaked_value"},
        )

        with pytest.raises(ProviderError) as excinfo:
            await NotionAdapter().execute(PAYLOAD, _ctx())

        assert "secret_leaked_value" not in str(excinfo.value)
        # …but it is still there for the operator, which is how the two real bugs above
        # were actually diagnosed.
        assert "secret_leaked_value" in (excinfo.value.detail or "")

class TestGmailAttachments:
    """Multipart assembly — SPEC-002 §5.1.

    Parsed back with the stdlib rather than string-matched: the assertion that matters is
    that a real MIME parser agrees this is a well-formed message with the attachment
    intact, not that the bytes contain a boundary somewhere.
    """

    @staticmethod
    def _sent_message(calls) -> str:
        import base64

        raw = calls["post"][0]["json"]["message"]["raw"]
        # urlsafe, and the adapter strips padding for Gmail.
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")

    @pytest.fixture
    def gmail_stub(self, monkeypatch):
        calls: dict[str, list] = {"post": []}

        async def _request(self, method, url, **kwargs):  # noqa: ANN001, ANN003
            calls["post"].append({"url": url, "json": kwargs.get("json")})
            return httpx.Response(
                200, json={"id": "draft-1"}, request=httpx.Request("POST", url)
            )

        monkeypatch.setattr(httpx.AsyncClient, "request", _request)
        return calls

    def _attachment(self, filename="payments-service.md", content=b"# Payments service\n"):
        from app.services.attachments import ResolvedAttachment

        return ResolvedAttachment(
            filename=filename, mime_type="text/markdown", content=content
        )

    async def test_an_attachment_produces_a_parseable_multipart_message(self, gmail_stub):
        from email import message_from_string

        from app.integrations.adapters import GmailAdapter

        await GmailAdapter().execute(
            {"to": ["anjali@acme.test"], "subject": "BRD", "body": "Hi Anjali.",
             "sendMode": "draft"},
            _ctx(attachments=[self._attachment()]),
        )

        parsed = message_from_string(self._sent_message(gmail_stub))
        assert parsed.is_multipart()
        parts = parsed.get_payload()
        assert len(parts) == 2, "expected a body part and one attachment"
        assert parts[0].get_payload() == "Hi Anjali."
        assert parts[1].get_filename() == "payments-service.md"
        assert parts[1].get_payload(decode=True) == b"# Payments service\n"

    async def test_no_attachment_keeps_the_plain_single_part_form(self, gmail_stub):
        """Mail needing no attachment must not change shape — every existing draft looks
        like this, and a gratuitous multipart wrapper would alter all of them."""
        from email import message_from_string

        from app.integrations.adapters import GmailAdapter

        await GmailAdapter().execute(
            {"to": ["anjali@acme.test"], "subject": "BRD", "body": "Hi Anjali.",
             "sendMode": "draft"},
            _ctx(attachments=[]),
        )

        parsed = message_from_string(self._sent_message(gmail_stub))
        assert not parsed.is_multipart()
        assert parsed.get_payload() == "Hi Anjali."

    async def test_the_boundary_is_stable_for_the_same_content(self, gmail_stub):
        """Derived from the content, not random: the executor's idempotency key is computed
        from the payload, so a replayed send must produce the same bytes."""
        from app.integrations.adapters import GmailAdapter

        payload = {"to": ["a@acme.test"], "subject": "S", "body": "B", "sendMode": "draft"}
        await GmailAdapter().execute(payload, _ctx(attachments=[self._attachment()]))
        await GmailAdapter().execute(payload, _ctx(attachments=[self._attachment()]))

        first, second = (self._sent_message({"post": [c]}) for c in gmail_stub["post"][:2])
        assert first == second

    async def test_a_long_attachment_is_line_wrapped(self, gmail_stub):
        """Gmail rejects a single unwrapped multi-kilobyte base64 line (RFC 2045 caps it
        at 76 characters), and a document is easily that long."""
        from email import message_from_string

        from app.integrations.adapters import GmailAdapter

        big = ("# Heading\n" + "content line\n" * 500).encode()
        await GmailAdapter().execute(
            {"to": ["a@acme.test"], "subject": "S", "body": "B", "sendMode": "draft"},
            _ctx(attachments=[self._attachment(content=big)]),
        )

        message = self._sent_message(gmail_stub)
        encoded_lines = [
            line for line in message.splitlines() if len(line) > 76 and " " not in line
        ]
        assert not encoded_lines, "an unwrapped base64 line would be rejected"
        # And it must still round-trip.
        parsed = message_from_string(message)
        assert parsed.get_payload()[1].get_payload(decode=True) == big

    async def test_attachments_are_named_in_the_preview(self):
        """The confirmation modal is the last place a human can catch the wrong document
        leaving, so a silent attachment would defeat its purpose."""
        from app.integrations.adapters import GmailAdapter

        preview = GmailAdapter().preview(
            {"to": ["a@acme.test"], "subject": "S", "body": "B", "sendMode": "draft",
             "attachments": [{"kind": "brd", "documentId": "d1"}]},
            time_zone="UTC", user_email="me@acme.test",
        )

        labels = {field["label"]: field["value"] for field in preview["fields"]}
        assert labels.get("Attachments") == "1 document"


class TestSendGridFromAddress:
    """SPEC-002 §5. The From address is the one field that decides whether SendGrid works
    at all: it refuses any sender it has not verified, so a wrong value is a 403 on every
    send rather than a cosmetic slip.

    `preview` resolved a chain (`payload.from` → env → login) while `execute` hardcoded the
    login. So the confirmation modal named one sender and SendGrid was handed another —
    breaking the modal's central promise, that it shows what the same code path will send.
    """

    @staticmethod
    def _ctx(**overrides):
        base = dict(
            mode="live", user_email="me@acme.test", time_zone="UTC", request_id="r",
            idempotency_key="ik_0123456789abcdef0123", api_key="SG.key",
        )
        base.update(overrides)
        return ExecutionContext(**base)

    PAYLOAD = {"to": ["them@vendor.test"], "subject": "S", "body": "B", "sendMode": "send"}

    @pytest.fixture
    def sendgrid_stub(self, monkeypatch):
        calls: list[dict] = []

        async def _request(self, method, url, **kwargs):  # noqa: ANN001, ANN003
            calls.append({"url": url, "json": kwargs.get("json")})
            return httpx.Response(202, json={}, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx.AsyncClient, "request", _request)
        return calls

    async def test_the_vault_from_address_is_actually_sent(self, sendgrid_stub, monkeypatch):
        """The regression. Before this, `fromEmail` could be set and was silently ignored."""
        from app.integrations.adapters import SendGridAdapter

        monkeypatch.delenv("SENDGRID_FROM_EMAIL", raising=False)
        await SendGridAdapter().execute(
            self.PAYLOAD, self._ctx(provider_config={"fromEmail": "verified@acme.test"})
        )

        assert sendgrid_stub[0]["json"]["from"] == {"email": "verified@acme.test"}

    async def test_an_explicit_from_on_the_action_wins(self, sendgrid_stub):
        from app.integrations.adapters import SendGridAdapter

        await SendGridAdapter().execute(
            {**self.PAYLOAD, "from": "chosen@acme.test"},
            self._ctx(provider_config={"fromEmail": "verified@acme.test"}),
        )

        assert sendgrid_stub[0]["json"]["from"] == {"email": "chosen@acme.test"}

    async def test_it_falls_back_to_the_login_when_nothing_is_configured(
        self, sendgrid_stub, monkeypatch
    ):
        """Almost certainly unverified, and therefore a 403 — but SendGrid's own "does not
        match a verified Sender Identity" is more informative than a message of our own."""
        from app.integrations.adapters import SendGridAdapter

        monkeypatch.delenv("SENDGRID_FROM_EMAIL", raising=False)
        await SendGridAdapter().execute(self.PAYLOAD, self._ctx(provider_config={}))

        assert sendgrid_stub[0]["json"]["from"] == {"email": "me@acme.test"}

    async def test_the_preview_and_the_send_agree_on_the_sender(
        self, sendgrid_stub, monkeypatch
    ):
        """The property that was broken. Asserted against the *same* inputs, because a modal
        that names a different sender than the one used is worse than no modal."""
        from app.integrations.adapters import SendGridAdapter

        monkeypatch.setenv("SENDGRID_FROM_EMAIL", "env@acme.test")
        adapter = SendGridAdapter()

        preview = adapter.preview(self.PAYLOAD, time_zone="UTC", user_email="me@acme.test")
        await adapter.execute(self.PAYLOAD, self._ctx(provider_config={}))

        shown = next(f["value"] for f in preview["fields"] if f["label"] == "From")
        assert shown == sendgrid_stub[0]["json"]["from"]["email"] == "env@acme.test"
