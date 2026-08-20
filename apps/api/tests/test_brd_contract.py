"""The BRD contract, its markdown rendering, and requirement-id stability — SPEC-014."""

from __future__ import annotations

import pytest

from app.domain.brd import (
    BrdInvalid,
    brd_filename,
    brd_to_markdown,
    dropped_requirement_ids,
    highest_requirement_numbers,
    validate_brd,
)


def document(**overrides):
    base = {
        "title": "Internal Expense Approval Tool",
        "executiveSummary": "A tool for submitting and approving expenses without email threads.",
        "objectives": ["Cut approval time"],
        "scope": {"inScope": ["Submission and approval"], "outOfScope": ["Payroll integration"]},
        "stakeholders": [{"role": "Finance approver", "interest": "Needs an audit trail"}],
        "functionalRequirements": [
            {"id": "FR-01", "requirement": "A user must be able to submit an expense.",
             "priority": "MUST", "rationale": None},
            {"id": "FR-02", "requirement": "An approver must be able to reject with a reason.",
             "priority": "SHOULD", "rationale": "Stated."},
        ],
        "nonFunctionalRequirements": [
            {"id": "NFR-01", "category": "Security", "requirement": "Only the approver may approve."}
        ],
        "assumptions": ["Users already have accounts"],
        "risks": [{"risk": "Scope creep into payroll", "mitigation": "Explicitly out of scope"}],
        "openQuestions": ["What is the approval limit above which a second approver is needed?"],
    }
    base.update(overrides)
    return base


class TestContract:
    def test_a_well_formed_document_validates(self):
        assert validate_brd(document())["title"] == "Internal Expense Approval Tool"

    def test_openQuestions_is_required(self):
        """The honesty mechanism, not a nicety: it is where unknowns go instead of
        inventions (SPEC-014 §5.1)."""
        raw = document()
        del raw["openQuestions"]
        with pytest.raises(BrdInvalid) as exc:
            validate_brd(raw)
        assert any("openQuestions" in i for i in exc.value.issues)

    def test_an_empty_openQuestions_list_is_allowed(self):
        """So "nothing further to ask" is expressible — it is a claim, and the markdown
        renders it as one."""
        assert validate_brd(document(openQuestions=[]))["openQuestions"] == []

    def test_both_halves_of_scope_are_required(self):
        with pytest.raises(BrdInvalid):
            validate_brd(document(scope="not an object"))

    @pytest.mark.parametrize("bad_id", ["1", "FR1", "REQ-01", "fr-01", "", "FR-"])
    def test_a_malformed_requirement_id_is_rejected(self, bad_id):
        with pytest.raises(BrdInvalid) as exc:
            validate_brd(document(functionalRequirements=[
                {"id": bad_id, "requirement": "Something valid.", "priority": "MUST", "rationale": None}
            ]))
        assert any("should look like FR-01" in i for i in exc.value.issues)

    def test_an_unknown_priority_falls_back_to_SHOULD(self):
        """Not rejected: a wrong priority is a nuisance, and discarding the whole document
        over it loses the user's speech."""
        parsed = validate_brd(document(functionalRequirements=[
            {"id": "FR-01", "requirement": "A requirement.", "priority": "CRITICAL", "rationale": None}
        ]))
        assert parsed["functionalRequirements"][0]["priority"] == "SHOULD"

    def test_a_blank_rationale_becomes_null_not_an_empty_string(self):
        parsed = validate_brd(document(functionalRequirements=[
            {"id": "FR-01", "requirement": "A requirement.", "priority": "MUST", "rationale": "  "}
        ]))
        assert parsed["functionalRequirements"][0]["rationale"] is None


