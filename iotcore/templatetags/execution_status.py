from django import template

from iotcore.models import AutomationRun


register = template.Library()


@register.simple_tag
def execution_status_snapshot(limit=5):
    """Return the live queue state displayed in the global execution panel."""
    try:
        limit = max(1, min(int(limit), 20))
    except (TypeError, ValueError):
        limit = 5

    runs = AutomationRun.objects.select_related("automation")
    pending = runs.filter(status=AutomationRun.Status.PENDING).order_by(
        "created_at", "id"
    )
    running = runs.filter(status=AutomationRun.Status.RUNNING).order_by(
        "started_at", "id"
    )
    completed = runs.filter(
        status__in=[
            AutomationRun.Status.SUCCESS,
            AutomationRun.Status.FAILED,
            AutomationRun.Status.CANCELLED,
            AutomationRun.Status.SKIPPED,
        ]
    ).order_by("-finished_at", "-id")

    return {
        "pending_count": pending.count(),
        "pending_runs": list(pending[:limit]),
        "running_count": running.count(),
        "running_runs": list(running[:limit]),
        "completed_runs": list(completed[:limit]),
        "completed_count": min(completed.count(), limit),
    }
