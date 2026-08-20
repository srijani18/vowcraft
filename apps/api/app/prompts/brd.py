"""The two BRD prompts — SPEC-014 §7, ported verbatim in intent.

Both state that the input is *speech*: a transcript of someone thinking aloud contains false
starts, self-corrections and filler, and a document that mirrors the delivery rather than
the intent reads as a transcript with headings.
"""

from __future__ import annotations

import json
from typing import Any

from app.domain.brd import highest_requirement_numbers

_SPEECH_NOTE = """
The input is a transcript of someone speaking, not written prose. Expect false starts,
repetition, filler, and self-correction. When the speaker corrects themselves, follow the
correction and ignore the abandoned version. Capture what they meant, not how they said it.
Never quote filler back into the document.
""".strip()

_INVENTION_RULE = """
Do not invent specifics. This is the single most important instruction.

You are being given a short spoken description. It cannot possibly specify an entire
system, and a document that reads as though it does is worse than useless — someone may
build from it. So:

- Do NOT invent stakeholders, names, teams, or organisations that were not mentioned.
- Do NOT invent numbers: no user counts, latency targets, uptime percentages, budgets or
  dates unless the speaker stated them.
- Do NOT invent compliance or regulatory requirements (GDPR, HIPAA, SOC 2, PCI) unless
  the speaker raised them.
- Do NOT pad a section to look complete. An empty array is a truthful answer.

Everything you would otherwise have had to guess goes in openQuestions instead, phrased
as a question you would ask the requester. A document with four requirements and ten open
questions is the correct output for one sentence of input. A document with twenty
confident requirements from that same sentence is a failure.
""".strip()

BRD_SYSTEM_PROMPT = f"""
You are a business analyst. You turn a spoken description of a need into a structured
business requirements document.

{_SPEECH_NOTE}

{_INVENTION_RULE}

Requirement ids: number functional requirements FR-01, FR-02, … and non-functional
NFR-01, NFR-02, … in the order you list them.

Priority: MUST is only for what the speaker made non-negotiable. If they did not signal
priority, use SHOULD. Do not mark everything MUST.

Write requirements as testable statements — "the system must let a user reset their own
password by email" rather than "password stuff". One capability per requirement.
""".strip()

BRD_REVISION_SYSTEM_PROMPT = f"""
You are a business analyst revising an existing business requirements document. The user
has spoken additional requirements. You will return the COMPLETE revised document.

{_SPEECH_NOTE}

{_INVENTION_RULE}

How to revise — this is an amendment, not a rewrite:

1. PRESERVE. Every existing requirement keeps its id and its wording, unless the new
   input directly contradicts or extends it. Do not reword requirements for style. Do not
   drop a requirement because the new input did not mention it — the user is adding to
   the document, not replacing it. Omitting an existing requirement is an error.
2. APPEND. New requirements continue the existing numbering. Never reuse an id, and never
   renumber an existing one: FR-03 must refer to the same requirement it referred to
   before.
3. AMEND. When the new input changes an existing requirement, edit that requirement in
   place, keeping its id, and say so in changeSummary.
4. RESOLVE. If the new input answers an existing open question, remove that question and
   fold the answer into the appropriate section.
5. REPORT. changeSummary states specifically what you did: ids added, ids amended and
   how, questions resolved. "Updated the document" is not an acceptable summary.
""".strip()


def build_initial_prompt(spoken_text: str) -> str:
    return "\n".join(
        [
            "Here is what the requester said. Write the business requirements document from it.",
            "",
            "--- TRANSCRIPT ---",
            spoken_text.strip(),
            "--- END TRANSCRIPT ---",
        ]
    )


def build_revision_prompt(current: dict[str, Any], spoken_text: str) -> str:
    """The current document as JSON, plus the new speech.

    Structured JSON rather than rendered markdown, so the model amends the same shape it
    must return — and the numbering ceiling is stated explicitly, because asking it to infer
    "the next id" from a long list is a needless way to get a duplicate.
    """
    fr, nfr = highest_requirement_numbers(current)
    return "\n".join(
        [
            "Here is the current document.",
            "",
            "--- CURRENT DOCUMENT (JSON) ---",
            json.dumps(current, indent=2),
            "--- END CURRENT DOCUMENT ---",
            "",
            "The requester has now said the following. Revise the document to incorporate it.",
            "",
            "--- NEW REQUIREMENTS (SPOKEN) ---",
            spoken_text.strip(),
            "--- END NEW REQUIREMENTS ---",
            "",
            f"Continue functional requirement ids from FR-{fr + 1:02d} and non-functional from "
            f"NFR-{nfr + 1:02d}. Every id at or below FR-{fr:02d} / NFR-{nfr:02d} already exists "
            "and must keep its current meaning.",
        ]
    )


