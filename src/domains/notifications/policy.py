"""Notification routing policy — single source of truth for "what gets pushed".

This module is intentionally side-effect-free and infrastructure-free.
The Celery task and HTTP handlers import :func:`decide` to find out
whether to write a NotificationLog row, whether to send an FCM push,
or both.

Design — v1: tiering only, no scheduling
----------------------------------------
The job we're solving is **anti-spam**. A nurse logging vitals 15× a
day shouldn't buzz a family member's phone 15 times. We achieve that
with three tiers:

  • critical  — emergency flags, status downgrades to "Critical".
                Always pushed.
  • important — status changes (any other), shift handovers, discharge.
                Always pushed.
  • routine   — vitals/notes that didn't change clinical status.
                In-app inbox only; never pushed.

What we deliberately do NOT implement in v1
-------------------------------------------
* **Quiet hours / deferred sends.** Modern phones already give users
  granular control via OS-level Do Not Disturb / Focus modes. Building
  it server-side duplicates an OS feature and (worse) creates a code
  path where notifications can be silently swallowed if the deferred-
  release worker has a bug. If users complain about night-time pushes,
  the right fix is a per-user "quiet hours" preference, not a global
  default.
* **Per-user opt-in/out.** Every recipient gets the same tier mapping.
  Future preference work hangs off this same :func:`decide` function —
  add a per-user lookup, return a tier-overridden decision.

Customisation hook
------------------
When you eventually add per-user preferences, do it here. Every call
site already routes through :func:`decide` — extend it to take a
``recipient_user_id`` and look up overrides before returning.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class NotificationTier(str, enum.Enum):
    """Severity tier of a notifiable event.

    The mapping from event source to tier lives in :func:`tier_for_event`,
    not at the call site. Handlers pass an ``event_kind`` and let this
    module decide.
    """

    critical = "critical"
    important = "important"
    routine = "routine"  # In-app log only. Never pushed.


@dataclass(frozen=True, slots=True)
class NotificationDecision:
    """The output of policy evaluation for a single (event, recipient) pair.

    Attributes
    ----------
    tier
        The classified tier — useful for analytics and the in-app filter
        tabs ("Critical Alerts").
    write_log
        Whether to write a ``NotificationLog`` row at all. True for
        every tier in v1 — even routine events fill the in-app inbox.
    push_immediately
        True for ``critical`` and ``important`` tiers; False for
        ``routine``. The Celery task sends FCM only when this is True.
    category
        The ``NotificationCategory`` value to store on the log row, so
        the UI can filter by tab.
    """

    tier: NotificationTier
    write_log: bool
    push_immediately: bool
    category: str  # NotificationCategory value (kept as str for storage)


# Event-kind strings the handlers send us. Centralised here so a typo at the
# call site fails fast on import-time validation rather than silently
# downgrading something to "routine".
EVENT_EMERGENCY_FLAG = "emergency_flag"
EVENT_STATUS_CHANGED = "status_changed"
EVENT_STATUS_TO_CRITICAL = "status_to_critical"
EVENT_VITALS_ONLY = "vitals_only"
EVENT_SHIFT_HANDOVER = "shift_handover"
EVENT_DISCHARGE = "discharge"
EVENT_GENERIC_NOTE = "generic_note"

_KNOWN_EVENTS: frozenset[str] = frozenset(
    {
        EVENT_EMERGENCY_FLAG,
        EVENT_STATUS_CHANGED,
        EVENT_STATUS_TO_CRITICAL,
        EVENT_VITALS_ONLY,
        EVENT_SHIFT_HANDOVER,
        EVENT_DISCHARGE,
        EVENT_GENERIC_NOTE,
    }
)


def tier_for_event(event_kind: str) -> NotificationTier:
    """Return the policy tier for a given event kind.

    Unknown event kinds are conservatively classified as ``routine`` —
    in-app only, never pushed — so a forgotten call site can never
    accidentally spam a family member's phone.
    """
    if event_kind == EVENT_EMERGENCY_FLAG:
        return NotificationTier.critical
    if event_kind == EVENT_STATUS_TO_CRITICAL:
        return NotificationTier.critical
    if event_kind in (EVENT_STATUS_CHANGED, EVENT_SHIFT_HANDOVER, EVENT_DISCHARGE):
        return NotificationTier.important
    # Vitals-only updates and free-text notes that didn't change status fall
    # here — they go in the inbox, but never as a push.
    return NotificationTier.routine


def _category_for_tier(tier: NotificationTier) -> str:
    """Map a tier to the ``NotificationCategory`` enum value used in storage."""
    if tier == NotificationTier.critical:
        return "critical_alert"
    if tier == NotificationTier.important:
        return "shift_log"
    return "general"


def decide(*, event_kind: str) -> NotificationDecision:
    """Evaluate the policy for a single recipient.

    Parameters
    ----------
    event_kind
        One of the ``EVENT_*`` constants in this module. Anything else
        is treated as ``routine`` (defensive default).
    """
    tier = tier_for_event(event_kind)
    category = _category_for_tier(tier)

    return NotificationDecision(
        tier=tier,
        write_log=True,
        push_immediately=(tier != NotificationTier.routine),
        category=category,
    )


__all__ = [
    "EVENT_DISCHARGE",
    "EVENT_EMERGENCY_FLAG",
    "EVENT_GENERIC_NOTE",
    "EVENT_SHIFT_HANDOVER",
    "EVENT_STATUS_CHANGED",
    "EVENT_STATUS_TO_CRITICAL",
    "EVENT_VITALS_ONLY",
    "NotificationDecision",
    "NotificationTier",
    "decide",
    "tier_for_event",
]
