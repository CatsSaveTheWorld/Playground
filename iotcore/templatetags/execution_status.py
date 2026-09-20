from django import template

from iotcore.models import ActionRun, AutomationRun


register = template.Library()


@register.simple_tag
def execution_status_snapshot(limit=5):
    """Return the live queue state displayed in the global execution panel."""
    try:
        limit = max(1, min(int(limit), 20))
    except (TypeError, ValueError):
        limit = 5

    # Child runs are represented inside their root job. Showing both would make
    # one click appear as several independently cancellable executions.
    runs = AutomationRun.objects.select_related("automation").filter(
        root_run__isnull=True
    )
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

    pending_runs = list(pending[:limit])
    running_runs = list(running[:limit])

    for run in pending_runs + running_runs:
        queue = ActionRun.objects.filter(root_run=run).select_related("device")
        run.waiting_action = queue.filter(
            status=AutomationRun.Status.WAITING
        ).order_by("available_at", "id").first()
        run.running_action = queue.filter(
            status=AutomationRun.Status.RUNNING,
            device__isnull=False,
        ).order_by("started_at", "id").first()

    return {
        "pending_count": pending.count(),
        "pending_runs": pending_runs,
        "running_count": running.count(),
        "running_runs": running_runs,
        "completed_runs": list(completed[:limit]),
        "completed_count": min(completed.count(), limit),
    }
