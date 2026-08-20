"""Route registration order — a class of bug that is invisible in code review.

Starlette matches routes in registration order, so a static path and a dynamic path that
could both match the same URL segment are only correctly resolved if the static one is
registered first. `/api/transcripts/pipeline-status` genuinely shipped broken this way once:
`{transcript_id}` was registered before it, so a request for "pipeline-status" matched the
dynamic route with `transcript_id="pipeline-status"` and 404'd on a route that existed.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("JWT_SECRET", "test-secret-not-used-in-any-deployment")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:3000")


def _app():
    from app.main import create_app

    return create_app()


def _matches_shape(a: str, b: str) -> bool:
    """Whether two paths could ever match the same URL — same segment count, and every
    literal segment identical (a `{param}` matches anything). Only paths of the same
    *shape* can actually collide in Starlette, which is what the first version of this
    test got wrong: it flagged `/pipeline-status` against `/{transcript_id}/extract`,
    which cannot collide because one has one segment and the other has two."""
    a_parts, b_parts = a.strip("/").split("/"), b.strip("/").split("/")
    if len(a_parts) != len(b_parts):
        return False
    for x, y in zip(a_parts, b_parts):
        is_param = lambda s: s.startswith("{") and s.endswith("}")
        if is_param(x) or is_param(y):
            continue
        if x != y:
            return False
    return True


def test_no_static_path_is_shadowed_by_a_dynamic_sibling():
    """A same-shaped static/dynamic pair only actually collides in Starlette if they also
    share an HTTP method.

    Starlette's resolution is method-aware, not simply first-path-match-wins: a route whose
    path matches but whose method does not is a *partial* match, and the router keeps
    searching for a full match rather than stopping there. `POST /action-items/bulk` is
    therefore safe registered after `GET/PATCH /action-items/{item_id}` — no method is
    shared, so the dynamic route can never claim the POST. The first version of this test
    did not model that and flagged two routes that were never actually broken.
    """
    app = _app()
    routes = [r for r in app.routes if hasattr(r, "path")]

    for i, route in enumerate(routes):
        if "{" in route.path:
            continue  # only a static path can be shadowed
        my_methods = set(getattr(route, "methods", set())) - {"HEAD", "OPTIONS"}
        for j, other in enumerate(routes):
            if other is route or "{" not in other.path or not _matches_shape(route.path, other.path):
                continue
            other_methods = set(getattr(other, "methods", set())) - {"HEAD", "OPTIONS"}
            if not (my_methods & other_methods):
                continue  # no shared method: Starlette falls through, no real collision
            assert i < j, (
                f"{route.path!r} {sorted(my_methods)} (index {i}) is registered after its "
                f"same-shaped, method-overlapping dynamic sibling {other.path!r} "
                f"{sorted(other_methods)} (index {j}) — the dynamic route will shadow it"
            )


def test_pipeline_status_specifically_is_reachable():
    """The concrete case that shipped broken. Kept alongside the general check above
    because a specific regression deserves a specific test, not only a general rule."""
    app = _app()
    paths = [r.path for r in app.routes if hasattr(r, "path")]
    static = paths.index("/api/transcripts/pipeline-status")
    dynamic = paths.index("/api/transcripts/{transcript_id}")
    assert static < dynamic
