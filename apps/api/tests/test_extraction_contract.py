"""The action-item extraction contract — SPEC-010 §7.

Ported from the deleted ``tests/extraction-schema.test.ts`` now that
``src/server/extraction/schema.ts`` has no callers left (BRD generation, the last one, moved
to FastAPI — SPEC-015 §7). `validate_extraction` is deliberately *not* a strict
reject-the-whole-document validator the way `validate_brd` is: its own docstring says so —
"enough validation to be safe to persist, not a full schema engine" — so these tests check
its actual, documented behaviour (malformed items filtered out, unknown values defaulted),
not the original TypeScript zod schema's stricter all-or-nothing one.
"""

from __future__ import annotations

from app.domain.extraction import validate_extraction


def _action(**overrides):
    base = {
        "description": "Send the revised budget to Priya",
        "actionType": "TASK",
        "ownerName": "Marcus",
        "deadlineIso": None,
        "priority": "HIGH",
        "confidence": "HIGH",
        "sourceTimestampMs": 12_500,
        "sourceQuote": "On the budget — I'll get the revised numbers over to Priya by Friday.",
        "reasoning": "Committed directly.",
        "supersededByIndex": None,
    }
    base.update(overrides)
    return base


class TestExtractionContract:
    def test_a_well_formed_result_validates(self):
        result = validate_extraction({"summary": "A meeting happened.", "actions": [_action()], "decisions": []})
        assert result["actions"][0]["description"] == "Send the revised budget to Priya"

    def test_a_missing_or_too_short_description_drops_that_action_not_the_whole_result(self):
        incomplete = {**_action()}
        del incomplete["description"]
        result = validate_extraction({"summary": None, "actions": [incomplete], "decisions": []})
        assert result["actions"] == []

    def test_an_unknown_action_type_falls_back_to_none_rather_than_rejecting_it(self):
        bad = _action(actionType="SLACK_MESSAGE")
        result = validate_extraction({"summary": None, "actions": [bad], "decisions": []})
        assert result["actions"][0]["actionType"] == "NONE"

    def test_nulls_are_accepted_where_the_contract_says_they_may_be(self):
        sparse = _action(ownerName=None, deadlineIso=None, reasoning=None, supersededByIndex=None)
        result = validate_extraction({"summary": None, "actions": [sparse], "decisions": []})
        action = result["actions"][0]
        assert action["ownerName"] is None
        assert action["deadlineIso"] is None
        assert action["reasoning"] is None
        assert action["supersededByIndex"] is None

    def test_an_empty_extraction_is_valid_a_meeting_may_commit_to_nothing(self):
        result = validate_extraction({"summary": None, "actions": [], "decisions": []})
        assert result == {"summary": None, "actions": [], "decisions": []}

    def test_an_unknown_priority_defaults_to_medium(self):
        result = validate_extraction(
            {"summary": None, "actions": [_action(priority="URGENT")], "decisions": []}
        )
        assert result["actions"][0]["priority"] == "MEDIUM"

    def test_a_negative_timestamp_is_clamped_to_zero(self):
        result = validate_extraction(
            {"summary": None, "actions": [_action(sourceTimestampMs=-500)], "decisions": []}
        )
        assert result["actions"][0]["sourceTimestampMs"] == 0

    def test_a_decision_missing_its_statement_is_dropped(self):
        result = validate_extraction(
            {"summary": None, "actions": [], "decisions": [{"statement": "", "decidedBy": None,
             "sourceTimestampMs": 0, "sourceQuote": ""}]}
        )
        assert result["decisions"] == []

    def test_a_well_formed_decision_survives(self):
        result = validate_extraction(
            {"summary": None, "actions": [], "decisions": [{
                "statement": "We will ship on Friday.", "decidedBy": "Priya",
                "sourceTimestampMs": 4000, "sourceQuote": "Let's ship Friday.",
            }]}
        )
        assert result["decisions"][0]["statement"] == "We will ship on Friday."
