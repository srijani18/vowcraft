"""The guardrail engine — SPEC-003 §3, §4.

Every case here was first verified to produce the *same* verdict as the TypeScript
implementation it was ported from, over a shared fixture corpus: same violations, same
order, same pass/fail. These tests are that comparison made permanent, so a future edit to
either side cannot silently diverge.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.domain.rules import RULES, evaluate_rules
from app.domain.rules.policy import POLICY_RULES
from app.domain.rules.scheduling import SCHEDULING_RULES
from app.domain.rules.validation import VALIDATION_RULES


def ids(evaluation) -> list[str]:
    return [f"{v.severity}:{v.rule_id}" for v in evaluation.violations]


def calendar_payload(**overrides) -> dict:
    payload = {
        "title": "Review",
        "startsAt": "2026-08-21T14:00:00.000Z",
        "durationMinutes": 30,
        "attendees": ["priya@acme.com"],
    }
    payload.update(overrides)
    return payload


class TestRegistry:
    def test_sixteen_rules_in_three_families(self):
        assert len(SCHEDULING_RULES) == 7
        # 4, not 5: VAL_OWNER_KNOWN was removed. It warned when an action's owner was not
        # on the `TeamMember` roster, but there has never been a UI to *populate* that
        # roster, so it fired on almost every real item and its remedy ("add them to the
        # roster") pointed at a surface that does not exist.
        assert len(VALIDATION_RULES) == 4
        assert len(POLICY_RULES) == 5
        assert len(RULES) == 16

    def test_every_rule_id_is_unique(self):
        rule_ids = [r.id for r in RULES]
        assert len(set(rule_ids)) == len(rule_ids)

    def test_no_rule_applies_to_NONE(self):
        """NONE means "not executable", so a guardrail on it has nothing to guard."""
        for rule in RULES:
            assert "NONE" not in rule.applies_to, rule.id

    def test_a_rule_that_throws_becomes_INFO_without_stopping_the_others(self, make_item, make_ctx):
        """An engine that died on one bad rule would take every other guardrail down."""

        def explode(_ctx):
            raise ValueError("deliberate")

        from app.domain.types import Rule

        broken = Rule("BROKEN", "Throws", "BLOCK", ("TASK",), explode)
        item = make_item("TASK", description="Do the thing", payload={"title": "T"})
        result = evaluate_rules(make_ctx(item), (broken, *VALIDATION_RULES))
        assert "INFO:BROKEN" in ids(result)
        # And it did not block: a rule that could not run must not decide the outcome.
        assert result.passes is True

    def test_violations_sort_BLOCK_then_WARN(self, make_item, make_ctx):
        item = make_item(
            "CALENDAR",
            description="Hold a review",
            owner_name="Nobody Here",
            payload=calendar_payload(startsAt="2026-08-19T14:00:00.000Z"),
        )
        result = evaluate_rules(make_ctx(item))
        severities = [v.severity for v in result.violations]
        assert severities == sorted(severities, key=lambda s: {"BLOCK": 0, "WARN": 1, "INFO": 2}[s])


class TestScheduling:
    def test_a_clean_calendar_item_passes(self, make_item, make_ctx):
        item = make_item("CALENDAR", owner_name="Priya Raman", owner_email="priya@acme.com",
                         payload=calendar_payload())
        result = evaluate_rules(make_ctx(item))
        assert result.passes, ids(result)

    def test_a_past_start_blocks(self, make_item, make_ctx):
        item = make_item("CALENDAR", owner_name="Priya Raman",
                         payload=calendar_payload(startsAt="2026-08-19T14:00:00.000Z"))
        assert "BLOCK:SCHED_PAST" in ids(evaluate_rules(make_ctx(item)))

    def test_a_weekend_blocks_unless_weekends_are_allowed(self, make_item, make_ctx, settings):
        item = make_item("CALENDAR", owner_name="Priya Raman",
                         payload=calendar_payload(startsAt="2026-08-22T14:00:00.000Z"))
        assert "BLOCK:SCHED_WEEKEND" in ids(evaluate_rules(make_ctx(item)))
        settings.allow_weekends = True
        assert "BLOCK:SCHED_WEEKEND" not in ids(evaluate_rules(make_ctx(item)))

    def test_outside_working_hours_blocks(self, make_item, make_ctx):
        item = make_item("CALENDAR", owner_name="Priya Raman",
                         payload=calendar_payload(startsAt="2026-08-21T20:00:00.000Z"))
        assert "BLOCK:SCHED_HOURS" in ids(evaluate_rules(make_ctx(item)))

    def test_malformed_working_hours_produce_no_opinion(self, make_item, make_ctx, settings):
        """Blocking on a value the user cannot see is worse than staying quiet."""
        settings.workday_start = "not a time"
        item = make_item("CALENDAR", owner_name="Priya Raman",
                         payload=calendar_payload(startsAt="2026-08-21T20:00:00.000Z"))
        assert "BLOCK:SCHED_HOURS" not in ids(evaluate_rules(make_ctx(item)))

    def test_over_the_duration_cap_blocks(self, make_item, make_ctx):
        item = make_item("CALENDAR", owner_name="Priya Raman",
                         payload=calendar_payload(startsAt="2026-08-21T09:30:00.000Z", durationMinutes=300))
        assert "BLOCK:SCHED_MAX_DURATION" in ids(evaluate_rules(make_ctx(item)))

    def test_overlapping_a_busy_block_blocks(self, make_item, make_ctx):
        item = make_item("CALENDAR", owner_name="Priya Raman",
                         payload=calendar_payload(startsAt="2026-08-21T10:30:00.000Z"))
        assert "BLOCK:SCHED_CONFLICT" in ids(evaluate_rules(make_ctx(item)))

    def test_back_to_back_is_not_an_overlap(self, make_item, make_ctx):
        """A meeting starting exactly when another ends does not clash."""
        item = make_item("CALENDAR", owner_name="Priya Raman",
                         payload=calendar_payload(startsAt="2026-08-21T11:00:00.000Z"))
        assert "BLOCK:SCHED_CONFLICT" not in ids(evaluate_rules(make_ctx(item)))

    def test_protected_time_reports_DND_not_CONFLICT(self, make_item, make_ctx):
        """Two messages for one overlap would be noise; DND is the more specific one."""
        item = make_item("CALENDAR", owner_name="Priya Raman",
                         payload=calendar_payload(startsAt="2026-08-21T12:15:00.000Z"))
        got = ids(evaluate_rules(make_ctx(item)))
        assert "BLOCK:SCHED_DND" in got
        assert "BLOCK:SCHED_CONFLICT" not in got

    def test_a_tight_gap_warns_rather_than_blocks(self, make_item, make_ctx):
        item = make_item("CALENDAR", owner_name="Priya Raman",
                         payload=calendar_payload(startsAt="2026-08-21T11:05:00.000Z"))
        result = evaluate_rules(make_ctx(item))
        assert "WARN:SCHED_BUFFER" in ids(result)
        # A bad idea, not an impossibility.
        assert result.passes is True

    def test_a_reminder_in_the_past_blocks(self, make_item, make_ctx):
        item = make_item("REMINDER", description="Remind me",
                         payload={"message": "M", "remindAt": "2026-08-19T09:00:00.000Z", "channel": "self"})
        assert "BLOCK:SCHED_PAST" in ids(evaluate_rules(make_ctx(item)))


class TestValidation:
    def test_missing_required_fields_block_and_are_named(self, make_item, make_ctx):
        item = make_item("CALENDAR", payload={})
        result = evaluate_rules(make_ctx(item))
        assert "BLOCK:VAL_REQUIRED_FIELDS" in ids(result)
        message = next(v.message for v in result.violations if v.rule_id == "VAL_REQUIRED_FIELDS")
        # Labels, not keys: the reviewer sees "Event title", not "title".
        assert "Event title" in message

    def test_a_malformed_address_blocks(self, make_item, make_ctx):
        item = make_item("CALENDAR", payload=calendar_payload(attendees=["not-an-email"]))
        assert "BLOCK:VAL_EMAIL_FORMAT" in ids(evaluate_rules(make_ctx(item)))

    def test_a_past_due_date_blocks(self, make_item, make_ctx):
        item = make_item("TASK", description="Do the thing",
                         payload={"title": "Do it", "dueAt": "2026-08-19T09:00:00.000Z"})
        assert "BLOCK:VAL_DEADLINE_PAST" in ids(evaluate_rules(make_ctx(item)))

    def test_over_budget_blocks_until_a_manager_signs_off(self, make_item, make_ctx):
        over = make_item("TASK", description="Approve the $250,000 purchase", payload={"title": "Buy"})
        assert "BLOCK:VAL_BUDGET_APPROVAL" in ids(evaluate_rules(make_ctx(over)))
        approved = make_item("TASK", description="Approve the $250,000 purchase",
                             payload={"title": "Buy", "managerApproved": True})
        assert "BLOCK:VAL_BUDGET_APPROVAL" not in ids(evaluate_rules(make_ctx(approved)))


class TestPolicy:
    def test_an_external_send_needs_explicit_approval(self, make_item, make_ctx):
        payload = {"to": ["out@other.com"], "subject": "S", "body": "B", "sendMode": "send"}
        item = make_item("EMAIL", description="Send the summary", payload=payload)
        assert "BLOCK:POL_EXTERNAL_EMAIL" in ids(evaluate_rules(make_ctx(item)))
        assert "BLOCK:POL_EXTERNAL_EMAIL" not in ids(evaluate_rules(make_ctx(item, approved=True)))

    def test_a_draft_to_an_external_address_is_fine(self, make_item, make_ctx):
        """A draft reaches nobody, so the rule has nothing to say about it."""
        item = make_item("EMAIL", description="Send the summary",
                         payload={"to": ["out@other.com"], "subject": "S", "body": "B", "sendMode": "draft"})
        assert "BLOCK:POL_EXTERNAL_EMAIL" not in ids(evaluate_rules(make_ctx(item)))

    def test_a_subdomain_of_an_org_domain_is_internal(self, make_item, make_ctx):
        item = make_item("EMAIL", description="Send the summary",
                         payload={"to": ["y@mail.acme.com"], "subject": "S", "body": "B", "sendMode": "send"})
        assert "BLOCK:POL_EXTERNAL_EMAIL" not in ids(evaluate_rules(make_ctx(item)))

    def test_financial_wording_blocks_until_approved(self, make_item, make_ctx):
        item = make_item("TASK", description="Pay the vendor invoice", payload={"title": "T"})
        assert "BLOCK:POL_NO_FINANCIAL_AUTOEXEC" in ids(evaluate_rules(make_ctx(item)))
        assert "BLOCK:POL_NO_FINANCIAL_AUTOEXEC" not in ids(evaluate_rules(make_ctx(item, approved=True)))

    def test_an_export_needs_recorded_consent(self, make_item, make_ctx):
        item = make_item("TASK", description="Export the customer list", payload={"title": "T"})
        assert "BLOCK:POL_EXPORT_CONSENT" in ids(evaluate_rules(make_ctx(item)))
        consented = make_item("TASK", description="Export the customer list",
                              payload={"title": "T", "exportConsent": True})
        assert "BLOCK:POL_EXPORT_CONSENT" not in ids(evaluate_rules(make_ctx(consented)))

    def test_a_superseded_item_blocks(self, make_item, make_ctx):
        item = make_item("TASK", description="Do the thing", payload={"title": "T"},
                         superseded_by_id="later-item")
        assert "BLOCK:POL_SUPERSEDED" in ids(evaluate_rules(make_ctx(item)))

    def test_a_negated_restatement_of_a_decision_warns(self, make_item, make_ctx):
        item = make_item("TASK", description="Do not ship the invoicing module after all",
                         payload={"title": "T"})
        result = evaluate_rules(make_ctx(item))
        assert "WARN:POL_CONTRADICTS_DECISION" in ids(result)
        # Shallow by design, so it warns rather than blocks — it will sometimes be wrong.
        assert result.passes is True

    def test_agreement_with_a_decision_does_not_warn(self, make_item, make_ctx):
        item = make_item("TASK", description="Ship the invoicing module in October as agreed",
                         payload={"title": "T"})
        assert "WARN:POL_CONTRADICTS_DECISION" not in ids(evaluate_rules(make_ctx(item)))


class TestNoneType:
    def test_an_informational_item_has_no_guardrails_to_fail(self, make_item, make_ctx):
        item = make_item("NONE", description="Just context", payload={})
        result = evaluate_rules(make_ctx(item))
        assert result.violations == []
        assert result.passes is True
