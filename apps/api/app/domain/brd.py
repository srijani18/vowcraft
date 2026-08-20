"""The BRD contract and its markdown rendering — SPEC-014 §5, §8.

Pure: the tool schema, validation, and the markdown view. The model call and the database
writes live in ``services/brd.py``.

Requirement ids are declared by the model, not assigned here, because refinement has to be
able to *keep* them: ``FR-03`` must mean the same requirement in revision 5 as in revision 1
(SPEC-014 §6.1). Code that renumbered on write would silently break that.
"""

from __future__ import annotations

import re
from typing import Any, Optional

_ID_PATTERN = re.compile(r"^(FR|NFR)-\d{1,3}$")
PRIORITIES = ("MUST", "SHOULD", "COULD")


class BrdInvalid(ValueError):
    """The model returned something that does not match the contract."""

    def __init__(self, issues: list[str]) -> None:
        super().__init__("; ".join(issues[:8]))
        self.issues = issues


def _bounded(value: str, field: str, min_len: int, max_len: int, issues: list[str]) -> str:
    """Ports one `z.string().trim().min().max()` field. A violation is appended to
    `issues` rather than truncated or dropped — the original rejects the whole document
    on any bound violation (`safeParse` fails atomically), and silently trimming a
    provider's response to fit would be exactly the kind of undisclosed data loss this
    feature's own "never invent, never silently drop" design principle exists to prevent."""
    if len(value) < min_len:
        issues.append(f"{field}: too short (minimum {min_len} characters)")
    elif len(value) > max_len:
        issues.append(f"{field}: too long (maximum {max_len} characters)")
    return value


def _as_str_list(
    value: Any, field: str, issues: list[str], *, array_max: int, item_min: int = 3, item_max: int = 300
) -> list[str]:
    """Ports one `z.array(z.string().trim().min().max()).max()` field — every bound
    (array length and each item's length) is enforced by appending to `issues`, matching
    the original's all-or-nothing `safeParse`, never by truncating the array or the text."""
    if value is None:
        return []
    if not isinstance(value, list):
        issues.append(f"{field}: expected a list")
        return []
    if len(value) > array_max:
        issues.append(f"{field}: too many items (maximum {array_max})")
    out = []
    for i, v in enumerate(value):
        s = str(v).strip()
        if not s:
            continue
        _bounded(s, f"{field}[{i}]", item_min, item_max, issues)
        out.append(s)
    return out


