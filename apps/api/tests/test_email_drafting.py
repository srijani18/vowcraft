"""The drafted email body — SPEC-010 §7, rule 8.

``derive_payload`` used to template an EMAIL body out of the action's description and its
source quote, producing this for "I'll email Anjali for the new product BRD":

    Send an email to Anjali requesting her to send me the BRD of the new product.

    Context from the meeting:
    "I will send an email to Anjali requesting her to send me the BRD of the new product."

That is a *note about* an email, not an email — nobody could send it without rewriting it,
and it restated the same sentence twice. The model that just read the transcript is in the
best position to write the message, so the extraction tool now carries `emailSubject` and
`emailBody`, and the template survives only as a fallback.

Pure-unit: `derive_payload` takes no I/O.
"""

from __future__ import annotations

from app.domain.extraction import DerivationContext, derive_payload


def _context(**overrides) -> DerivationContext:
    base = dict(team_members=[], owner_email=None, self_email="me@acme.test")
    base.update(overrides)
    return DerivationContext(**base)


def _action(**overrides) -> dict:
    base = {
        "description": "Send an email to Anjali requesting the new product BRD",
        "actionType": "EMAIL",
        "ownerName": None,
        "deadlineIso": None,
        "priority": "MEDIUM",
        "confidence": "HIGH",
        "sourceTimestampMs": 0,
        "sourceQuote": "I will send an email to Anjali requesting the new product BRD.",
        "reasoning": None,
        "supersededByIndex": None,
        "emailSubject": None,
        "emailBody": None,
    }
    base.update(overrides)
    return base


class TestTheDraftedMessageIsUsed:
    def test_the_drafted_body_replaces_the_template_entirely(self):
        action = _action(
            emailSubject="BRD for the new product",
            emailBody="Hi Anjali, could you send me the BRD for the new product? Thanks.",
        )

        payload = derive_payload(action, None, _context())

        assert payload["body"] == "Hi Anjali, could you send me the BRD for the new product? Thanks."
        # The two hallmarks of the old template must be gone.
        assert "Context from the meeting" not in payload["body"]
        assert action["sourceQuote"] not in payload["body"]

    def test_the_drafted_subject_is_used_rather_than_the_description(self):
        """The description is written for a reviewer scanning a board ("Send an email to
        Anjali requesting…"); a subject line is written for the recipient."""
        action = _action(
            emailSubject="BRD for the new product",
            emailBody="Hi Anjali, could you send me the BRD?",
        )

        payload = derive_payload(action, None, _context())

        assert payload["subject"] == "BRD for the new product"

    def test_whitespace_only_drafts_are_treated_as_absent(self):
        """A provider returning "  " must not produce an empty email body."""
        action = _action(emailSubject="   ", emailBody="\n\t ")

        payload = derive_payload(action, None, _context())

        assert payload["subject"] == action["description"][:120]
        assert "Context from the meeting" in payload["body"]

    def test_a_long_drafted_subject_is_still_bounded(self):
        action = _action(emailSubject="S" * 400, emailBody="Hi.")

        payload = derive_payload(action, None, _context())

        assert len(payload["subject"]) == 120


class TestTheFallbackSurvives:
    def test_no_draft_falls_back_to_the_template(self):
        """A provider that ignores the new fields — or an older stored extraction — must
        still produce a usable payload rather than an empty body."""
        payload = derive_payload(_action(), None, _context())

        assert payload["subject"] == _action()["description"][:120]
        assert "Context from the meeting" in payload["body"]
        assert _action()["sourceQuote"] in payload["body"]


class TestUnchangedBehaviour:
    def test_an_extracted_email_is_always_a_draft(self):
        """Drafting is the LOW-risk end of the scale (SPEC-003 §3), and an extracted email
        is the last thing that should send itself. Adding a real body makes this *more*
        important, not less — the message is now plausible enough to send by accident."""
        action = _action(emailSubject="Subject", emailBody="Hi Anjali.")

        assert derive_payload(action, None, _context())["sendMode"] == "draft"

    def test_a_recipient_still_comes_from_the_roster(self):
        """The drafted body does not invent a recipient: `to` is resolved from the roster
        by name, exactly as before."""
        action = _action(
            description="Email Anjali the BRD",
            sourceQuote="I will email Anjali the BRD.",
            emailSubject="BRD", emailBody="Hi Anjali.",
        )
        context = _context(
            team_members=[{"name": "Anjali Pandey", "email": "anjali@acme.test"}]
        )

        payload = derive_payload(action, None, context)

        assert payload.get("to") == ["anjali@acme.test"]

    def test_no_roster_match_leaves_the_recipient_absent(self):
        """`to` is a required field, so this is what makes the item land in "needs
        clarification" rather than executing with nobody addressed."""
        action = _action(emailSubject="BRD", emailBody="Hi Anjali.")

        assert "to" not in derive_payload(action, None, _context())
