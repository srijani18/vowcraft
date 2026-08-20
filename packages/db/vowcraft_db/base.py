"""Declarative base and the naming convention Alembic needs.

The convention matters for a schema that already exists: Prisma named its constraints
its own way, and without an explicit convention Alembic autogenerate produces migrations
that try to rename every index on the first run. Naming them explicitly keeps a diff
against the live schema honest.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "%(column_0_label)s_idx",
    "uq": "%(table_name)s_%(column_0_name)s_key",
    "ck": "%(table_name)s_%(constraint_name)s_check",
    "fk": "%(table_name)s_%(column_0_name)s_fkey",
    "pk": "%(table_name)s_pkey",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
