"""The BRD prompts — SPEC-014 §7.

A port of the assertions in the deleted `tests/brd.test.ts`: the schema cannot enforce
"do not invent a stakeholder" or "this is speech, not prose" — only the prompt can say so,
which makes the prompt's literal wording part of the contract, not an implementation detail
free to drift.
"""

from __future__ import annotations

from app.prompts.brd import (
    BRD_REVISION_SYSTEM_PROMPT,
    BRD_SYSTEM_PROMPT,
    build_revision_prompt,
)

_DOCUMENT = {
    "title": "Internal Expense Approval Tool",
    "executiveSummary": "A tool for submitting and approving expenses without email threads.",
    "objectives": ["Cut approval time"],
    "scope": {"inScope": ["Submission and approval"], "outOfScope": ["Payroll integration"]},
    "stakeholders": [{"role": "Finance approver", "interest": "Needs an audit trail"}],
    "functionalRequirements": [
        {"id": "FR-01", "requirement": "A user must submit an expense.", "priority": "MUST", "rationale": None},
        {"id": "FR-02", "requirement": "An approver may reject with a reason.", "priority": "SHOULD", "rationale": None},
    ],
    "nonFunctionalRequirements": [
        {"id": "NFR-01", "category": "Security", "requirement": "Only the approver may approve."}
    ],
    "assumptions": [],
    "risks": [],
    "openQuestions": [],
}


class TestBothPromptsForbidInvention:
    def test_both_forbid_invention_explicitly(self):
        for prompt in (BRD_SYSTEM_PROMPT, BRD_REVISION_SYSTEM_PROMPT):
            assert "do not invent" in prompt.lower()
            assert "openQuestions" in prompt

    def test_both_say_the_input_is_speech_not_prose(self):
        for prompt in (BRD_SYSTEM_PROMPT, BRD_REVISION_SYSTEM_PROMPT):
            assert "false starts" in prompt.lower()
            assert "correct" in prompt.lower()

    def test_the_initial_prompt_names_the_specifics_it_must_not_fabricate(self):
        for forbidden in ("GDPR", "stakeholders", "latency"):
            assert forbidden.lower() in BRD_SYSTEM_PROMPT.lower()


class TestRevisionPromptRules:
    def test_forbids_dropping_and_renumbering(self):
        flat = " ".join(BRD_REVISION_SYSTEM_PROMPT.split())
        assert "omitting an existing requirement is an error" in flat.lower()
        assert "never renumber an existing one" in flat.lower()
        assert "never reuse an id" in flat.lower()
        assert "COMPLETE revised document" in flat


class TestBuildRevisionPrompt:
    def test_states_where_to_continue_from_rather_than_leaving_it_inferred(self):
        prompt = build_revision_prompt(_DOCUMENT, "It also needs a monthly report.")
        assert "FR-03" in prompt
        assert "NFR-02" in prompt
        # And that everything at or below the ceiling already exists.
        assert "FR-02" in prompt
        assert "already exists" in prompt

    def test_hands_over_structured_json_not_rendered_prose(self):
        prompt = build_revision_prompt(_DOCUMENT, "More requirements.")
        assert '"functionalRequirements"' in prompt
        assert "CURRENT DOCUMENT (JSON)" in prompt
        # The new speech is clearly delimited from the document.
        assert "NEW REQUIREMENTS (SPOKEN)" in prompt
        assert "More requirements." in prompt
