from django import template
from django.db.models import Q

from iotcore.models import ActionRun, AutomationNode, AutomationNodeRun, AutomationRun


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

        # Graph DELAY nodes are intentionally separate from legacy ActionRun
        # delays. Include both the root run and nested automation runs so the
        # global panel still tells the user what the whole execution tree is
        # waiting for.
        run.waiting_node = (
            AutomationNodeRun.objects
            .filter(
                Q(automation_run=run) | Q(automation_run__root_run=run),
                status=AutomationRun.Status.WAITING,
                node_type=AutomationNode.Type.DELAY,
            )
            .select_related("node")
            .order_by("available_at", "queue_order", "id")
            .first()
        )

    return {
        "pending_count": pending.count(),
        "pending_runs": pending_runs,
        "running_count": running.count(),
        "running_runs": running_runs,
        "completed_runs": list(completed[:limit]),
        "completed_count": min(completed.count(), limit),
    }
