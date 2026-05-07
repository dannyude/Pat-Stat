"""Unit tests for ``src.domains.notifications.policy``.

These tests are intentionally infrastructure-free — no DB, no Redis, no
async machinery. They cover the pure decision logic that classifies
events into tiers.

Quiet-hours / deferral tests were intentionally dropped in v1 — see the
docstring in ``policy.py`` for rationale (server-side scheduling
duplicates OS-level Do Not Disturb and creates silent-drop bugs if the
release worker has issues).
"""

from src.domains.notifications import policy


# ─── tier_for_event ────────────────────────────────────────────────────────


class TestTierForEvent:
    def test_emergency_flag_is_critical(self):
        assert policy.tier_for_event(policy.EVENT_EMERGENCY_FLAG) == policy.NotificationTier.critical

    def test_status_to_critical_is_critical(self):
        assert policy.tier_for_event(policy.EVENT_STATUS_TO_CRITICAL) == policy.NotificationTier.critical

    def test_status_changed_is_important(self):
        assert policy.tier_for_event(policy.EVENT_STATUS_CHANGED) == policy.NotificationTier.important

    def test_shift_handover_is_important(self):
        assert policy.tier_for_event(policy.EVENT_SHIFT_HANDOVER) == policy.NotificationTier.important

    def test_discharge_is_important(self):
        assert policy.tier_for_event(policy.EVENT_DISCHARGE) == policy.NotificationTier.important

    def test_vitals_only_is_routine(self):
        assert policy.tier_for_event(policy.EVENT_VITALS_ONLY) == policy.NotificationTier.routine

    def test_generic_note_is_routine(self):
        assert policy.tier_for_event(policy.EVENT_GENERIC_NOTE) == policy.NotificationTier.routine

    def test_unknown_event_kind_defaults_to_routine(self):
        """Unknown events MUST default to routine — failing closed prevents
        an accidentally-spammy push when a new event kind is introduced."""
        assert policy.tier_for_event("entirely-made-up") == policy.NotificationTier.routine
        assert policy.tier_for_event("") == policy.NotificationTier.routine


# ─── decide(): routine never pushes ────────────────────────────────────────


class TestRoutineDecisions:
    def test_routine_writes_log_but_no_push(self):
        d = policy.decide(event_kind=policy.EVENT_VITALS_ONLY)
        assert d.tier == policy.NotificationTier.routine
        assert d.write_log is True
        assert d.push_immediately is False
        assert d.category == "general"

    def test_generic_note_is_routine_too(self):
        d = policy.decide(event_kind=policy.EVENT_GENERIC_NOTE)
        assert d.push_immediately is False
        assert d.category == "general"

    def test_unknown_event_is_routine_safety_net(self):
        d = policy.decide(event_kind="something_we_forgot_to_register")
        assert d.tier == policy.NotificationTier.routine
        assert d.push_immediately is False


# ─── decide(): critical always pushes ─────────────────────────────────────


class TestCriticalDecisions:
    def test_emergency_flag_pushes_immediately(self):
        d = policy.decide(event_kind=policy.EVENT_EMERGENCY_FLAG)
        assert d.tier == policy.NotificationTier.critical
        assert d.push_immediately is True
        assert d.category == "critical_alert"

    def test_status_to_critical_pushes_immediately(self):
        d = policy.decide(event_kind=policy.EVENT_STATUS_TO_CRITICAL)
        assert d.push_immediately is True
        assert d.category == "critical_alert"


# ─── decide(): important always pushes ─────────────────────────────────────


class TestImportantDecisions:
    def test_status_changed_pushes_immediately(self):
        d = policy.decide(event_kind=policy.EVENT_STATUS_CHANGED)
        assert d.tier == policy.NotificationTier.important
        assert d.push_immediately is True
        assert d.category == "shift_log"

    def test_shift_handover_pushes_immediately(self):
        d = policy.decide(event_kind=policy.EVENT_SHIFT_HANDOVER)
        assert d.push_immediately is True
        assert d.category == "shift_log"

    def test_discharge_pushes_immediately(self):
        d = policy.decide(event_kind=policy.EVENT_DISCHARGE)
        assert d.push_immediately is True
        assert d.category == "shift_log"


# ─── category mapping ──────────────────────────────────────────────────────


class TestCategoryMapping:
    def test_critical_maps_to_critical_alert(self):
        assert policy.decide(event_kind=policy.EVENT_EMERGENCY_FLAG).category == "critical_alert"

    def test_important_maps_to_shift_log(self):
        assert policy.decide(event_kind=policy.EVENT_SHIFT_HANDOVER).category == "shift_log"

    def test_routine_maps_to_general(self):
        assert policy.decide(event_kind=policy.EVENT_VITALS_ONLY).category == "general"


# ─── decision shape contract ───────────────────────────────────────────────


class TestDecisionShape:
    def test_decision_has_no_deferred_field_in_v1(self):
        """v1 explicitly does not implement deferral. If someone re-adds a
        ``deferred_until`` attribute, this test is a tripwire to make
        them confirm they also wired up the morning-digest worker."""
        d = policy.decide(event_kind=policy.EVENT_SHIFT_HANDOVER)
        assert not hasattr(d, "deferred_until"), (
            "deferred_until reintroduced — confirm the morning-digest worker "
            "exists before merging, otherwise deferred notifications will be "
            "silently dropped."
        )
