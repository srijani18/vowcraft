"""Column helpers shared by every model.

Three recurring shapes, factored out because getting any of them wrong binds the model to
the wrong live column:

* ``pk()`` — Prisma's ``@default(cuid())`` is generated application-side, so there is no
  server default to inherit. The default lives here instead.
* ``enum_column()`` — binds to the Postgres enum type Prisma already created, rather than
  creating a second one under a different name.
* ``created_at()`` / ``updated_at()`` — Prisma's ``@default(now())`` *is* a server
  default, so those are left to the database; ``@updatedAt`` is not, and is handled by
  SQLAlchemy's ``onupdate``.
"""

from __future__ import annotations

import enum as _enum
from datetime import datetime
from typing import Any, Optional, Type

from sqlalchemy import DateTime, Enum, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .ids import cuid


def pk() -> Mapped[str]:
    return mapped_column(Text, primary_key=True, default=cuid)


def enum_column(
    py_enum: Type[_enum.Enum],
    *,
    name: str,
    nullable: bool = False,
    default: Any = None,
    server_default: Any = None,
    column_name: Optional[str] = None,
) -> Mapped[Any]:
    """Bind to an existing Postgres enum type.

    ``create_type=False`` is essential: the types exist already, and letting SQLAlchemy
    emit ``CREATE TYPE`` makes every migration fail on a duplicate. ``values_callable``
    stores the enum's *values* rather than its Python member names — identical here, but
    explicit so a future rename of a member cannot silently change what lands in the
    column.
    """
    kwargs: dict[str, Any] = {"nullable": nullable}
    if server_default is not None:
        kwargs["server_default"] = server_default
    if column_name:
        return mapped_column(
            column_name,
            Enum(
                py_enum,
                name=name,
                create_type=False,
                values_callable=lambda e: [m.value for m in e],
            ),
            default=default,
            **kwargs,
        )
    return mapped_column(
        Enum(
            py_enum,
            name=name,
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        default=default,
        **kwargs,
    )


def created_at(column_name: str = "createdAt") -> Mapped[datetime]:
    # Server-side, matching Prisma's @default(now()).
    return mapped_column(
        column_name, DateTime(timezone=False), server_default=func.now(), nullable=False
    )


def updated_at(column_name: str = "updatedAt") -> Mapped[datetime]:
    """Prisma's ``@updatedAt``: NOT NULL with **no database default**.

    So the value must be supplied application-side on insert as well as update. Declaring
    ``server_default`` here instead looks equivalent and is not: SQLAlchemy then omits the
    column from the INSERT, trusting a default that does not exist, and Postgres rejects
    the row with a not-null violation on every create.

    ``default`` and ``onupdate`` both point at ``func.now()`` so the timestamp is computed
    by the database clock rather than the application's — one clock, no drift between
    workers.
    """
    return mapped_column(
        column_name,
        DateTime(timezone=False),
        default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


def ts(column_name: str, *, nullable: bool = True) -> Mapped[Optional[datetime]]:
    """A plain timestamp column with no default — set by the application when it means something."""
    return mapped_column(column_name, DateTime(timezone=False), nullable=nullable)
