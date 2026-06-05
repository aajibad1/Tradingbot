"""Notification models."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Channel(str, Enum):
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    SMS = "sms"
    PUSH = "push"
    EMAIL = "email"
    SLACK = "slack"


class Priority(str, Enum):
    NORMAL = "normal"
    CRITICAL = "critical"   # kill switch / loss-limit — forces an SMS fallback


class Notification(BaseModel):
    user_id: str
    country: str = Field(default="", description="ISO-3166 alpha-2; drives channel routing")
    message: str
    priority: Priority = Priority.NORMAL
    # Per-channel destination address (phone, telegram chat_id, email, …). Only
    # channels with BOTH a recipient and a configured provider are attempted.
    recipients: dict[Channel, str] = Field(default_factory=dict)


class Attempt(BaseModel):
    channel: Channel
    ok: bool
    error: str | None = None


class DispatchResult(BaseModel):
    delivered: bool
    channel: Channel | None = None     # the channel that succeeded
    attempts: list[Attempt] = Field(default_factory=list)