_STRING_ARRAY = lambda description: {  # noqa: E731
    "type": "array",
    "description": description,
    "items": {"type": "string"},
}

_BRD_PROPERTIES: dict[str, Any] = {
    "title": {
        "type": "string",
        "description": (
            "A specific title drawn from what was said — the system or capability being "
            'described. Never "Business Requirements Document" or "Untitled".'
        ),
    },
    "executiveSummary": {
        "type": "string",
        "description": "Two to four sentences: what is being built and why it matters.",
    },
    "objectives": _STRING_ARRAY("What success looks like. Outcomes, not features."),
    "scope": {
        "type": "object",
        "description": "What this covers and, equally, what it does not.",
        "properties": {
            "inScope": _STRING_ARRAY("Included in this effort."),
            "outOfScope": _STRING_ARRAY(
                "Explicitly excluded. Include anything the speaker ruled out, and anything a "
                "reader would otherwise assume was included. Empty is acceptable if genuinely "
                "unknown."
            ),
        },
        "required": ["inScope", "outOfScope"],
    },
    "stakeholders": {
        "type": "array",
        "description": "Roles with a stake in this. Roles only — do not invent people or names.",
        "items": {
            "type": "object",
            "properties": {
                "role": {"type": "string"},
                "interest": {"type": "string"},
            },
            "required": ["role", "interest"],
        },
    },
    "functionalRequirements": {
        "type": "array",
        "description": "What the system must do. Each gets a stable id.",
        "items": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": (
                        "Sequential, formatted FR-01, FR-02, … When revising, KEEP each existing "
                        "id attached to its existing requirement and continue the sequence."
                    ),
                },
                "requirement": {"type": "string"},
                "priority": {"type": "string", "enum": ["MUST", "SHOULD", "COULD"]},
                "rationale": {
                    "type": ["string", "null"],
                    "description": "Why, if the speaker said why. Null rather than a guess.",
                },
            },
            "required": ["id", "requirement", "priority", "rationale"],
        },
    },
    "nonFunctionalRequirements": {
        "type": "array",
        "description": (
            "Qualities rather than features: performance, security, accessibility, "
            "availability, compliance. Only those actually implied by what was said."
        ),
        "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "category": {"type": "string"},
                "requirement": {"type": "string"},
            },
            "required": ["id", "category", "requirement"],
        },
    },
    "assumptions": _STRING_ARRAY(
        "What is being taken as true and would change the design if false."
    ),
    "risks": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "risk": {"type": "string"},
                "mitigation": {
                    "type": ["string", "null"],
                    "description": "Only if stated or self-evident. Null rather than invented.",
                },
            },
            "required": ["risk", "mitigation"],
        },
    },
    "openQuestions": _STRING_ARRAY(
        "What you would need to ask before this document could be built from. THIS IS "
        "REQUIRED TO BE USEFUL: a short spoken requirement cannot specify a system, so "
        "anything you would otherwise have to invent belongs here as a question instead. A "
        "long list here is a good answer, not a failure."
    ),
}

_BRD_REQUIRED = [
    "title", "executiveSummary", "objectives", "scope", "stakeholders",
    "functionalRequirements", "nonFunctionalRequirements", "assumptions", "risks",
    "openQuestions",
]

BRD_TOOL: dict[str, Any] = {
    "name": "write_business_requirements",
    "description": (
        "Record the business requirements described in the transcript, as a structured "
        "document. Call this exactly once."
    ),
    "input_schema": {"type": "object", "properties": _BRD_PROPERTIES, "required": _BRD_REQUIRED},
}

BRD_REVISION_TOOL: dict[str, Any] = {
    "name": "revise_business_requirements",
    "description": (
        "Return the COMPLETE revised document, incorporating the new requirements, plus a "
        "summary of what you changed. Call this exactly once."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "document": {
                "type": "object",
                "description": (
                    "The entire document after your revision — not a diff, and not only the "
                    "changed sections. Omitting an existing requirement is an error."
                ),
                "properties": _BRD_PROPERTIES,
                "required": _BRD_REQUIRED,
            },
            "changeSummary": {
                "type": "string",
                "description": (
                    "What you changed, specifically: which ids you added, which you amended and "
                    "how, and which open questions the new input resolved."
                ),
            },
        },
        "required": ["document", "changeSummary"],
    },
}