class TestBounds:
    """`schema.ts` bounds every field with `.min()`/`.max()`, and `safeParse` rejects the
    whole document on any single violation. A prior port of `validate_brd` enforced almost
    none of these — a provider response with, say, 200 functional requirements or a
    100,000-character objective would have been accepted and stored whole. Worse, the one
    bound it did enforce (an array-length cap) silently truncated rather than rejecting,
    which is exactly the undisclosed data loss this feature's own design forbids
    everywhere else. Every test here exists because that comparison found a real gap."""

    def test_a_title_over_the_limit_is_rejected_not_truncated(self):
        with pytest.raises(BrdInvalid) as exc:
            validate_brd(document(title="x" * 161))
        assert any("title" in i for i in exc.value.issues)

    def test_an_executive_summary_over_the_limit_is_rejected(self):
        with pytest.raises(BrdInvalid) as exc:
            validate_brd(document(executiveSummary="x" * 1201))
        assert any("executiveSummary" in i for i in exc.value.issues)

    def test_more_than_fifteen_objectives_is_rejected_not_truncated_to_fifteen(self):
        with pytest.raises(BrdInvalid) as exc:
            validate_brd(document(objectives=[f"Objective number {i}" for i in range(16)]))
        assert any("objectives" in i and "too many" in i for i in exc.value.issues)

    def test_a_single_objective_over_300_characters_is_rejected(self):
        with pytest.raises(BrdInvalid) as exc:
            validate_brd(document(objectives=["x" * 301]))
        assert any("objectives[0]" in i for i in exc.value.issues)

    def test_more_than_eighty_functional_requirements_is_rejected(self):
        many = [
            {"id": f"FR-{i:02d}", "requirement": "Something valid here.", "priority": "MUST", "rationale": None}
            for i in range(1, 82)
        ]
        with pytest.raises(BrdInvalid) as exc:
            validate_brd(document(functionalRequirements=many))
        assert any("functionalRequirements" in i and "too many" in i for i in exc.value.issues)

    def test_a_requirement_text_over_600_characters_is_rejected(self):
        with pytest.raises(BrdInvalid):
            validate_brd(document(functionalRequirements=[
                {"id": "FR-01", "requirement": "x" * 601, "priority": "MUST", "rationale": None}
            ]))

    def test_a_rationale_over_500_characters_is_rejected(self):
        with pytest.raises(BrdInvalid):
            validate_brd(document(functionalRequirements=[
                {"id": "FR-01", "requirement": "Fine.", "priority": "MUST", "rationale": "x" * 501}
            ]))

    def test_more_than_twenty_stakeholders_is_rejected(self):
        many = [{"role": f"Role {i}", "interest": "Needs something."} for i in range(21)]
        with pytest.raises(BrdInvalid) as exc:
            validate_brd(document(stakeholders=many))
        assert any("stakeholders" in i and "too many" in i for i in exc.value.issues)

    def test_a_stakeholder_interest_over_400_characters_is_rejected(self):
        with pytest.raises(BrdInvalid):
            validate_brd(document(stakeholders=[{"role": "Approver", "interest": "x" * 401}]))

    def test_more_than_twenty_risks_is_rejected(self):
        many = [{"risk": f"Risk {i} description here.", "mitigation": None} for i in range(21)]
        with pytest.raises(BrdInvalid) as exc:
            validate_brd(document(risks=many))
        assert any("risks" in i and "too many" in i for i in exc.value.issues)

    def test_a_mitigation_over_400_characters_is_rejected(self):
        with pytest.raises(BrdInvalid):
            validate_brd(document(risks=[{"risk": "Something risky.", "mitigation": "x" * 401}]))

    def test_open_questions_allow_up_to_400_characters_unlike_other_string_arrays(self):
        # openQuestions' item cap (400) differs from objectives/assumptions (300) in the
        # original — a shared default here would silently over- or under-accept one of them.
        assert validate_brd(document(openQuestions=["x" * 400]))["openQuestions"] == ["x" * 400]
        with pytest.raises(BrdInvalid):
            validate_brd(document(openQuestions=["x" * 401]))

    def test_more_than_twenty_five_open_questions_is_rejected(self):
        with pytest.raises(BrdInvalid) as exc:
            validate_brd(document(openQuestions=[f"Question {i}?" for i in range(26)]))
        assert any("openQuestions" in i and "too many" in i for i in exc.value.issues)


