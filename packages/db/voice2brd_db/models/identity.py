"""User, settings, team, auth identity, reset tokens — the identity cluster.

Column names are Prisma's camelCase; Python attributes are snake_case. The explicit
first argument to ``mapped_column`` is the *database* name, so ``password_hash`` reads
naturally in Python while still binding to ``"passwordHash"`` in a table this code did
not create.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import ARRAY, Boolean, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..columns import created_at, pk, ts, updated_at


class User(Base):
    __tablename__ = "User"

    id: Mapped[str] = pk()
    email: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    image: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at_: Mapped[datetime] = created_at()

    password_hash: Mapped[Optional[str]] = mapped_column("passwordHash", Text, nullable=True)
    # Every session carries this as a claim; changing it invalidates tokens issued
    # earlier, which is how "sign out everywhere" works without server-side state.
    password_updated_at: Mapped[Optional[datetime]] = ts("passwordUpdatedAt")

    onboarding_completed_at: Mapped[Optional[datetime]] = ts("onboardingCompletedAt")
    onboarding_skipped: Mapped[bool] = mapped_column(
        "onboardingSkipped", Boolean, server_default=text("false"), nullable=False
    )
    # Set when the account is scheduled for deletion; the row is retained until the
    # grace period elapses so an accidental delete is recoverable.
    deletion_requested_at: Mapped[Optional[datetime]] = ts("deletionRequestedAt")

    __table_args__ = (Index("User_email_key", "email", unique=True),)

    settings: Mapped[Optional["UserSettings"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    team_members: Mapped[list["TeamMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    identities: Mapped[list["AuthIdentity"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserSettings(Base):
    """The rule engine's ``RuleContext.settings`` — SPEC-003 §2."""

    __tablename__ = "UserSettings"

    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(
        "userId", Text, ForeignKey("User.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    user: Mapped[User] = relationship(back_populates="settings")

    time_zone: Mapped[str] = mapped_column(
        "timeZone", Text, server_default=text("'Asia/Kolkata'"), nullable=False
    )
    # "HH:MM" strings rather than times: they are compared against a zoned wall clock,
    # and storing them as timestamps would invite a timezone conversion that must not
    # happen (SPEC-003).
    workday_start: Mapped[str] = mapped_column(
        "workdayStart", Text, server_default=text("'09:00'"), nullable=False
    )
    workday_end: Mapped[str] = mapped_column(
        "workdayEnd", Text, server_default=text("'18:00'"), nullable=False
    )
    allow_weekends: Mapped[bool] = mapped_column(
        "allowWeekends", Boolean, server_default=text("false"), nullable=False
    )
    max_meeting_minutes: Mapped[int] = mapped_column(
        "maxMeetingMinutes", Integer, server_default=text("120"), nullable=False
    )
    min_buffer_minutes: Mapped[int] = mapped_column(
        "minBufferMinutes", Integer, server_default=text("15"), nullable=False
    )
    org_domains: Mapped[list[str]] = mapped_column(
        # Nullable to match the live schema: Prisma emits list columns without
        # NOT NULL. Reads coalesce to [] rather than trusting the column.
        "orgDomains", ARRAY(Text), server_default=text("ARRAY[]::text[]"), nullable=True
    )
    auto_execute_low_risk: Mapped[bool] = mapped_column(
        "autoExecuteLowRisk", Boolean, server_default=text("false"), nullable=False
    )
    budget_approval_limit: Mapped[int] = mapped_column(
        "budgetApprovalLimit", Integer, server_default=text("1000"), nullable=False
    )
    org_currency: Mapped[str] = mapped_column(
        "orgCurrency", Text, server_default=text("'USD'"), nullable=False
    )
    approval_thresholds: Mapped[dict] = mapped_column(
        "approvalThresholds", JSONB, server_default=text("'{}'::jsonb"), nullable=False
    )
    provider_routing: Mapped[dict] = mapped_column(
        "providerRouting", JSONB, server_default=text("'{}'::jsonb"), nullable=False
    )
    updated_at_: Mapped[datetime] = updated_at()

    __table_args__ = (Index("UserSettings_userId_key", "userId", unique=True),)


class TeamMember(Base):
    """Known colleagues, so an owner name resolves to an address without guessing."""

    __tablename__ = "TeamMember"

    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(
        "userId", Text, ForeignKey("User.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    user: Mapped[User] = relationship(back_populates="team_members")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("TeamMember_userId_email_key", "userId", "email", unique=True),
        Index("TeamMember_userId_name_idx", "userId", "name"),
    )


class AuthIdentity(Base):
    """A federated sign-in link — SPEC-007 §3.

    Joined on ``providerAccountId``, never on email: an email address can be reassigned
    within an organisation, and joining on it would hand the new holder the old holder's
    account.
    """

    __tablename__ = "AuthIdentity"

    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(
        "userId", Text, ForeignKey("User.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    user: Mapped[User] = relationship(back_populates="identities")
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_account_id: Mapped[str] = mapped_column("providerAccountId", Text, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column("avatarUrl", Text, nullable=True)
    created_at_: Mapped[datetime] = created_at()
    last_login_at: Mapped[Optional[datetime]] = ts("lastLoginAt")

    __table_args__ = (
        Index(
            "AuthIdentity_provider_providerAccountId_key", "provider", "providerAccountId", unique=True
        ),
        Index("AuthIdentity_userId_idx", "userId"),
    )


class PasswordResetToken(Base):
    """Single-use, hashed, short-lived — SPEC-007 §2.

    Only the SHA-256 of the token is stored: a database dump must not yield working
    reset links.
    """

    __tablename__ = "PasswordResetToken"

    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(
        "userId", Text, ForeignKey("User.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False
    )
    user: Mapped[User] = relationship(back_populates="reset_tokens")
    token_hash: Mapped[str] = mapped_column("tokenHash", Text, nullable=False)
    expires_at: Mapped[datetime] = ts("expiresAt", nullable=False)
    consumed_at: Mapped[Optional[datetime]] = ts("consumedAt")
    requested_ip: Mapped[Optional[str]] = mapped_column("requestedIp", Text, nullable=True)
    created_at_: Mapped[datetime] = created_at()

    __table_args__ = (
        Index("PasswordResetToken_tokenHash_key", "tokenHash", unique=True),
        Index("PasswordResetToken_userId_createdAt_idx", "userId", "createdAt"),
        # Sweeping expired tokens is a periodic scan; without this it is a seq scan.
        Index("PasswordResetToken_expiresAt_idx", "expiresAt"),
    )


class OAuthState(Base):
    """PKCE state for an in-flight authorization — SPEC-007 §3.

    ``userId`` is nullable because sign-in flows have no user yet; connect flows do.
    """

    __tablename__ = "OAuthState"

    state: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[Optional[str]] = mapped_column("userId", Text, nullable=True)
    purpose: Mapped[str] = mapped_column(Text, server_default=text("'integration'"), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    code_verifier: Mapped[str] = mapped_column("codeVerifier", Text, nullable=False)
    redirect_uri: Mapped[str] = mapped_column("redirectUri", Text, nullable=False)
    nonce: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    next_path: Mapped[Optional[str]] = mapped_column("nextPath", Text, nullable=True)
    consumed_at: Mapped[Optional[datetime]] = ts("consumedAt")
    expires_at: Mapped[datetime] = ts("expiresAt", nullable=False)
    created_at_: Mapped[datetime] = created_at()

    __table_args__ = (Index("OAuthState_expiresAt_idx", "expiresAt"),)
