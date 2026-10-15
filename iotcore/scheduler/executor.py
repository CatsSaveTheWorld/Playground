from datetime import timedelta

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from ..automation_graph import AutomationGraph, GRAPH_VERSION, GraphValidationError
from ..device.services.device_service import DeviceService
from ..models import (
    Action,
    ActionRun,
    Automation,
    AutomationEdge,
    AutomationNode,
    AutomationNodeRun,
    AutomationRun,
    Device,
)
from .service import AutomationService


class AutomationExecutor:
    """Durable, device-scoped execution queue for Automations."""

    DEFAULT_ACTION_DELAY = 0.5
    ACTIVE_STATUSES = (
        AutomationRun.Status.PENDING,
        AutomationRun.Status.WAITING,
        AutomationRun.Status.RUNNING,
    )
    TERMINAL_STATUSES = (
        AutomationRun.Status.SUCCESS,
        AutomationRun.Status.FAILED,
        AutomationRun.Status.CANCELLED,
        AutomationRun.Status.SKIPPED,
    )

    @classmethod
    @transaction.atomic
    def enqueue(
        cls,
        automation,
        *,
        source=AutomationRun.Source.MANUAL,
        step=None,
        trigger_payload=None,
        entry_node_key=None,
    ):
        # Callers may still hold a pre-conversion/pre-edit instance. Lock the
        # current row so the mode, revision and graph rows form one snapshot.
        automation = Automation.objects.select_for_update().get(pk=automation.pk)
        graph_version = 0
        graph_snapshot = {}
        if automation.graph_version == GRAPH_VERSION:
            graph_snapshot = AutomationGraph.snapshot(automation)
            AutomationGraph.validate_snapshot(graph_snapshot)
            graph_version = GRAPH_VERSION
            if entry_node_key is None and step is not None:
                entry_node_key = AutomationGraph.legacy_step_entry_key(
                    automation, step.pk
                )
        return AutomationRun.objects.create(
            automation=automation,
            automation_name=automation.name,
            step=step,
            source=source,
            trigger_payload=trigger_payload or {},
            graph_version=graph_version,
            graph_snapshot=graph_snapshot,
            entry_node_key=entry_node_key,
            status=AutomationRun.Status.PENDING,
        )

    @classmethod
    def execute(
        cls,
        automation,
        *,
        source=AutomationRun.Source.MANUAL,
        trigger_payload=None,
    ):
        run = cls.enqueue(
            automation,
            source=source,
            trigger_payload=trigger_payload,
        )
        return cls.execute_run(run)

    @classmethod
    def execute_run(cls, run):
        """Drain immediately runnable work without sleeping for a delay."""
        if isinstance(run, int):
            run = AutomationRun.objects.get(pk=run)
        root = run.root_run or run

        for _ in range(1000):
            processed = cls.run_next_pending(root_run_id=root.pk)
            root.refresh_from_db()
            if root.status in cls.TERMINAL_STATUSES or processed is None:
                break

        success = root.status not in {
            AutomationRun.Status.FAILED,
            AutomationRun.Status.CANCELLED,
        }
        message = root.message or (
            "지연 작업이 실행 대기 중입니다."
            if root.status == AutomationRun.Status.RUNNING
            else "자동화 실행 완료"
        )
        return success, message

    @classmethod
    def run_next_pending(cls, *, root_run_id=None):
        planned = cls._plan_next_run(root_run_id=root_run_id)

        # Graph control-flow is drained before device commands. ACTION nodes
        # only register ActionRuns; explicit DELAY nodes are the boundary when
        # a workflow intentionally needs to wait before evaluating more flow.
        node_run = cls._claim_next_graph_node(root_run_id=root_run_id)
        if node_run is not None:
            root = node_run.automation_run.root_run or node_run.automation_run
            cls._execute_graph_node(node_run)
            root.refresh_from_db()
            return root

        action_run = cls._claim_next_action(root_run_id=root_run_id)
        if action_run is not None:
            root = action_run.root_run
            cls._execute_action_run(action_run)
            root.refresh_from_db()
            return root
        return planned

    @classmethod
    def _plan_next_run(cls, *, root_run_id=None):
        with transaction.atomic():
            runs = (
                AutomationRun.objects
                .select_for_update(skip_locked=True)
                .select_related("automation", "step", "root_run")
                .filter(
                    status=AutomationRun.Status.PENDING,
                    planned_at__isnull=True,
                )
            )
            if root_run_id is not None:
                runs = runs.filter(Q(pk=root_run_id) | Q(root_run_id=root_run_id))
            run = runs.order_by("created_at", "id").first()
            if run is None:
                return None

            root = run.root_run or run
            cls._plan_run(run, root=root, ancestry=())
            cls._refresh_run(run)
            root.refresh_from_db()
            return root

    @classmethod
    def _plan_run(cls, run, *, root, ancestry):
        now = timezone.now()
        automation = run.automation
        if automation is None:
            cls._set_run_terminal(
                run,
                AutomationRun.Status.FAILED,
                "삭제된 자동화입니다.",
            )
            return
        if automation.pk in ancestry:
            cls._set_run_terminal(
                run,
                AutomationRun.Status.FAILED,
                "자동화가 순환 참조되어 실행할 수 없습니다.",
            )
            return

        if run.graph_version == GRAPH_VERSION and run.graph_snapshot:
            cls._plan_graph_run(run, root=root, ancestry=ancestry)
            return

        if not run.automation_name:
            run.automation_name = automation.name
        run.status = AutomationRun.Status.RUNNING
        run.started_at = run.started_at or now
        run.save(update_fields=["status", "started_at", "automation_name"])

        if run.step_id:
            steps = [run.step]
        else:
            steps = list(
                automation.steps
                .filter(enabled=True)
                .prefetch_related(
                    "triggers",
                    "actions__device",
                    "actions__target_automation",
                )
                .order_by("order", "id")
            )

        if not steps:
            run.planned_at = now
            run.save(update_fields=["planned_at", "graph_snapshot"])
            cls._set_run_terminal(
                run,
                AutomationRun.Status.FAILED,
                "등록된 Step이 없습니다.",
            )
            return

        history_steps = []
        executed_any = False
        next_ancestry = ancestry + (automation.pk,)
        for step in steps:
            evidence = []
            matched = AutomationService.trigger_list_matches(
                list(step.triggers.order_by("order", "id")),
                now,
                trigger_payload=run.trigger_payload or {},
                operator=step.trigger_operator,
                resting=False,
                empty_matches=True,
                evidence=evidence,
            )
            if evidence:
                history_steps.append({"order": step.order, "matched": matched, "evaluated_at": now.isoformat(), "evidence": evidence})
                run.graph_snapshot = {"history_steps": history_steps}
            if matched is not True:
                continue

            actions = list(step.actions.order_by("order", "id"))
            if not actions:
                run.planned_at = now
                run.save(update_fields=["planned_at", "graph_snapshot"])
                cls._set_run_terminal(
                    run,
                    AutomationRun.Status.FAILED,
                    f"Step {step.order}에 실행 동작이 없습니다.",
                )
                return

            executed_any = True
            for action in actions:
                available_at = now
                status = AutomationRun.Status.PENDING
                delay_applied = False
                if (
                    action.delay
                    and action.delay_position == Action.DelayPosition.BEFORE
                ):
                    available_at = now + timedelta(seconds=action.delay)
                    status = AutomationRun.Status.WAITING
                    delay_applied = True

                action_run = ActionRun.objects.create(
                    automation_run=run,
                    root_run=root,
                    action=action,
                    device=action.device,
                    target_automation=action.target_automation,
                    step_order=step.order,
                    order=action.order,
                    action_type=action.action_type,
                    function=action.function,
                    parameter=action.parameter,
                    target_automation_name=(
                        action.target_automation.name
                        if action.target_automation_id
                        else ""
                    ),
                    delay=action.delay,
                    delay_position=action.delay_position,
                    delay_applied=delay_applied,
                    available_at=available_at,
                    status=status,
                )

                if action.action_type == Action.Type.AUTOMATION:
                    if status == AutomationRun.Status.WAITING:
                        continue
                    cls._start_child_run(
                        action_run,
                        root=root,
                        ancestry=next_ancestry,
                    )

        run.planned_at = now
        run.save(update_fields=["planned_at", "graph_snapshot"])
        if not executed_any:
            cls._set_run_terminal(
                run,
                AutomationRun.Status.SUCCESS,
                "실행 조건을 만족한 Step이 없습니다.",
            )
            return
        cls._refresh_run(run, propagate=False)

    @classmethod
    def _plan_graph_run(cls, run, *, root, ancestry):
        now = timezone.now()
        snapshot = run.graph_snapshot or {}
        try:
            AutomationGraph.validate_snapshot(snapshot)
        except GraphValidationError as exc:
            run.planned_at = now
            run.save(update_fields=["planned_at"])
            cls._set_run_terminal(
                run, AutomationRun.Status.FAILED, f"자동화 그래프가 올바르지 않습니다. ({exc})"
            )
            return

        if not run.automation_name and run.automation_id:
            run.automation_name = run.automation.name
        run.status = AutomationRun.Status.RUNNING
        run.started_at = run.started_at or now
        run.save(update_fields=["status", "started_at", "automation_name"])

        node_map = {str(node["key"]): node for node in snapshot.get("nodes", [])}
        entry_key = str(run.entry_node_key) if run.entry_node_key else None
        if entry_key:
            initial = node_map.get(entry_key)
            if initial is None:
                run.planned_at = now
                run.save(update_fields=["planned_at"])
                cls._set_run_terminal(run, AutomationRun.Status.FAILED, "시작 조건 Node를 찾을 수 없습니다.")
                return
        else:
            initial = next(
                (node for node in node_map.values() if node["type"] == AutomationNode.Type.START),
                None,
            )
            if initial is None:
                run.planned_at = now
                run.save(update_fields=["planned_at"])
                cls._set_run_terminal(run, AutomationRun.Status.FAILED, "START Node가 없습니다.")
                return

        cls._enqueue_graph_node(run, initial, available_at=now)
        run.planned_at = now
        run.save(update_fields=["planned_at"])
        cls._refresh_run(run, propagate=False)

    @classmethod
    def _enqueue_graph_node(cls, run, node_data, *, available_at=None):
        key = str(node_data["key"])
        existing = run.node_runs.filter(node_key=key).first()
        if existing is not None:
            return existing, False

        # queue_order is the durable logical flow order.  It is intentionally
        # independent from available_at so a few microseconds of timestamp drift
        # cannot reorder two same-level fan-out branches.
        next_order = (
            run.node_runs.aggregate(value=Max("queue_order"))["value"] or 0
        ) + 1
        node = None
        if run.automation_id:
            node = run.automation.graph_nodes.filter(key=key).first()
        delay = int(node_data.get("delay_seconds") or 0)
        due = available_at or timezone.now()
        status = AutomationRun.Status.PENDING
        if node_data.get("type") == AutomationNode.Type.DELAY and delay > 0:
            due = timezone.now() + timedelta(seconds=delay)
            status = AutomationRun.Status.WAITING

        # get_or_create handles the unique (run,node_key) race using a nested
        # savepoint.  Catching IntegrityError directly in the caller's atomic
        # block would leave that transaction unusable on PostgreSQL/MySQL.
        node_run, created = AutomationNodeRun.objects.get_or_create(
            automation_run=run,
            node_key=key,
            defaults={
                "node": node,
                "node_type": node_data["type"],
                "queue_order": next_order,
                "status": status,
                "available_at": due,
            },
        )
        return node_run, created

    @classmethod
    def _claim_next_graph_node(cls, *, root_run_id=None):
        now = timezone.now()
        candidates = AutomationNodeRun.objects.filter(
            status__in=[AutomationRun.Status.PENDING, AutomationRun.Status.WAITING],
            available_at__lte=now,
        ).select_related("automation_run", "automation_run__root_run")
        if root_run_id is not None:
            candidates = candidates.filter(
                Q(automation_run_id=root_run_id)
                | Q(automation_run__root_run_id=root_run_id)
            )
        ids = candidates.order_by("queue_order", "available_at", "id").values_list("id", flat=True)[:100]
        for candidate_id in ids:
            with transaction.atomic():
                candidate = (
                    AutomationNodeRun.objects.select_for_update(skip_locked=True)
                    .select_related("automation_run", "automation_run__root_run")
                    .filter(pk=candidate_id)
                    .first()
                )
                if candidate is None:
                    continue
                if candidate.status not in {AutomationRun.Status.PENDING, AutomationRun.Status.WAITING}:
                    continue
                if candidate.available_at > timezone.now():
                    continue
                root = candidate.automation_run.root_run or candidate.automation_run
                if root.status == AutomationRun.Status.CANCELLED:
                    candidate.status = AutomationRun.Status.CANCELLED
                    candidate.message = "사용자가 작업을 취소했습니다."
                    candidate.finished_at = timezone.now()
                    candidate.save(update_fields=["status", "message", "finished_at"])
                    continue
                # Graph control-flow is intentionally sequential even when more
                # than one worker process is polling the durable queue. Existing
                # device ActionRuns may still execute independently per device.
                if AutomationNodeRun.objects.filter(
                    automation_run_id=candidate.automation_run_id,
                    status=AutomationRun.Status.RUNNING,
                ).exclude(pk=candidate.pk).exists():
                    continue
                candidate.status = AutomationRun.Status.RUNNING
                candidate.started_at = candidate.started_at or timezone.now()
                candidate.save(update_fields=["status", "started_at"])
                return candidate
        return None

    @classmethod
    def _execute_graph_node(cls, node_run):
        run = node_run.automation_run
        snapshot = run.graph_snapshot or {}
        node_map = {str(node["key"]): node for node in snapshot.get("nodes", [])}
        node = node_map.get(str(node_run.node_key))
        if node is None:
            cls._fail_graph_node(node_run, "실행 Snapshot에서 Node를 찾을 수 없습니다.")
            return
        if not node.get("enabled", True):
            cls._finish_graph_node(node_run, AutomationRun.Status.SKIPPED, {"reason": "disabled"})
            return

        try:
            node_type = node["type"]
            outcome = AutomationEdge.Outcome.ALWAYS
            result = {}
            if node_type == AutomationNode.Type.CONDITION:
                triggers = [
                    AutomationGraph.condition_as_trigger(item)
                    for item in node.get("conditions", [])
                ]
                evidence = []
                matched = AutomationService.trigger_list_matches(
                    triggers,
                    timezone.now(),
                    trigger_payload=run.trigger_payload or {},
                    operator="and",
                    resting=False,
                    empty_matches=False,
                    evidence=evidence,
                )
                if matched is None:
                    cls._finish_graph_node(
                        node_run,
                        AutomationRun.Status.SKIPPED,
                        {"matched": None, "evidence": evidence},
                        message="조건 값을 확인할 수 없어 이 경로를 종료했습니다.",
                        dispatch=False,
                    )
                    return
                outcome = (
                    AutomationEdge.Outcome.TRUE
                    if matched
                    else AutomationEdge.Outcome.FALSE
                )
                result = {"matched": bool(matched), "outcome": outcome, "evidence": evidence}
            elif node_type == AutomationNode.Type.ACTION:
                cls._queue_graph_actions(run, node_run, node)
                result = {"queued_actions": len(node.get("actions", []))}
            elif node_type == AutomationNode.Type.DELAY:
                result = {"delay_seconds": int(node.get("delay_seconds") or 0)}
            elif node_type in {AutomationNode.Type.START, AutomationNode.Type.END}:
                pass
            else:
                raise GraphValidationError(f"지원하지 않는 Node 유형입니다: {node_type}")

            cls._finish_graph_node(node_run, AutomationRun.Status.SUCCESS, result, outcome=outcome)
        except Exception as exc:
            cls._fail_graph_node(node_run, f"Node 실행 중 오류가 발생했습니다. ({type(exc).__name__}: {exc})")

    @classmethod
    def _queue_graph_actions(cls, run, node_run, node):
        root = run.root_run or run
        now = timezone.now()
        for item in sorted(node.get("actions", []), key=lambda value: (value.get("order", 0), value.get("id", 0))):
            available_at = now
            status = AutomationRun.Status.PENDING
            delay_applied = False
            delay = int(item.get("delay") or 0)
            delay_position = item.get("delay_position") or Action.DelayPosition.AFTER
            if delay and delay_position == Action.DelayPosition.BEFORE:
                available_at = now + timedelta(seconds=delay)
                status = AutomationRun.Status.WAITING
                delay_applied = True

            # A Graph Run executes its immutable snapshot, not live legacy rows.
            # The legacy Action FK is therefore intentionally left NULL: a user
            # may edit/rebuild the old Step tables while this run is waiting.
            # Device/target deletion cannot be made executable, but resolving
            # missing FKs to NULL lets the normal ActionRun failure path handle
            # it cleanly instead of raising an IntegrityError while queueing.
            device_id = item.get("device_id")
            if device_id and not Device.objects.filter(pk=device_id).exists():
                device_id = None
            target_automation_id = item.get("target_automation_id")
            if target_automation_id and not Automation.objects.filter(pk=target_automation_id).exists():
                target_automation_id = None

            ActionRun.objects.create(
                automation_run=run,
                root_run=root,
                action=None,
                device_id=device_id,
                target_automation_id=target_automation_id,
                step_order=max(1, int(node_run.queue_order or 1)),
                order=max(1, int(item.get("order") or 1)),
                action_type=item.get("action_type") or Action.Type.DEVICE,
                function=item.get("function") or "",
                parameter=item.get("parameter"),
                target_automation_name=item.get("target_automation_name") or "",
                graph_node_key=node_run.node_key,
                graph_item_order=max(1, int(item.get("order") or 1)),
                delay=delay,
                delay_position=delay_position,
                delay_applied=delay_applied,
                available_at=available_at,
                status=status,
            )

    @classmethod
    def _finish_graph_node(
        cls, node_run, status, result, *, outcome=AutomationEdge.Outcome.ALWAYS,
        message="", dispatch=True,
    ):
        now = timezone.now()
        with transaction.atomic():
            current = AutomationNodeRun.objects.select_for_update().get(pk=node_run.pk)
            current.status = status
            current.result = result or {}
            current.message = message or ""
            current.finished_at = now
            current.save(update_fields=["status", "result", "message", "finished_at"])
            if dispatch and status == AutomationRun.Status.SUCCESS:
                cls._dispatch_graph_targets(current.automation_run, current.node_key, outcome)
        cls._refresh_run(node_run.automation_run)

    @classmethod
    def _dispatch_graph_targets(cls, run, source_key, outcome):
        snapshot = run.graph_snapshot or {}
        node_map = {str(node["key"]): node for node in snapshot.get("nodes", [])}
        edges = [
            edge for edge in snapshot.get("edges", [])
            if str(edge.get("source")) == str(source_key) and edge.get("outcome") == outcome
        ]
        edges.sort(key=lambda edge: (int(edge.get("order") or 0), str(edge.get("target") or "")))
        for edge in edges:
            target = node_map.get(str(edge.get("target")))
            if target is not None:
                cls._enqueue_graph_node(run, target)

    @classmethod
    def _fail_graph_node(cls, node_run, message):
        now = timezone.now()
        AutomationNodeRun.objects.filter(pk=node_run.pk).update(
            status=AutomationRun.Status.FAILED,
            message=message,
            finished_at=now,
        )
        cls._fail_graph_tree(node_run.automation_run, message)

    @classmethod
    def _fail_graph_tree(cls, run, message):
        root = run.root_run or run
        now = timezone.now()
        AutomationNodeRun.objects.filter(
            Q(automation_run=root) | Q(automation_run__root_run=root),
            status__in=[AutomationRun.Status.PENDING, AutomationRun.Status.WAITING],
        ).update(
            status=AutomationRun.Status.CANCELLED,
            message="앞선 Node 실패로 취소됨",
            finished_at=now,
        )
        ActionRun.objects.filter(
            root_run=root,
            status__in=[AutomationRun.Status.PENDING, AutomationRun.Status.WAITING],
        ).update(
            status=AutomationRun.Status.CANCELLED,
            message="앞선 Node 실패로 취소됨",
            finished_at=now,
        )
        AutomationRun.objects.filter(
            Q(pk=root.pk) | Q(root_run=root), status__in=cls.ACTIVE_STATUSES
        ).update(
            status=AutomationRun.Status.FAILED,
            message=message,
            finished_at=now,
        )

    @classmethod
    def _start_child_run(cls, action_run, *, root, ancestry):
        target = action_run.target_automation
        if target is None:
            action_run.status = AutomationRun.Status.FAILED
            action_run.message = "실행할 자동화가 지정되지 않았습니다."
            action_run.finished_at = timezone.now()
            action_run.save(update_fields=["status", "message", "finished_at"])
            return

        action_run.status = AutomationRun.Status.RUNNING
        action_run.started_at = action_run.started_at or timezone.now()
        action_run.save(update_fields=["status", "started_at"])
        graph_version = 0
        graph_snapshot = {}
        if target.graph_version == GRAPH_VERSION:
            graph_snapshot = AutomationGraph.snapshot(target)
            AutomationGraph.validate_snapshot(graph_snapshot)
            graph_version = GRAPH_VERSION
        child = AutomationRun.objects.create(
            automation=target,
            automation_name=target.name,
            source=AutomationRun.Source.AUTOMATION,
            trigger_payload={
                "parent_run_id": action_run.automation_run_id,
                "parent_action_id": action_run.action_id,
                "parent_graph_node_key": (
                    str(action_run.graph_node_key)
                    if action_run.graph_node_key
                    else None
                ),
            },
            graph_version=graph_version,
            graph_snapshot=graph_snapshot,
            status=AutomationRun.Status.PENDING,
            root_run=root,
            parent_action_run=action_run,
        )
        cls._plan_run(child, root=root, ancestry=ancestry)
        child.refresh_from_db()
        if child.status in cls.TERMINAL_STATUSES:
            cls._complete_parent_action(child, refresh_parent=False)

    @classmethod
    def _claim_next_action(cls, *, root_run_id=None):
        now = timezone.now()
        candidates = ActionRun.objects.filter(
            status__in=[
                AutomationRun.Status.PENDING,
                AutomationRun.Status.WAITING,
            ],
            available_at__lte=now,
        ).select_related(
            "device",
            "automation_run",
            "root_run",
            "target_automation",
        )
        if root_run_id is not None:
            candidates = candidates.filter(root_run_id=root_run_id)

        ids = candidates.order_by("available_at", "id").values_list(
            "id", flat=True
        )[:100]
        for candidate_id in ids:
            with transaction.atomic():
                candidate = (
                    ActionRun.objects
                    .select_for_update(skip_locked=True)
                    .select_related(
                        "device",
                        "automation_run",
                        "root_run",
                        "target_automation",
                    )
                    .filter(pk=candidate_id)
                    .first()
                )
                if candidate is None:
                    continue
                if candidate.status not in {
                    AutomationRun.Status.PENDING,
                    AutomationRun.Status.WAITING,
                } or candidate.available_at > timezone.now():
                    continue
                if candidate.root_run.status == AutomationRun.Status.CANCELLED:
                    cls._cancel_action(candidate, "사용자가 작업을 취소했습니다.")
                    continue

                is_safety_stop = bool(
                    isinstance(candidate.parameter, dict)
                    and candidate.parameter.get("safety_stop") is True
                )
                if (
                    candidate.device_id
                    and not is_safety_stop
                    and ActionRun.objects.filter(
                        device_id=candidate.device_id,
                        id__lt=candidate.id,
                        status__in=cls.ACTIVE_STATUSES,
                    ).exists()
                ):
                    continue

                candidate.status = AutomationRun.Status.RUNNING
                candidate.started_at = candidate.started_at or timezone.now()
                candidate.save(update_fields=["status", "started_at"])
                return candidate
        return None

    @classmethod
    def _execute_action_run(cls, action_run):
        if action_run.action_type == Action.Type.AUTOMATION:
            cls._execute_automation_action(action_run)
            return

        if action_run.device is None or not action_run.function:
            success, message = False, "기기 동작 정보가 올바르지 않습니다."
        else:
            try:
                success, message = DeviceService.execute_step(action_run)
            except Exception as exc:
                success = False
                message = f"기기 동작 중 예외가 발생했습니다. ({exc})"

        now = timezone.now()
        with transaction.atomic():
            current = ActionRun.objects.select_for_update().get(pk=action_run.pk)
            current.status = (
                AutomationRun.Status.SUCCESS
                if success
                else AutomationRun.Status.FAILED
            )
            current.message = message or ""
            current.finished_at = now
            current.save(update_fields=["status", "message", "finished_at"])

            if success:
                cls._schedule_next_device_action(current, now=now)
                cls._refresh_run(current.automation_run)
            else:
                cls._fail_execution_tree(
                    current,
                    message or "기기 동작에 실패했습니다.",
                )

    @classmethod
    def _automation_ancestry_for_run(cls, run):
        """Return parent Automation ids from the current nested-run chain."""
        ancestry = []
        current = run
        seen_runs = set()
        while current is not None and current.pk not in seen_runs:
            seen_runs.add(current.pk)
            if current.automation_id:
                ancestry.append(current.automation_id)
            parent_action = current.parent_action_run
            if parent_action is None:
                break
            current = parent_action.automation_run
        return tuple(ancestry)

    @classmethod
    def _execute_automation_action(cls, action_run):
        with transaction.atomic():
            current = (
                ActionRun.objects
                .select_for_update()
                .select_related("root_run", "target_automation")
                .get(pk=action_run.pk)
            )
            child = getattr(current, "child_automation_run", None)
            if child is None:
                try:
                    cls._start_child_run(
                        current,
                        root=current.root_run,
                        ancestry=cls._automation_ancestry_for_run(current.automation_run),
                    )
                    # Planning may finish the child immediately (for example
                    # a cycle or a missing Step). _start_child_run updates the
                    # parent action without refreshing its run while planning;
                    # this deferred execution path must finish propagation.
                    current.refresh_from_db()
                    if current.status == AutomationRun.Status.FAILED:
                        cls._fail_execution_tree(current, current.message)
                    else:
                        cls._refresh_run(current.automation_run)
                except Exception as exc:
                    current.status = AutomationRun.Status.FAILED
                    current.message = (
                        "중첩 자동화를 시작하지 못했습니다. "
                        f"({type(exc).__name__}: {exc})"
                    )
                    current.finished_at = timezone.now()
                    current.save(update_fields=["status", "message", "finished_at"])
                    cls._fail_execution_tree(current, current.message)
                return
            cls._complete_parent_action(child, delay_is_complete=True)

    @classmethod
    def _schedule_next_device_action(cls, action_run, *, now):
        if not action_run.device_id:
            return
        next_action = (
            ActionRun.objects
            .filter(
                root_run=action_run.root_run,
                device_id=action_run.device_id,
                id__gt=action_run.id,
                status__in=[
                    AutomationRun.Status.PENDING,
                    AutomationRun.Status.WAITING,
                ],
            )
            .order_by("id")
            .first()
        )
        if next_action is None:
            return

        delay = (
            action_run.delay
            if action_run.delay_position == Action.DelayPosition.AFTER
            else 0
        )
        seconds = delay or cls.DEFAULT_ACTION_DELAY
        resume_at = now + timedelta(seconds=seconds)
        if next_action.available_at < resume_at:
            next_action.available_at = resume_at
        if delay:
            next_action.status = AutomationRun.Status.WAITING
            next_action.message = (
                f"{action_run.device.name} 후속 작업이 "
                f"{delay}초 동안 지연 대기 중입니다."
            )
            next_action.save(update_fields=["available_at", "status", "message"])
        else:
            next_action.save(update_fields=["available_at"])

    @classmethod
    def _complete_parent_action(
        cls,
        child_run,
        *,
        delay_is_complete=False,
        refresh_parent=True,
    ):
        parent_action = child_run.parent_action_run
        if parent_action is None:
            return
        parent_action.refresh_from_db()

        if (
            child_run.status == AutomationRun.Status.SUCCESS
            and parent_action.delay
            and parent_action.delay_position == Action.DelayPosition.AFTER
            and not parent_action.delay_applied
            and not delay_is_complete
        ):
            parent_action.status = AutomationRun.Status.WAITING
            parent_action.available_at = timezone.now() + timedelta(
                seconds=parent_action.delay
            )
            parent_action.delay_applied = True
            parent_action.message = "중첩 자동화 후 지연 대기 중입니다."
            parent_action.save(
                update_fields=[
                    "status",
                    "available_at",
                    "delay_applied",
                    "message",
                ]
            )
            return

        parent_action.status = child_run.status
        parent_action.message = child_run.message
        parent_action.finished_at = timezone.now()
        parent_action.save(update_fields=["status", "message", "finished_at"])
        if refresh_parent:
            cls._refresh_run(parent_action.automation_run)

    @classmethod
    def _refresh_run(cls, run, *, propagate=True):
        run.refresh_from_db()
        if run.status in {
            AutomationRun.Status.CANCELLED,
            AutomationRun.Status.FAILED,
        }:
            if propagate:
                cls._propagate_run_completion(run)
            return

        statuses = list(run.action_runs.values_list("status", flat=True))
        node_statuses = (
            list(run.node_runs.values_list("status", flat=True))
            if run.graph_version == GRAPH_VERSION
            else []
        )
        combined = statuses + node_statuses
        if not combined:
            return
        if AutomationRun.Status.FAILED in combined:
            cls._set_run_terminal(
                run,
                AutomationRun.Status.FAILED,
                "자동화 실행 중 실패한 작업이 있습니다.",
            )
        elif any(status in cls.ACTIVE_STATUSES for status in combined):
            if run.status != AutomationRun.Status.RUNNING:
                run.status = AutomationRun.Status.RUNNING
                run.save(update_fields=["status"])
            return
        elif AutomationRun.Status.CANCELLED in combined:
            cls._set_run_terminal(
                run,
                AutomationRun.Status.CANCELLED,
                "자동화 작업이 취소되었습니다.",
            )
        else:
            cls._set_run_terminal(
                run,
                AutomationRun.Status.SUCCESS,
                "자동화 실행 완료",
            )
        if propagate:
            cls._propagate_run_completion(run)

    @classmethod
    def _propagate_run_completion(cls, run):
        if run.status in cls.TERMINAL_STATUSES and run.parent_action_run_id:
            cls._complete_parent_action(run)

    @classmethod
    def _fail_execution_tree(cls, action_run, message):
        now = timezone.now()
        root = action_run.root_run
        ActionRun.objects.filter(
            root_run=root,
            status__in=[
                AutomationRun.Status.PENDING,
                AutomationRun.Status.WAITING,
            ],
        ).update(
            status=AutomationRun.Status.CANCELLED,
            message="앞선 작업 실패로 취소됨",
            finished_at=now,
        )
        AutomationNodeRun.objects.filter(
            Q(automation_run=root) | Q(automation_run__root_run=root),
            status__in=[
                AutomationRun.Status.PENDING,
                AutomationRun.Status.WAITING,
            ],
        ).update(
            status=AutomationRun.Status.CANCELLED,
            message="앞선 작업 실패로 취소됨",
            finished_at=now,
        )
        AutomationRun.objects.filter(
            Q(pk=root.pk) | Q(root_run=root),
            status__in=cls.ACTIVE_STATUSES,
        ).update(
            status=AutomationRun.Status.FAILED,
            message=message,
            finished_at=now,
        )

    @classmethod
    def cancel_run(cls, run, *, message="사용자가 작업을 취소했습니다."):
        """Cancel a root tree without sending a compensating device command."""
        root = run.root_run or run
        now = timezone.now()
        with transaction.atomic():
            root = AutomationRun.objects.select_for_update().get(pk=root.pk)
            if root.status in cls.TERMINAL_STATUSES:
                return False, "이미 종료된 작업입니다."

            ActionRun.objects.filter(
                root_run=root,
                status__in=[
                    AutomationRun.Status.PENDING,
                    AutomationRun.Status.WAITING,
                ],
            ).update(
                status=AutomationRun.Status.CANCELLED,
                message=message,
                finished_at=now,
            )
            AutomationNodeRun.objects.filter(
                Q(automation_run=root) | Q(automation_run__root_run=root),
                status__in=[
                    AutomationRun.Status.PENDING,
                    AutomationRun.Status.WAITING,
                ],
            ).update(
                status=AutomationRun.Status.CANCELLED,
                message=message,
                finished_at=now,
            )
            # Device commands already in flight are not undone. Orchestration
            # wrappers and all not-yet-started device commands stop immediately.
            ActionRun.objects.filter(
                root_run=root,
                device__isnull=True,
                status=AutomationRun.Status.RUNNING,
            ).update(
                status=AutomationRun.Status.CANCELLED,
                message=message,
                finished_at=now,
            )
            AutomationRun.objects.filter(
                Q(pk=root.pk) | Q(root_run=root),
                status__in=cls.ACTIVE_STATUSES,
            ).update(
                status=AutomationRun.Status.CANCELLED,
                message=message,
                finished_at=now,
            )
        return True, message

    @staticmethod
    def _cancel_action(action_run, message):
        action_run.status = AutomationRun.Status.CANCELLED
        action_run.message = message
        action_run.finished_at = timezone.now()
        action_run.save(update_fields=["status", "message", "finished_at"])

    @staticmethod
    def _set_run_terminal(run, status, message):
        run.status = status
        run.message = message or ""
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "message", "finished_at"])