def validate_brd(raw: Any) -> dict[str, Any]:
    """Validate and normalise a document.

    Hand-written rather than declarative because the failure *messages* are the point: a
    rejected document should say which field and why, so a provider swap that changes the
    shape is diagnosable rather than just broken.
    """
    issues: list[str] = []
    if not isinstance(raw, dict):
        raise BrdInvalid(["expected an object"])

    title = _bounded(str(raw.get("title") or "").strip(), "title", 3, 160, issues)
    summary = _bounded(
        str(raw.get("executiveSummary") or "").strip(), "executiveSummary", 10, 1200, issues
    )

    scope_raw = raw.get("scope")
    if not isinstance(scope_raw, dict):
        issues.append("scope: expected an object with inScope and outOfScope")
        scope_raw = {}
    # Both halves are required. Omitting out-of-scope is how scope creeps, so an empty list
    # is acceptable but a missing field is not.
    scope = {
        "inScope": _as_str_list(scope_raw.get("inScope"), "scope.inScope", issues, array_max=25),
        "outOfScope": _as_str_list(scope_raw.get("outOfScope"), "scope.outOfScope", issues, array_max=25),
    }

    functional_raw = raw.get("functionalRequirements") or []
    if len(functional_raw) > 80:
        issues.append("functionalRequirements: too many items (maximum 80)")
    functional: list[dict[str, Any]] = []
    for i, item in enumerate(functional_raw):
        if not isinstance(item, dict):
            issues.append(f"functionalRequirements[{i}]: expected an object")
            continue
        rid = str(item.get("id") or "").strip()
        if not _ID_PATTERN.match(rid):
            issues.append(f"functionalRequirements[{i}].id: '{rid}' should look like FR-01")
            continue
        priority = str(item.get("priority") or "SHOULD").upper()
        requirement = _bounded(
            str(item.get("requirement") or "").strip(), f"functionalRequirements[{i}].requirement", 3, 600, issues
        )
        rationale = (str(item["rationale"]).strip() or None) if item.get("rationale") else None
        if rationale is not None:
            _bounded(rationale, f"functionalRequirements[{i}].rationale", 0, 500, issues)
        functional.append(
            {
                "id": rid,
                "requirement": requirement,
                "priority": priority if priority in PRIORITIES else "SHOULD",
                # Null rather than a guess: the model is told to omit a rationale it was
                # not given.
                "rationale": rationale,
            }
        )

    non_functional_raw = raw.get("nonFunctionalRequirements") or []
    if len(non_functional_raw) > 40:
        issues.append("nonFunctionalRequirements: too many items (maximum 40)")
    non_functional: list[dict[str, Any]] = []
    for i, item in enumerate(non_functional_raw):
        if not isinstance(item, dict):
            issues.append(f"nonFunctionalRequirements[{i}]: expected an object")
            continue
        rid = str(item.get("id") or "").strip()
        if not _ID_PATTERN.match(rid):
            issues.append(f"nonFunctionalRequirements[{i}].id: '{rid}' should look like NFR-01")
            continue
        non_functional.append(
            {
                "id": rid,
                "category": _bounded(
                    str(item.get("category") or "").strip(),
                    f"nonFunctionalRequirements[{i}].category", 2, 60, issues,
                ),
                "requirement": _bounded(
                    str(item.get("requirement") or "").strip(),
                    f"nonFunctionalRequirements[{i}].requirement", 3, 600, issues,
                ),
            }
        )

    stakeholders_raw = raw.get("stakeholders") or []
    if len(stakeholders_raw) > 20:
        issues.append("stakeholders: too many items (maximum 20)")
    stakeholders = []
    for i, s in enumerate(stakeholders_raw):
        if not isinstance(s, dict):
            continue
        role = str(s.get("role") or "").strip()
        if not role:
            continue
        interest = str(s.get("interest") or "").strip()
        _bounded(role, f"stakeholders[{i}].role", 2, 120, issues)
        _bounded(interest, f"stakeholders[{i}].interest", 3, 400, issues)
        stakeholders.append({"role": role, "interest": interest})

    risks_raw = raw.get("risks") or []
    if len(risks_raw) > 20:
        issues.append("risks: too many items (maximum 20)")
    risks = []
    for i, r in enumerate(risks_raw):
        if not isinstance(r, dict):
            continue
        risk = str(r.get("risk") or "").strip()
        if not risk:
            continue
        _bounded(risk, f"risks[{i}].risk", 3, 400, issues)
        mitigation = (str(r["mitigation"]).strip() or None) if r.get("mitigation") else None
        if mitigation is not None:
            _bounded(mitigation, f"risks[{i}].mitigation", 0, 400, issues)
        risks.append({"risk": risk, "mitigation": mitigation})

    # `openQuestions` must be *present*. It is the honesty mechanism: a short spoken
    # requirement cannot specify a system, and this is where the model puts what it does
    # not know instead of inventing it (SPEC-014 §5.1). An empty list is a legitimate
    # answer; a missing field is not.
    if "openQuestions" not in raw:
        issues.append("openQuestions: required — it is where unknowns go instead of inventions")

    objectives = _as_str_list(raw.get("objectives"), "objectives", issues, array_max=15)
    assumptions = _as_str_list(raw.get("assumptions"), "assumptions", issues, array_max=20)
    open_questions = _as_str_list(
        raw.get("openQuestions"), "openQuestions", issues, array_max=25, item_max=400
    )

    if issues:
        raise BrdInvalid(issues)

    return {
        "title": title,
        "executiveSummary": summary,
        "objectives": objectives,
        "scope": scope,
        "stakeholders": stakeholders,
        "functionalRequirements": functional,
        "nonFunctionalRequirements": non_functional,
        "assumptions": assumptions,
        "risks": risks,
        "openQuestions": open_questions,
    }


