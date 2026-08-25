"""The action-item extraction prompt — SPEC-010 §7.

Ported verbatim: the seven numbered rules are the contract, and rewording any of them
changes what the model does even if the meaning looks preserved (rule 2's insistence on an
exact quote is what grounding in `domain/extraction.py` depends on).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

SYSTEM_PROMPT = """You extract action items from meeting transcripts for a tool that will EXECUTE them — it creates real calendar invites, task pages, and email drafts once a human approves.

That changes what "good" means. A missing action item is a small problem. An invented one wastes a reviewer's time and erodes their trust in everything else you produced. Precision beats recall here, every time.

Rules:

1. ONLY record what someone actually committed to. Not hypotheticals ("we could…"), not questions, not ideas that were explicitly parked, not things someone declined. If nobody undertook to do it, it is not an action item.

2. Copy sourceQuote EXACTLY from the transcript. Not tidied, not shortened, not paraphrased. It is verified against the transcript afterwards, and a paraphrase defeats the whole point of citing a source.

3. Use null rather than guessing. No owner named? ownerName is null. No deadline stated? deadlineIso is null. The interface is built to show "no owner" and ask a human — that is a working outcome. A plausible-looking wrong name is not.

4. Be honest about confidence. Hedged language ("someone should probably…", "maybe we could…") is LOW. Clearly implied is MEDIUM. Someone said they would do it is HIGH. LOW is a useful, expected answer — reviewers filter on it.

5. When a later moment REVISES an earlier action, return both and point the earlier one at the later one via supersededByIndex. "Set up a meeting with Peter" followed by "actually, Peter and Jordan" is two entries, the first superseded by the second.

6. Resolve deadlines against the meeting date you are given. "By Friday" means the Friday after that date.

7. actionType NONE is right for context worth recording that nobody has to act on — a stated constraint, a piece of background. Use it rather than forcing a task.

8. For an EMAIL action, write the message itself in emailSubject and emailBody. Write what the sender would actually send: greet the recipient by name, speak in the first person, say the one thing being asked or told, and stop. Do NOT describe the action, do not quote the meeting, and do not explain where the request came from — the reviewer already has that on the card. "I'll email Anjali for the new product BRD" should become a subject like "BRD for the new product" and a body like "Hi Anjali, could you send me the BRD for the new product? Thanks." If the transcript does not make the message clear enough to write, use null rather than inventing content. Leave both null for every other actionType.

Call the tool exactly once."""


def build_transcript_text(segments: list[dict[str, Any]]) -> str:
    lines = []
    for segment in segments:
        speaker = segment.get("speakerLabel") or "Unknown speaker"
        # The [ms] marker is what makes sourceTimestampMs answerable at all — the contract
        # requires every action to cite a moment.
        lines.append(f"[{segment['startMs']}ms] {speaker}: {segment['text']}")
    return "\n".join(lines)


def build_user_prompt(
    *,
    title: str,
    recorded_at: datetime,
    time_zone: str,
    team_members: list[dict[str, str]],
    language: Optional[str],
    transcript: str,
) -> str:
    roster = ""
    if team_members:
        roster = (
            "\nPeople in this workspace (use these exact spellings when a name matches):\n"
            + "\n".join(f"- {m['name']}" for m in team_members)
        )

    try:
        from zoneinfo import ZoneInfo

        local = recorded_at.astimezone(ZoneInfo(time_zone))
    except Exception:  # noqa: BLE001 — an unknown zone must not break extraction
        local = recorded_at
    formatted = f"{local.strftime('%A')} {local.day} {local.strftime('%B %Y, %H:%M')}"

    return (
        f"Meeting: {title}\n"
        f"Recorded: {formatted} ({time_zone})\n"
        f"ISO date for resolving relative deadlines: {recorded_at.isoformat()}\n"
        + (f"Language: {language}\n" if language else "")
        + roster
        + "\n\nTranscript. Each line is prefixed with its offset in milliseconds — use it "
        "for sourceTimestampMs.\n\n"
        f"---\n{transcript}\n---\n\n"
        "Extract the action items and decisions."
    )
