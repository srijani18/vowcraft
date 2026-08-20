"""Risk classification, the approval gate, readiness, and the state machine.

Verified against the TypeScript implementation before being written down here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.action_item import (
    SERVER_ONLY_STATUSES,
    can_transition,
    compute_readiness,
    deadline_bucket,
    review_sort_key,
    transition_error,
)
from app.domain.password_rules import dominates, is_common_password, validate_password
from app.domain.payload import missing_fields, to_email_list
from app.domain.risk import classify_risk, effective_gate, gate_satisfied, largest_amount
from app.domain.types import RuleViolation, SettingsView


class TestLargestAmount:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("costs $500", 500),
            ("costs £2,500.50", 2500.50),
            ("about $5k", 5_000),
            ("roughly $2 million", 2_000_000),
            ("USD 1,200", 1200),
            ("₹75,000 budget", 75_000),
            ("no money here", None),
        ],
    )
    def test_reads_amounts_with_scale(self, text, expected):
        assert largest_amount(text) == expected

    def test_takes_the_largest_not_the_first(self):
        """"Reduce the £200 fee on the £2,000,000 contract" is a two-million-pound
        conversation; the first match would classify it as trivial."""
        assert largest_amount("reduce the £200 fee on the £2,000,000 contract") == 2_000_000


class TestRiskEscalatesOnly:
    def test_a_draft_is_low_however_alarming_the_recipients(self, make_item, settings):
        item = make_item("EMAIL", description="Send the report")
        payload = {"sendMode": "draft", "to": ["a@out.com", "b@out.com"]}
        assert classify_risk(item, payload, settings, "me@acme.com").tier == "LOW"

    def test_sending_externally_is_high(self, make_item, settings):
        item = make_item("EMAIL", description="Send the report")
        assessment = classify_risk(item, {"sendMode": "send", "to": ["x@outside.com"]}, settings, "me@acme.com")
        assert assessment.tier == "HIGH"
        assert any("outside the organisation" in f for f in assessment.factors)

    def test_sending_internally_is_medium(self, make_item, settings):
        item = make_item("EMAIL", description="Send the report")
        assert classify_risk(item, {"sendMode": "send", "to": ["y@acme.com"]}, settings, "me@acme.com").tier == "MEDIUM"

    def test_a_subdomain_counts_as_internal(self, make_item, settings):
        item = make_item("EMAIL", description="Send the report")
        assert classify_risk(item, {"sendMode": "send", "to": ["y@mail.acme.com"]}, settings, "me@acme.com").tier == "MEDIUM"

    def test_a_calendar_hold_for_yourself_is_low(self, make_item, settings):
        item = make_item("CALENDAR", description="Block focus time")
        assert classify_risk(item, {"attendees": ["me@acme.com"]}, settings, "me@acme.com").tier == "LOW"

    def test_inviting_others_is_medium(self, make_item, settings):
        item = make_item("CALENDAR", description="Hold a review")
        assert classify_risk(item, {"attendees": ["priya@acme.com"]}, settings, "me@acme.com").tier == "MEDIUM"

    def test_destructive_wording_is_high(self, make_item, settings):
        item = make_item("TASK", description="Delete the vendor account")
        assessment = classify_risk(item, {}, settings, None)
        assert assessment.tier == "HIGH"
        assert any("destructive" in f for f in assessment.factors)

    def test_over_the_budget_limit_is_high(self, make_item, settings):
        item = make_item("TASK", description="Approve the $250,000 contract")
        assert classify_risk(item, {}, settings, None).tier == "HIGH"

    def test_an_amount_is_read_from_the_quote_too(self, make_item, settings):
        """The figure is usually said aloud and never reaches a structured field."""
        item = make_item("TASK", description="Approve it", source_quote="we agreed on $80,000")
        assert classify_risk(item, {}, settings, None).tier == "HIGH"

    def test_a_later_low_factor_cannot_lower_an_earlier_high_one(self, make_item, settings):
        """Escalation only: no ordering of checks may downgrade something dangerous."""
        item = make_item("EMAIL", description="Delete the account and email $900,000 details")
        assert classify_risk(item, {"sendMode": "draft"}, settings, None).tier == "HIGH"


class TestApprovalGate:
    def test_high_risk_demands_confirmation(self, settings):
        assert effective_gate("HIGH", "HIGH", settings, "EMAIL").gate == "EXPLICIT_APPROVAL_WITH_CONFIRMATION"

    def test_medium_risk_demands_approval(self, settings):
        assert effective_gate("MEDIUM", "HIGH", settings, "EMAIL").gate == "EXPLICIT_APPROVAL"

    def test_low_risk_can_auto_execute_only_when_enabled_and_confident(self, settings):
        assert effective_gate("LOW", "HIGH", settings, "TASK").gate == "EXPLICIT_APPROVAL"
        settings.auto_execute_low_risk = True
        assert effective_gate("LOW", "HIGH", settings, "TASK").gate == "AUTO"
        # Confidence still matters: an unsure extraction is never automatic.
        assert effective_gate("LOW", "LOW", settings, "TASK").gate == "EXPLICIT_APPROVAL"

    def test_low_confidence_carries_a_note_pointing_at_the_quote(self, settings):
        decision = effective_gate("LOW", "LOW", settings, "TASK")
        assert decision.note and "source quote" in decision.note

    def test_a_configured_threshold_can_tighten(self, settings):
        settings.approval_thresholds = {"TASK": "EXPLICIT_APPROVAL_WITH_CONFIRMATION"}
        assert effective_gate("LOW", "HIGH", settings, "TASK").gate == "EXPLICIT_APPROVAL_WITH_CONFIRMATION"

    def test_a_configured_threshold_can_never_loosen(self, settings):
        """A configuration mistake must not be able to open a hole."""
        settings.approval_thresholds = {"EMAIL": "AUTO"}
        assert effective_gate("HIGH", "HIGH", settings, "EMAIL").gate == "EXPLICIT_APPROVAL_WITH_CONFIRMATION"

    def test_gate_satisfaction_requires_what_it_says(self):
        assert gate_satisfied("AUTO", approved=False, confirmed=False) is True
        assert gate_satisfied("EXPLICIT_APPROVAL", approved=True, confirmed=False) is True
        assert gate_satisfied("EXPLICIT_APPROVAL", approved=False, confirmed=False) is False
        # A client that forgets the confirmation step must fail here, server-side.
        assert gate_satisfied("EXPLICIT_APPROVAL_WITH_CONFIRMATION", approved=True, confirmed=False) is False
        assert gate_satisfied("EXPLICIT_APPROVAL_WITH_CONFIRMATION", approved=True, confirmed=True) is True


class TestReadiness:
    def test_a_complete_item_is_ready(self, make_item):
        item = make_item("TASK", payload={"title": "Do it"}, owner_name="Alex Rivera")
        assert compute_readiness(item).readiness == "READY"

    def test_low_confidence_needs_clarification_even_with_a_complete_payload(self, make_item):
        # A regression pin: an earlier port of computeReadiness dropped this check
        # entirely, so a low-confidence extraction with a complete payload and an owner
        # was surfaced as READY (executable) rather than asking a human to confirm it.
        item = make_item(
            "TASK", payload={"title": "Do it"}, owner_name="Alex Rivera", confidence="LOW"
        )
        result = compute_readiness(item)
        assert result.readiness == "NEEDS_CLARIFICATION"
        assert "low confidence" in " ".join(result.reasons)

    def test_a_missing_owner_needs_clarification_even_with_a_complete_payload(self, make_item):
        # Same regression class as the confidence check above — dropped by the same
        # earlier port, so an unowned item with a complete payload was also READY.
        item = make_item("TASK", payload={"title": "Do it"}, owner_name=None)
        result = compute_readiness(item)
        assert result.readiness == "NEEDS_CLARIFICATION"
        assert "No owner identified." in result.reasons

    def test_a_missing_field_needs_clarification_and_says_which(self, make_item):
        item = make_item("CALENDAR", payload={})
        result = compute_readiness(item)
        assert result.readiness == "NEEDS_CLARIFICATION"
        assert "startsAt" in result.missing_fields
        assert any("Event title" in r for r in result.reasons)

    def test_a_blocking_violation_needs_clarification(self, make_item):
        item = make_item("TASK", payload={"title": "Do it"})
        violation = RuleViolation(rule_id="X", severity="BLOCK", message="Blocked for a reason.")
        result = compute_readiness(item, [violation])
        assert result.readiness == "NEEDS_CLARIFICATION"
        assert "Blocked for a reason." in result.reasons

    def test_a_warning_alone_does_not_block_readiness(self, make_item):
        item = make_item("TASK", payload={"title": "Do it"}, owner_name="Alex Rivera")
        violation = RuleViolation(rule_id="X", severity="WARN", message="Just a warning.")
        assert compute_readiness(item, [violation]).readiness == "READY"

    def test_a_NONE_item_is_informational(self, make_item):
        assert compute_readiness(make_item("NONE")).readiness == "INFORMATIONAL"

    def test_a_rejected_item_is_informational_whatever_its_payload(self, make_item):
        item = make_item("TASK", status="REJECTED", payload={})
        result = compute_readiness(item)
        assert result.readiness == "INFORMATIONAL"
        assert result.reasons == ["Rejected by a reviewer."]


class TestStateMachine:
    @pytest.mark.parametrize(
        "start,target,allowed",
        [
            ("PROPOSED", "APPROVED", True),
            ("PROPOSED", "REJECTED", True),
            ("PROPOSED", "DEFERRED", True),
            ("PROPOSED", "EXECUTED", False),
            ("APPROVED", "EXECUTING", True),
            ("EXECUTING", "EXECUTED", True),
            ("EXECUTING", "FAILED", True),
            ("FAILED", "EXECUTING", True),
            ("REJECTED", "PROPOSED", True),
            ("EXECUTED", "PROPOSED", False),
            ("EXECUTED", "REJECTED", False),
        ],
    )
    def test_transitions(self, start, target, allowed):
        assert can_transition(start, target) is allowed

    def test_executed_is_terminal_and_says_why(self):
        message = transition_error("EXECUTED", "REJECTED")
        assert message and "compensating action" in message

    def test_a_no_op_is_allowed_rather_than_an_error(self):
        """A client re-sending the current status is idempotent, not a mistake."""
        assert transition_error("APPROVED", "APPROVED") is None

    def test_an_illegal_transition_names_both_ends(self):
        message = transition_error("PROPOSED", "EXECUTED")
        assert message and "PROPOSED" in message and "EXECUTED" in message

    def test_execution_statuses_are_server_only(self):
        """A caller must not be able to mark something EXECUTED without execution."""
        assert set(SERVER_ONLY_STATUSES) == {"EXECUTING", "EXECUTED"}


class TestPasswordPolicy:
    """Verified verdict-for-verdict against the TypeScript policy, message text included."""

    @pytest.mark.parametrize("password", [
        "a memorable phrase you will recall",
        "a password that should survive",
        "my welcome home party plans",
        "the qwerty keyboard is fine",
        "correct horse battery staple",
    ])
    def test_good_passphrases_are_accepted(self, password):
        """Substring matching on a wordlist punishes exactly the people writing good
        passphrases. These all contain a listed word and must pass."""
        assert validate_password(password, email="you@example.com", name="Anjali Guha") is None

    @pytest.mark.parametrize("password,code", [
        ("password", "password_too_short"),
        ("Password1!", "password_too_short"),
        ("password12345", "password_common"),
        ("welcome20244", "password_common"),
        ("iloveyou12345", "password_common"),
        ("qwertyuiopqw", "password_common"),
        ("passwordpassword", "password_common"),
        ("aaaaaaaaaaaaaaaa", "password_repetitive"),
        ("short", "password_too_short"),
        ("", "password_required"),
    ])
    def test_weak_passwords_are_refused_with_a_stable_code(self, password, code):
        problem = validate_password(password)
        assert problem is not None and problem.code == code

    def test_a_password_that_is_essentially_the_email_is_refused(self):
        problem = validate_password("marcus1234567", email="marcus@example.com")
        assert problem is not None and problem.code == "password_contains_email"

    def test_a_short_local_part_that_is_also_a_word_does_not_false_positive(self):
        """The earlier rule refused this for anyone at you@ — and equally me@, dev@, ops@."""
        assert validate_password("a memorable phrase you will recall", email="you@example.com") is None

    def test_dominance_is_proportional_not_containment(self):
        assert dominates("marcus", "marcus2024") is True
        assert dominates("marcus", "marcus1234567") is True
        assert dominates("you", "amemorablephraseyouwillrecall") is False

    @pytest.mark.parametrize("password,common", [
        ("password", True), ("password123", True), ("123password", True),
        ("password!!!", True), ("passwordpassword", True),
        # Accepted: a trailing letter puts real material after the word, which is exactly
        # what the three-character padding threshold is there to distinguish.
        ("welcome2024x", False),
        ("a password that should survive", False), ("my welcome home party", False),
    ])
    def test_common_password_matching_is_whole_password(self, password, common):
        assert is_common_password(password) is common


class TestPayload:
    def test_required_fields_per_action_type(self):
        assert set(missing_fields("CALENDAR", {})) == {"title", "startsAt", "durationMinutes", "attendees"}
        assert set(missing_fields("EMAIL", {})) == {"to", "subject", "body"}
        assert missing_fields("NONE", {}) == []

    def test_an_empty_list_does_not_satisfy_a_min_items_field(self):
        assert "attendees" in missing_fields("CALENDAR", {"title": "T", "startsAt": "x", "durationMinutes": 30, "attendees": []})

    def test_whitespace_does_not_satisfy_a_string_field(self):
        assert "title" in missing_fields("TASK", {"title": "   "})

    @pytest.mark.parametrize("value,expected", [
        ("a@b.co", ["a@b.co"]),
        ("a@b.co, c@d.co", ["a@b.co", "c@d.co"]),
        ("a@b.co; c@d.co", ["a@b.co", "c@d.co"]),
        (["a@b.co"], ["a@b.co"]),
        (None, []),
        (42, []),
    ])
    def test_recipient_normalisation_accepts_every_shape_a_model_emits(self, value, expected):
        assert to_email_list(value) == expected


class TestDeadlineBucket:
    """A port of src/lib/time.ts::deadlineBucket — the boundaries are inclusive on the
    near side, matching the diff-in-milliseconds comparison in the TS source."""

    NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)

    def test_no_deadline_is_none(self):
        assert deadline_bucket(None, self.NOW) == "none"

    def test_in_the_past_is_overdue(self):
        assert deadline_bucket(self.NOW - timedelta(minutes=1), self.NOW) == "overdue"

    def test_within_a_day_is_today(self):
        assert deadline_bucket(self.NOW + timedelta(hours=23), self.NOW) == "today"
        assert deadline_bucket(self.NOW + timedelta(days=1), self.NOW) == "today"

    def test_within_a_week_is_week(self):
        assert deadline_bucket(self.NOW + timedelta(days=2), self.NOW) == "week"
        assert deadline_bucket(self.NOW + timedelta(days=7), self.NOW) == "week"

    def test_beyond_a_week_is_none(self):
        assert deadline_bucket(self.NOW + timedelta(days=8), self.NOW) == "none"

    def test_a_naive_datetime_is_treated_as_utc_not_server_local_time(self):
        # The database stores naive-but-UTC timestamps; treating a naive deadline as
        # local time would silently mis-bucket it depending on where the process runs.
        naive_deadline = (self.NOW + timedelta(hours=2)).replace(tzinfo=None)
        assert deadline_bucket(naive_deadline, self.NOW) == "today"


class TestReviewSortKey:
    """A port of src/domain/action-item.ts::compareForReview."""

    def test_orders_by_priority_first(self, make_item):
        high = make_item(priority="HIGH")
        low = make_item(priority="LOW")
        assert review_sort_key(high) < review_sort_key(low)

    def test_deadline_breaks_a_priority_tie_earliest_first(self, make_item):
        soon = make_item(priority="MEDIUM", deadline=datetime(2026, 8, 21, tzinfo=timezone.utc))
        later = make_item(priority="MEDIUM", deadline=datetime(2026, 8, 25, tzinfo=timezone.utc))
        assert review_sort_key(soon) < review_sort_key(later)

    def test_no_deadline_sorts_after_any_deadline(self, make_item):
        with_deadline = make_item(priority="MEDIUM", deadline=datetime(2099, 1, 1, tzinfo=timezone.utc))
        without_deadline = make_item(priority="MEDIUM", deadline=None)
        assert review_sort_key(with_deadline) < review_sort_key(without_deadline)

    def test_source_timestamp_breaks_a_remaining_tie(self, make_item):
        earlier = make_item(priority="MEDIUM", deadline=None, source_timestamp_ms=1000)
        later = make_item(priority="MEDIUM", deadline=None, source_timestamp_ms=5000)
        assert review_sort_key(earlier) < review_sort_key(later)