def highest_requirement_numbers(document: dict[str, Any]) -> tuple[int, int]:
    """The highest existing id per family, so the prompt can state where to continue.

    The maximum, not the count: a gap in the sequence would otherwise cause a reused id.
    """
    def top(items: list[dict[str, Any]]) -> int:
        best = 0
        for item in items:
            parts = str(item.get("id", "")).split("-")
            if len(parts) == 2 and parts[1].isdigit():
                best = max(best, int(parts[1]))
        return best

    return top(document.get("functionalRequirements", [])), top(
        document.get("nonFunctionalRequirements", [])
    )


def dropped_requirement_ids(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Ids present before a revision and absent after — SPEC-014 §6.1.

    The prompt forbids dropping a requirement, but a prompt is not an enforcement mechanism,
    and losing a requirement the user already accepted is the worst outcome this feature has.
    """
    def ids(doc: dict[str, Any]) -> list[str]:
        return [r["id"] for r in doc.get("functionalRequirements", [])] + [
            r["id"] for r in doc.get("nonFunctionalRequirements", [])
        ]

    kept = set(ids(after))
    return [i for i in ids(before) if i not in kept]


def _bullets(items: list[str]) -> Optional[str]:
    return "\n".join(f"- {i}" for i in items) if items else None


def _section(heading: str, body: Optional[str]) -> list[str]:
    if not body or not body.strip():
        return []
    return [f"## {heading}", "", body.strip(), ""]


def brd_to_markdown(document: dict[str, Any], meta: Optional[dict[str, Any]] = None) -> str:
    """Markdown is generated, never stored — SPEC-014 §4.2.

    Empty sections are omitted rather than printed as a bare heading, with one deliberate
    exception: see open questions below.
    """
    lines: list[str] = [f"# {document['title']}", ""]

    if meta:
        parts = []
        if meta.get("generatedAt"):
            parts.append(f"Generated {meta['generatedAt']}")
        if meta.get("revisions"):
            n = meta["revisions"]
            parts.append(f"{n} revision{'' if n == 1 else 's'}")
        if parts:
            lines += [f"*{' · '.join(parts)}*", ""]

    lines += _section("Executive summary", document["executiveSummary"])
    lines += _section("Objectives", _bullets(document["objectives"]))

    scope_parts: list[str] = []
    if document["scope"]["inScope"]:
        scope_parts += ["**In scope**", "", _bullets(document["scope"]["inScope"]) or "", ""]
    if document["scope"]["outOfScope"]:
        scope_parts += ["**Out of scope**", "", _bullets(document["scope"]["outOfScope"]) or ""]
    lines += _section("Scope", "\n".join(scope_parts))

    if document["stakeholders"]:
        table = ["| Role | Interest |", "|---|---|"] + [
            f"| {s['role']} | {s['interest']} |" for s in document["stakeholders"]
        ]
        lines += _section("Stakeholders", "\n".join(table))

    if document["functionalRequirements"]:
        table = ["| ID | Requirement | Priority | Rationale |", "|---|---|---|---|"] + [
            f"| {r['id']} | {r['requirement']} | {r['priority']} | {r['rationale'] or '—'} |"
            for r in document["functionalRequirements"]
        ]
        lines += _section("Functional requirements", "\n".join(table))

    if document["nonFunctionalRequirements"]:
        table = ["| ID | Category | Requirement |", "|---|---|---|"] + [
            f"| {r['id']} | {r['category']} | {r['requirement']} |"
            for r in document["nonFunctionalRequirements"]
        ]
        lines += _section("Non-functional requirements", "\n".join(table))

    lines += _section("Assumptions", _bullets(document["assumptions"]))
    if document["risks"]:
        body = "\n".join(
            f"- **{r['risk']}**" + (f" — {r['mitigation']}" if r["mitigation"] else "")
            for r in document["risks"]
        )
        lines += _section("Risks", body)

    # Printed even when empty, unlike every other section. An empty list is a *claim* that
    # nothing further needs asking, and a reader deciding whether to build from this
    # document needs to see the claim made rather than infer it from a missing heading.
    lines += [
        "## Open questions",
        "",
        _bullets(document["openQuestions"])
        or "_None recorded. Treat that as a claim to verify, not a guarantee of completeness._",
        "",
    ]

    text = "\n".join(lines)
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.rstrip() + "\n"


def brd_filename(title: str, extension: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
    return f"{slug or 'requirements'}.{extension}"
