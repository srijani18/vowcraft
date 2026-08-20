"""Auth request and response bodies.

Shapes match what the existing frontend already sends and reads, so the cutover is a base
URL change plus attaching a bearer token — not a rewrite of every form.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SignupRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: str = Field(max_length=254)
    # Not validated for length here: the policy in `domain/password_rules` owns that and
    # returns a message naming how many characters are missing. A Pydantic length error
    # would replace that with "String should have at least 12 characters".
    password: str = Field(max_length=400)
    name: Optional[str] = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: str = Field(max_length=254)
    password: str = Field(max_length=400)


class RefreshRequest(BaseModel):
    refreshToken: str


class ChangePasswordRequest(BaseModel):
    # Optional: an account with no password yet (Google-only sign-in) is setting one for
    # the first time and has nothing to prove — see the note in AuthService.change_password.
    currentPassword: Optional[str] = Field(default=None, max_length=400)
    newPassword: str = Field(max_length=400)


class UserView(BaseModel):
    """What the frontend renders. No hash, no token, no internal timestamps beyond
    what the UI actually shows."""

    id: str
    email: str
    name: Optional[str]
    image: Optional[str]
    hasPassword: bool
    onboardingCompletedAt: Optional[str]
    onboardingSkipped: bool
    createdAt: Optional[str]


class TokenPair(BaseModel):
    accessToken: str
    refreshToken: str
    tokenType: str = "Bearer"
    #: Seconds until the *access* token expires, so the client can refresh proactively
    #: rather than waiting for a 401 and retrying.
    expiresIn: int


class AuthResponse(BaseModel):
    ok: bool = True
    user: UserView
    tokens: TokenPair
