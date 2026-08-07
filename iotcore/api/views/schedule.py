"""Compatibility views for the former schedule URL terminology.

The canonical UI and URLs use ``automation`` / ``automations``.  These small
adapters preserve the old positional keyword names used by legacy routes.
"""

from .automation import (
    automation_create,
    automation_delete,
    automation_list,
    automation_toggle,
    automation_update,
)


def schedule_list(request):
    return automation_list(request)


def schedule_create(request):
    return automation_create(request)


def schedule_update(request, schedule_id):
    return automation_update(request, automation_id=schedule_id)


def schedule_toggle(request, schedule_id):
    return automation_toggle(request, automation_id=schedule_id)


def schedule_delete(request, schedule_id):
    return automation_delete(request, automation_id=schedule_id)
