"""Deprecated compatibility command.

Use ``manage.py run_automation_worker``. Kept temporarily so an older systemd
unit cannot break during the deployment that introduces the unified model.
"""
from .run_automation_worker import Command  # noqa: F401
