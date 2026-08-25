"""The integration adapter contract — SPEC-002 §4.

One interface, five adapters. ``server`` and ``domain`` never import a vendor SDK, which is
what makes "add a provider" a single file rather than a change that ripples.

Every adapter has a **mock branch and a live branch**, and the mock branch labels its output
``simulated: true``. That label is load-bearing: a reviewer must never be unsure whether a
click reached the real world, and a mock result that looked real would make the whole
approval workflow untrustworthy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class ExecutionContext:
    mode: str  # "mock" | "live"
    user_email: str
    time_zone: str
    request_id: str
    idempotency_key: str
    access_token: Optional[str] = None
    api_key: Optional[str] = None
    #: The provider's non-secret configuration from the credential vault — everything in
    #: its resolved secrets except ``apiKey``, which already has its own field above.
    #:
    #: Exists because some providers need a *setting*, not just a credential, and it has to
    #: be per-user rather than deployment-wide. Notion is the case that forced it: creating
    #: a page requires a target database id, extraction cannot possibly know one, and the
    #: adapter's own "set a default in Settings" error promised a field that did not exist
    #: (the TypeScript read `NOTION_TASK_DATABASE_ID` from the environment, and that
    #: fallback was dropped in the port — leaving Notion tasks impossible to execute).
    provider_config: dict[str, str] = field(default_factory=dict)
    #: Already-resolved attachment bytes, from `services/attachments.py`. Adapters receive
    #: content, never ids: they talk to providers and never touch the database, and the
    #: ownership check that makes an id safe to read belongs on the executor's side of that
    #: line. Typed loosely to keep this module free of a service import.
    attachments: list[Any] = field(default_factory=list)


@dataclass
class ProviderResult:
    external_id: str
    #: One sentence a human can read in the success toast and the audit log — e.g.
    #: 'Created "Q3 budget review" on 21 Aug, 10:00 for 2 guests.' Each adapter computes
    #: its own; this is what `outcome.result.summary` in the frontend actually renders.
    summary: str
    external_url: Optional[str] = None
    #: True when nothing left this process. Surfaced in the UI and stored on the attempt.
    simulated: bool = False
    detail: dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    """A dispatch failure, classified for the retry policy.

    ``retryable`` is the field the executor reads. Getting it wrong in either direction is
    costly: a retryable error marked permanent fails an action that would have succeeded, and
    a permanent error marked retryable sends three identical requests to a provider that
    rejected the first for a good reason.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status: Optional[int] = None,
        detail: Optional[str] = None,
        retry_after_ms: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status
        #: The provider's own words. Logged; never rendered.
        self.detail = detail
        self.retry_after_ms = retry_after_ms


class IntegrationProvider(Protocol):
    id: str
    display_name: str
    capability: str

    def validate(self, payload: dict[str, Any]) -> list[str]:
        """Field paths that are missing or unusable. Empty means dispatchable."""
        ...

    async def execute(self, payload: dict[str, Any], ctx: ExecutionContext) -> ProviderResult: ...
