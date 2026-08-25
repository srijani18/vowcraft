"""Attachment resolution — SPEC-002 §5.1.

The ownership tests are the reason this file exists. An `attachments` entry is a document id
carried in an *editable* payload, so it is untrusted input on the way to a read. If that
check is wrong, an outbound email carries another account's requirements document — a
disclosure no downstream guardrail can catch, because a guardrail sees a perfectly
well-formed payload either way.

Everything else here is about failing loudly. A silently dropped attachment means an email
that went out without the thing it was about, and the reviewer was told it succeeded.
"""

from __future__ import annotations

import pytest
from vowcraft_db import BrdDocument

from app.core.exceptions import AppError
from app.domain.brd import validate_brd
from app.services.attachments import resolve_attachments

#: Built through the real validator rather than hand-written, so the fixture cannot drift
#: from the shape actually stored. It matters here: `brd_to_markdown` indexes its keys
#: directly (`document["executiveSummary"]`), which is safe only because `validate_brd`
#: guarantees every key exists — a hand-rolled partial dict would test a shape the
#: application never produces.
CONTENT = validate_brd(
    {
        "title": "Payments service",
        "executiveSummary": "Take card payments on the web checkout.",
        "scope": {"inScope": ["Card payments"], "outOfScope": ["Refunds"]},
        "functionalRequirements": [
            {"id": "FR-01", "requirement": "Accept a card payment", "priority": "MUST"}
        ],
        "openQuestions": ["Which payment processor?"],
    }
)


@pytest.fixture
def make_brd(db_session):
    async def _make(user, *, title: str = "Payments service", content=CONTENT) -> BrdDocument:
        row = BrdDocument(user_id=user.id, title=title, content=content, status="READY")
        db_session.add(row)
        await db_session.flush()
        return row

    return _make


def _payload(*ids: str) -> dict:
    return {"attachments": [{"kind": "brd", "documentId": i} for i in ids]}


class TestOwnership:
    async def test_another_users_document_cannot_be_attached(
        self, make_user, make_brd, db_session
    ):
        """The whole point. A guessed or forged id must not read across accounts."""
        mine = await make_user(email="mine@acme.test")
        theirs = await make_user(email="theirs@acme.test")
        their_doc = await make_brd(theirs, title="Their secret plans")

        with pytest.raises(AppError) as excinfo:
            await resolve_attachments(db_session, mine.id, _payload(their_doc.id))

        assert excinfo.value.code == "attachment_not_found"

    async def test_the_error_does_not_reveal_that_the_document_exists(
        self, make_user, make_brd, db_session
    ):
        """"Not found" and "not yours" are deliberately the same answer — the difference is
        only useful to someone probing for ids."""
        mine = await make_user(email="mine@acme.test")
        theirs = await make_user(email="theirs@acme.test")
        their_doc = await make_brd(theirs, title="Their secret plans")

        with pytest.raises(AppError) as theirs_error:
            await resolve_attachments(db_session, mine.id, _payload(their_doc.id))
        with pytest.raises(AppError) as missing_error:
            await resolve_attachments(db_session, mine.id, _payload("brd_does_not_exist"))

        assert theirs_error.value.code == missing_error.value.code
        assert theirs_error.value.message == missing_error.value.message
        assert "Their secret plans" not in theirs_error.value.message


class TestResolving:
    async def test_a_document_resolves_to_markdown_bytes(
        self, make_user, make_brd, db_session
    ):
        user = await make_user()
        doc = await make_brd(user)

        resolved = await resolve_attachments(db_session, user.id, _payload(doc.id))

        assert len(resolved) == 1
        assert resolved[0].mime_type == "text/markdown"
        assert resolved[0].content, "no bytes produced"
        # Rendered, not stored — the same renderer the export route uses.
        assert b"Accept a card payment" in resolved[0].content

    async def test_the_filename_is_derived_from_the_title(
        self, make_user, make_brd, db_session
    ):
        user = await make_user()
        doc = await make_brd(user, title="Payments Service v2")

        resolved = await resolve_attachments(db_session, user.id, _payload(doc.id))

        assert resolved[0].filename == "payments-service-v2.md"

    async def test_several_documents_resolve_in_order(
        self, make_user, make_brd, db_session
    ):
        user = await make_user()
        first = await make_brd(user, title="First doc")
        second = await make_brd(user, title="Second doc")

        resolved = await resolve_attachments(db_session, user.id, _payload(first.id, second.id))

        assert [a.filename for a in resolved] == ["first-doc.md", "second-doc.md"]


class TestNothingToDo:
    async def test_no_attachments_key_resolves_to_nothing(self, make_user, db_session):
        user = await make_user()
        assert await resolve_attachments(db_session, user.id, {}) == []

    async def test_a_non_list_attachments_value_is_ignored_rather_than_crashing(
        self, make_user, db_session
    ):
        """Payloads are editable, so a wrong-typed value is reachable input."""
        user = await make_user()
        assert await resolve_attachments(db_session, user.id, {"attachments": "nope"}) == []


class TestFailingLoudly:
    async def test_an_empty_document_is_refused_by_name(
        self, make_user, make_brd, db_session
    ):
        """A DRAFTING document that never produced content would otherwise attach a file
        containing nothing but headings."""
        user = await make_user()
        doc = await make_brd(user, title="Never generated", content=None)

        with pytest.raises(AppError) as excinfo:
            await resolve_attachments(db_session, user.id, _payload(doc.id))

        assert excinfo.value.code == "attachment_empty"
        # Safe to name: it is the user's own document.
        assert "Never generated" in excinfo.value.message

    async def test_an_unknown_kind_is_refused_rather_than_skipped(
        self, make_user, db_session
    ):
        """Skipping would send the email without the attachment and report success."""
        user = await make_user()

        with pytest.raises(AppError) as excinfo:
            await resolve_attachments(
                db_session, user.id, {"attachments": [{"kind": "spreadsheet", "documentId": "x"}]}
            )

        assert excinfo.value.code == "unknown_attachment_kind"

    async def test_a_missing_document_id_is_refused(self, make_user, db_session):
        user = await make_user()

        with pytest.raises(AppError) as excinfo:
            await resolve_attachments(
                db_session, user.id, {"attachments": [{"kind": "brd"}]}
            )

        assert excinfo.value.code == "invalid_attachment"

    async def test_too_many_attachments_is_refused_before_any_read(
        self, make_user, db_session
    ):
        user = await make_user()

        with pytest.raises(AppError) as excinfo:
            await resolve_attachments(db_session, user.id, _payload(*[f"id-{i}" for i in range(11)]))

        assert excinfo.value.code == "too_many_attachments"