class TestIdStability:
    def test_reports_the_maximum_not_the_count(self):
        """A gap in the sequence must not cause a reused id."""
        numbers = highest_requirement_numbers(document(functionalRequirements=[
            {"id": "FR-01", "requirement": "One.", "priority": "MUST", "rationale": None},
            {"id": "FR-07", "requirement": "Seven.", "priority": "MUST", "rationale": None},
        ]))
        assert numbers[0] == 7

    def test_an_empty_document_starts_at_zero(self):
        assert highest_requirement_numbers(
            document(functionalRequirements=[], nonFunctionalRequirements=[])
        ) == (0, 0)

    def test_a_dropped_requirement_is_detected_in_either_family(self):
        before = validate_brd(document())
        after = validate_brd(document(
            functionalRequirements=[before["functionalRequirements"][0]],
            nonFunctionalRequirements=[],
        ))
        assert dropped_requirement_ids(before, after) == ["FR-02", "NFR-01"]

    def test_appending_drops_nothing(self):
        before = validate_brd(document())
        after = validate_brd(document(functionalRequirements=[
            *document()["functionalRequirements"],
            {"id": "FR-03", "requirement": "A new one.", "priority": "SHOULD", "rationale": None},
        ]))
        assert dropped_requirement_ids(before, after) == []

    def test_rewording_while_keeping_the_id_is_not_a_drop(self):
        before = validate_brd(document())
        after = validate_brd(document(functionalRequirements=[
            {"id": "FR-01", "requirement": "Reworded entirely.", "priority": "MUST", "rationale": None},
            document()["functionalRequirements"][1],
        ]))
        assert dropped_requirement_ids(before, after) == []


class TestMarkdown:
    def test_every_populated_section_appears(self):
        md = brd_to_markdown(validate_brd(document()))
        for heading in ("# Internal Expense Approval Tool", "## Executive summary", "## Objectives",
                        "## Scope", "## Stakeholders", "## Functional requirements",
                        "## Non-functional requirements", "## Assumptions", "## Risks",
                        "## Open questions"):
            assert heading in md, heading

    def test_requirements_render_with_their_ids(self):
        md = brd_to_markdown(validate_brd(document()))
        assert "| FR-01 |" in md and "| NFR-01 |" in md

    def test_an_absent_rationale_renders_as_a_dash_not_None(self):
        md = brd_to_markdown(validate_brd(document()))
        assert "None" not in md
        assert "| — |" in md

    def test_an_empty_section_is_omitted_rather_than_left_bare(self):
        md = brd_to_markdown(validate_brd(document(risks=[], assumptions=[], stakeholders=[])))
        assert "## Risks" not in md
        assert "## Assumptions" not in md
        assert "## Objectives" in md

    def test_open_questions_print_even_when_empty(self):
        """An empty list is a claim that nothing needs asking. A reader deciding whether to
        build from this needs to see the claim made, not infer it from a missing heading."""
        md = brd_to_markdown(validate_brd(document(openQuestions=[])))
        assert "## Open questions" in md
        assert "None recorded" in md and "not a guarantee" in md

    def test_out_of_scope_is_omitted_when_empty_but_in_scope_survives(self):
        md = brd_to_markdown(validate_brd(document(scope={"inScope": ["Just this"], "outOfScope": []})))
        assert "**In scope**" in md
        assert "**Out of scope**" not in md

    def test_no_run_of_three_newlines_survives(self):
        assert "\n\n\n" not in brd_to_markdown(validate_brd(document(risks=[], assumptions=[])))

    @pytest.mark.parametrize("title,expected", [
        ("Internal Expense Approval Tool", "internal-expense-approval-tool.md"),
        ("Payments / Refunds (v2)", "payments-refunds-v2.md"),
        ("!!!", "requirements.md"),
    ])
    def test_filenames_are_slugged_and_never_empty(self, title, expected):
        assert brd_filename(title, "md") == expected

    def test_a_very_long_title_is_bounded(self):
        assert len(brd_filename("x" * 200, "md")) <= 64
