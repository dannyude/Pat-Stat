"""Notification policy and routing for the PatStat platform.

This package owns the *decisions* about which events trigger pushes,
when those pushes happen, and how their visible payloads are sanitised.
It does not own the transport — that lives in ``src.tasks.notifications``
and ``src.tasks.providers.firebase_push``.
"""
