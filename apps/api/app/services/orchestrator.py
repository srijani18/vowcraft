"""The multi-step workflow orchestrator — SPEC-002 §8.

Composes `ExecutorService`, and adds no gate logic of its own: everything that makes a
single execution safe (the status gate, the dependency gate, the idempotent replay) is
already enforced by `execute()`, so running N items in dependency order is exactly
"call `execute()` N times, in an order where each item's blocker has already been
attempted." The one thing this module owns is discovering that order.

`dependsOnId`/its `blocks` backref (a single FK per item — see `ActionItem`'s docstring)
is the only populated ordering mechanism in this schema; `parentId`/`stepOrder` exist as
columns but nothing creates or reads them anywhere, so they play no part here.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from vowcraft_db import ActionItem, Transcript

from app.core.config import Settings
from app.core.exceptions import AppError, not_found
from app.services.executor import ExecutorService

#: A defensive cap, not a real-world expectation — nothing in this app produces a chain
#: remotely this long. Mirrors `BulkBody`'s `max_length=100` philosophy: a limit exists so
#: a pathological or corrupted graph fails loudly instead of walking indefinitely.
MAX_WORKFLOW_NODES = 200


class OrchestratorService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def _discover(self, user_id: str, item_id: str) -> list[ActionItem]:
        """This item, plus everything transitively reachable via `blocks`, in an order
        where every node's own blocker already appears earlier in the list.

        A breadth-first walk over the forward relationship. Because `depends_on_id` is a
        single FK, the reachable set is a tree, not an arbitrary graph — a node can have
        several children (several items depending on it) but only ever one parent, so BFS
        visiting each node once is sufficient; there is no path by which a node could need
        to appear before a parent discovered later.
        """
        root = await self.session.scalar(
            select(ActionItem)
            .join(Transcript, Transcript.id == ActionItem.transcript_id)
            .where(ActionItem.id == item_id, Transcript.user_id == user_id)
        )
        if root is None:
            raise not_found("That action item does not exist.")

        ordered = [root]
        visited = {root.id}
        queue = deque([root])
        while queue:
            current = queue.popleft()
            children = (
                await self.session.scalars(
                    select(ActionItem)
                    .join(Transcript, Transcript.id == ActionItem.transcript_id)
                    .where(
                        ActionItem.depends_on_id == current.id,
                        Transcript.user_id == user_id,
                    )
                    .order_by(ActionItem.created_at_.asc())
                )
            ).all()
            for child in children:
                # A cycle guard: nothing in this API can create one today (`dependsOnId`
                # has no write path at all — only `prisma/seed.mjs` sets it), but the FK
                # itself does not forbid one, and an infinite walk is a worse failure mode
                # than an incomplete one.
                if child.id in visited:
                    continue
                visited.add(child.id)
                ordered.append(child)
                queue.append(child)
                if len(ordered) > MAX_WORKFLOW_NODES:
                    raise AppError(
                        422, "workflow_too_large",
                        f"This chain has more than {MAX_WORKFLOW_NODES} items.",
                    )
        return ordered

    async def preview_workflow(self, user_id: str, item_id: str) -> dict[str, Any]:
        """Read-only: what `run_workflow` would attempt, and in what order — for a
        confirmation step before anything with a real side effect runs."""
        nodes = await self._discover(user_id, item_id)
        return {
            "items": [
                {
                    "id": n.id,
                    "description": n.description,
                    "status": n.status.value if hasattr(n.status, "value") else str(n.status),
                    "actionType": n.action_type.value if hasattr(n.action_type, "value") else str(n.action_type),
                }
                for n in nodes
            ]
        }

    async def run_workflow(
        self,
        user_id: str,
        user_email: str,
        item_id: str,
        *,
        confirmed: bool = False,
        request_id: str = "",
    ) -> dict[str, Any]:
        """Runs `item_id` and everything downstream of it via `blocks`.

        Does not walk upstream — this runs forward from wherever it is asked to start, not
        "the whole workflow this item happens to belong to." Every step still individually
        re-checks its own status and dependency gate inside `execute()`, so starting midway
        through an unapproved chain buys nothing over starting at its actual root; the
        natural place to trigger a run is the item that itself has no blocker, since that is
        also the only item rendered with a "this blocks N others" affordance.

        Halts the entire run — not just the failed item's own branch — at the first error
        `execute()` raises, whether that is a gate rejection (`409`, e.g.
        `blocked_by_dependency` or `not_approved`) or a genuine dispatch failure (`502
        execution_failed`; there is no "FAILED via a normal 200" path — `execute()` always
        raises on a terminal failure). An unattempted item is left exactly as it was:
        `APPROVED` and unexecuted is already an accurate, individually-gated state, so no
        new status is introduced for "skipped."

        A halt on one branch of a branching chain (one item blocking several others) also
        halts independent siblings that were not themselves blocked by the failure — a
        deliberate choice, not an oversight: proving two branches are truly independent
        before continuing would be real graph analysis for a mechanism this schema does not
        otherwise support (no priority between siblings, no parallelism anywhere else in
        this execution path), and nothing is lost by it — a sibling left unattempted here is
        exactly as invocable afterward as it was before, either directly or via a fresh
        `run_workflow` call starting at it.
        """
        # Captured as plain ids before any execution happens: `execute()` writes each
        # item's new status via a Core `update()`, which does not refresh an
        # already-identity-mapped instance, so this loop calls `session.expire_all()`
        # after every step to force the *next* item's dependency-gate check (inside
        # `execute()`) to read what was actually just committed rather than the
        # pre-execution row `_discover()` originally loaded. That leaves every `ActionItem`
        # object touched here expired, including its `id` — so nothing below reads a `node`
        # attribute again; only these plain, pre-captured ids are used.
        node_ids = [n.id for n in await self._discover(user_id, item_id)]
        executor = ExecutorService(self.session, self.settings)

        steps: list[dict[str, Any]] = []
        halted_at: dict[str, Any] | None = None
        attempted = 0
        for node_id in node_ids:
            try:
                result = await executor.execute(
                    user_id, user_email, node_id, confirmed=confirmed, request_id=request_id,
                )
            except AppError as exc:
                halted_at = {"id": node_id, "statusCode": exc.status_code, "code": exc.code, "message": exc.message}
                break
            attempted += 1
            self.session.expire_all()
            steps.append(
                {
                    "id": node_id,
                    "ok": True,
                    "replayed": bool(result.get("replayed")),
                    "status": result.get("status"),
                    "result": result.get("result"),
                }
            )

        return {
            "startedId": item_id,
            "ok": halted_at is None,
            "steps": steps,
            "haltedAt": halted_at,
            # `attempted` is the count of *successful* steps; the node at that index is the
            # one that halted the run (already reported in `haltedAt`), so skipped ids start
            # one past it.
            "skippedIds": node_ids[attempted + 1 :] if halted_at else [],
        }
