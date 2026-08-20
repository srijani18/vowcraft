"""Vowcraft database package.

Owns the schema, the SQLAlchemy models and the Alembic migrations. Deployed on Render as
a *release job* rather than a service — a database is not a process you run, it is a
managed Postgres instance plus the migrations that shape it.

Imported by the backend; imported by nothing else.
"""

from __future__ import annotations

from .base import Base
from .clock import now_ms, to_epoch_ms
from .ids import cuid
from .models import *  # noqa: F401,F403
from .models import __all__ as _model_names

__all__ = ["Base", "cuid", "now_ms", "to_epoch_ms", *_model_names]
