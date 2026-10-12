import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from ...automation_graph import AutomationGraph, GRAPH_VERSION, GraphValidationError
from ...device_actions import DeviceActionRegistry
from ...forms import (
    ActionFormSet,
    AutomationForm,
    AutomationGroupForm,
    StepFormSet,
    TriggerFormSet,
)
from ...models import (
    Action,
    Automation,
    AutomationActionItem,
    AutomationGroup,
    AutomationRun,
    Device,
    Step,
    Trigger,
)
from ...scheduler.calculator import describe_step
from ...scheduler.executor import AutomationExecutor
from ...scheduler.service import AutomationService


def _owner_key(form, index, *, child=False):
    field = "trigger_key" if child else "set_key"
    value = str(form.cleaned_data.get(field) or "").strip() if getattr(form, "cleaned_data", None) else ""
    if value:
        return f"key:{value}"
    if child:
        idx = form.cleaned_data.get("trigger_index") if getattr(form, "cleaned_data", None) else None
        return f"index:{idx}" if idx is not None else None
    return f"index:{index}"


def _automation_forms(request, automation):
    data = request.POST if request.method == "POST" else None
    step_qs = automation.steps.order_by("order", "id") if automation.pk else Step.objects.none()
    trigger_qs = Trigger.objects.filter(step__automation=automation).order_by("step__order", "order", "id") if automation.pk else Trigger.objects.none()
    action_qs = Action.objects.filter(step__automation=automation).order_by("step__order", "order", "id") if automation.pk else Action.objects.none()
    return (
        AutomationForm(data, instance=automation),
        StepFormSet(data, prefix="triggers", queryset=step_qs),
        TriggerFormSet(data, prefix="conditions", queryset=trigger_qs),
        ActionFormSet(data, prefix="actions", queryset=action_qs),
    )


def _seed_child_owner_fields(step_formset, trigger_formset, action_formset):
    if any(fs.is_bound for fs in (step_formset, trigger_formset, action_formset)):
        return
    step_key_by_id = {}
    for form in step_formset.forms:
        if form.instance.pk:
            key = f"step-{form.instance.pk}"
            form.fields["set_key"].initial = key
            step_key_by_id[form.instance.pk] = key
    for form in trigger_formset.forms:
        if form.instance.pk and form.instance.step_id in step_key_by_id:
            form.fields["trigger_key"].initial = step_key_by_id[form.instance.step_id]
    for form in action_formset.forms:
        if form.instance.pk and form.instance.step_id in step_key_by_id:
            form.fields["trigger_key"].initial = step_key_by_id[form.instance.step_id]


def _automation_list_url(automation_type):
    return f"{reverse('iotcore:automation_list')}?type={automation_type}"


def _validate_graph(step_formset, trigger_formset, action_formset):
    active_steps = []
    owners = {}
    for index, form in enumerate(step_formset.forms):
        if not form.cleaned_data or form.cleaned_data.get("DELETE"):
            continue
        owner = _owner_key(form, index)
        active_steps.append((index, owner, form))
        owners[owner] = {"actions": 0, "triggers": 0, "schedules": 0}

    errors = []
    if not active_steps:
        errors.append("Step을 하나 이상 등록하세요.")

    for form in trigger_formset.forms:
        if not form.cleaned_data or form.cleaned_data.get("DELETE") or not form.cleaned_data.get("condition_type"):
            continue
        owner = _owner_key(form, 0, child=True)
        if owner not in owners:
            errors.append("일부 트리거의 Step 연결 정보가 올바르지 않습니다.")
            continue
        owners[owner]["triggers"] += 1
        if form.cleaned_data.get("trigger_type") == Trigger.Type.SCHEDULE:
            owners[owner]["schedules"] += 1

    for form in action_formset.forms:
        if not form.cleaned_data or form.cleaned_data.get("DELETE") or not form.cleaned_data.get("action_type"):
            continue
        owner = _owner_key(form, 0, child=True)
        if owner not in owners:
            errors.append("일부 동작의 Step 연결 정보가 올바르지 않습니다.")
            continue
        owners[owner]["actions"] += 1

    missing_actions = [str(index + 1) for index, owner, _ in active_steps if owners[owner]["actions"] == 0]
    if missing_actions:
        errors.append(f"각 Step에는 Action이 하나 이상 필요합니다. 해당 Step: {', '.join(missing_actions)}")
    too_many_schedules = [str(index + 1) for index, owner, _ in active_steps if owners[owner]["schedules"] > 1]
    if too_many_schedules:
        errors.append(f"한 Step에는 예약 시간 Trigger를 하나만 둘 수 있습니다. 해당 Step: {', '.join(too_many_schedules)}")

    if errors:
        step_formset._non_form_errors = step_formset.error_class(errors)
        return False
    return True


def _replace_graph(automation, step_formset, trigger_formset, action_formset):
    step_rows = []
    owner_to_step = {}
    for index, form in enumerate(step_formset.forms):
        cleaned = form.cleaned_data
        if not cleaned or cleaned.get("DELETE"):
            continue
        owner = _owner_key(form, index)
        step_rows.append((owner, cleaned))

    # Pending runs may refer to Step ids that are about to be rebuilt. Cancel
    # them explicitly instead of letting them execute against a different graph.
    automation.runs.filter(
        graph_version=0,
        status=AutomationRun.Status.PENDING,
    ).update(
        status=AutomationRun.Status.CANCELLED,
        message="자동화 수정으로 취소됨",
        finished_at=timezone.now(),
    )

    # Deliberately rebuild the small graph atomically. It makes edit code and
    # debugging much easier than trying to diff nested form payloads.
    automation.steps.all().delete()

    for order, (owner, cleaned) in enumerate(step_rows, start=1):
        step = Step.objects.create(
            automation=automation,
            order=order,
            enabled=cleaned.get("enabled", True),
            trigger_operator=cleaned.get("trigger_operator") or Step.TriggerOperator.AND,
        )
        owner_to_step[owner] = step

    trigger_orders = {owner: 0 for owner in owner_to_step}
    for form in trigger_formset.forms:
        cleaned = form.cleaned_data
        if not cleaned or cleaned.get("DELETE") or not cleaned.get("condition_type"):
            continue
        owner = _owner_key(form, 0, child=True)
        step = owner_to_step.get(owner)
        if step is None:
            continue
        trigger_orders[owner] += 1
        Trigger.objects.create(
            step=step,
            order=trigger_orders[owner],
            trigger_type=cleaned["trigger_type"],
            config=cleaned.get("config") or {},
        )

    action_orders = {owner: 0 for owner in owner_to_step}
    for form in action_formset.forms:
        cleaned = form.cleaned_data
        if not cleaned or cleaned.get("DELETE") or not cleaned.get("action_type"):
            continue
        owner = _owner_key(form, 0, child=True)
        step = owner_to_step.get(owner)
        if step is None:
            continue
        action_orders[owner] += 1
        Action.objects.create(
            step=step,
            order=action_orders[owner],
            action_type=cleaned["action_type"],
            device=cleaned.get("device"),
            function=cleaned.get("function") or "",
            parameter=cleaned.get("parameter"),
            target_automation=cleaned.get("target_automation"),
            delay=cleaned.get("delay") or 0,
            delay_position=cleaned.get("delay_position") or Action.DelayPosition.AFTER,
        )

    for step in automation.steps.all():
        AutomationService.recalculate_step(step)
        AutomationService.refresh_step_result(step)

    # Keep the new graph schema synchronized while the legacy editor remains
    # available during rollout. This gives every successful edit a graph-native
    # representation without sacrificing rollback compatibility.
    AutomationGraph.sync_from_legacy(automation)


def _build_execution_blocks(step_formset, action_formset, trigger_formset):
    blocks = []
    owner_to_block = {}
    for index, form in enumerate(step_formset.forms):
        if not form.is_bound and form.instance.pk:
            owner = f"key:step-{form.instance.pk}"
        else:
            value = str(form["set_key"].value() or "").strip()
            owner = f"key:{value}" if value else f"index:{index}"
        block = {"index": index, "trigger_form": form, "conditions": [], "actions": []}
        blocks.append(block)
        owner_to_block[owner] = block

    def child_owner(form):
        value = str(form["trigger_key"].value() or "").strip()
        if value:
            return f"key:{value}"
        try:
            return f"index:{int(form['trigger_index'].value())}"
        except (TypeError, ValueError):
            return None

    for form in trigger_formset.forms:
        block = owner_to_block.get(child_owner(form))
        if block:
            block["conditions"].append(form)
    for form in action_formset.forms:
        block = owner_to_block.get(child_owner(form))
        if block:
            block["actions"].append(form)
    return blocks


def _action_ui_context():
    action_registry = {
        device_type: [
            {"code": action.code, "name": action.display_name, "parameter_key": action.parameter_key}
            for action in actions
        ]
        for device_type, actions in DeviceActionRegistry._ACTIONS.items()
    }
    device_types = {
        str(device.pk): device.device_type
        for device in Device.objects.filter(device_role__in=[Device.Role.CONTROL, Device.Role.HYBRID])
    }
    return {
        "action_registry_json": json.dumps(action_registry, ensure_ascii=False),
        "device_types_json": json.dumps(device_types, ensure_ascii=False),
    }


def _render_form(request, *, automation, form, step_formset, trigger_formset, action_formset, is_update):
    _seed_child_owner_fields(step_formset, trigger_formset, action_formset)
    context = {
        "automation": automation,
        "form": form,
        "trigger_formset": step_formset,
        "condition_formset": trigger_formset,
        "action_formset": action_formset,
        "execution_blocks": _build_execution_blocks(step_formset, action_formset, trigger_formset),
        "is_update": is_update,
        "is_immediate": automation.automation_type == Automation.Type.IMMEDIATE,
    }
    context.update(_action_ui_context())
    return render(request, "iotcore/automation_form.html", context)


def _graph_editor_context(automation, graph_payload):
    devices = list(Device.objects.order_by("location", "name", "id"))
    action_registry = {
        device_type: [
            {
                "code": action.code,
                "name": action.display_name,
                "parameter_key": action.parameter_key,
            }
            for action in actions
        ]
        for device_type, actions in DeviceActionRegistry._ACTIONS.items()
    }
    return {
        "graph_payload": graph_payload,
        "graph_devices": [
            {
                "id": device.pk,
                "name": device.name,
                "location": device.location,
                "device_type": device.device_type,
                "device_role": device.device_role,
                "protocol": device.protocol,
                # Keep the graph picker aligned with ActionForm. Legacy IR
                # electric-fan rows are intentionally excluded from generic
                # action execution; Tuya fans remain selectable.
                "controllable": bool(
                    device.is_controllable
                    and not (
                        device.device_type == "electric_fan"
                        and device.protocol != Device.Protocol.TUYA
                    )
                ),
            }
            for device in devices
        ],
        "graph_automations": [
            {"id": item.pk, "name": item.name}
            for item in Automation.objects.exclude(pk=automation.pk).order_by("name", "id")
        ] if automation.pk else [
            {"id": item.pk, "name": item.name}
            for item in Automation.objects.order_by("name", "id")
        ],
        "graph_action_registry": action_registry,
    }


def _render_graph_form(request, *, automation, form, is_update, graph_payload=None):
    if graph_payload is None:
        graph_payload = AutomationGraph.editor_payload(automation)
    context = {
        "automation": automation,
        "form": form,
        "is_update": is_update,
        "is_immediate": automation.automation_type == Automation.Type.IMMEDIATE,
    }
    context.update(_graph_editor_context(automation, graph_payload))
    return render(request, "iotcore/automation_graph_form.html", context)


def _parse_graph_post(request):
    raw = str(request.POST.get("graph_json") or "").strip()
    if not raw:
        raise GraphValidationError("그래프 데이터가 전송되지 않았습니다.")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GraphValidationError("그래프 JSON 형식이 올바르지 않습니다.") from exc


def _decorate(automation):
    graph_nodes = list(automation.graph_nodes.all()) if automation.graph_version else []
    if automation.graph_version == GRAPH_VERSION and graph_nodes:
        automation.uses_graph = True
        automation.graph_node_count = len(graph_nodes)
        automation.step_count = len([n for n in graph_nodes if n.node_type == "action"])
        automation.trigger_count = sum(n.conditions.count() for n in graph_nodes)
        automation.action_count = sum(n.actions.count() for n in graph_nodes)
        automation.summary = f"Graph Node {len(graph_nodes)}개"
        next_runs = [n.next_run_at for n in graph_nodes if n.next_run_at]
        automation.next_run_at = min(next_runs) if next_runs else None
        return

    automation.uses_graph = False
    steps = list(automation.steps.all())
    automation.step_count = len(steps)
    automation.trigger_count = sum(step.triggers.count() for step in steps)
    automation.action_count = sum(step.actions.count() for step in steps)
    automation.summary = " / ".join(describe_step(step) for step in steps) or "Step 없음"
    next_runs = [step.next_run_at for step in steps if step.next_run_at]
    automation.next_run_at = min(next_runs) if next_runs else None


@login_required(login_url="common:login")
def automation_list(request):
    selected_type = str(request.GET.get("type") or Automation.Type.SCHEDULED)
    if selected_type not in {Automation.Type.IMMEDIATE, Automation.Type.SCHEDULED}:
        selected_type = Automation.Type.SCHEDULED

    # automation_type is deliberately user-controlled. Trigger composition must
    # never move an Automation between the immediate/scheduled libraries.
    queryset = (
        Automation.objects.filter(automation_type=selected_type)
        .select_related("group")
        .prefetch_related(
            "steps__triggers",
            "steps__actions__device",
            "steps__actions__target_automation",
            "graph_nodes__conditions",
            "graph_nodes__actions__device",
            "graph_nodes__actions__target_automation",
        )
        .order_by("name", "id")
    )
    automations = list(queryset)
    for automation in automations:
        _decorate(automation)

    groups = list(AutomationGroup.objects.order_by("order", "name", "id"))
    items_by_group = {group.id: [] for group in groups}
    ungrouped = []
    for automation in automations:
        if automation.group_id in items_by_group:
            items_by_group[automation.group_id].append(automation)
        else:
            ungrouped.append(automation)

    def sort_items(items):
        if selected_type == Automation.Type.SCHEDULED:
            return sorted(items, key=lambda item: (not item.enabled, item.name.casefold(), item.id))
        return sorted(items, key=lambda item: (item.name.casefold(), item.id))

    # Every real group is rendered even when it currently has no Automation of
    # the selected type. This keeps the library structure visible and stable.
    group_sections = [
        {
            "group": group,
            "name": group.name,
            "items": sort_items(items_by_group[group.id]),
            "is_ungrouped": False,
        }
        for group in groups
    ]
    if ungrouped:
        group_sections.append({
            "group": None,
            "name": "미분류",
            "items": sort_items(ungrouped),
            "is_ungrouped": True,
        })

    is_scheduled = selected_type == Automation.Type.SCHEDULED
    return render(request, "iotcore/automation_list.html", {
        "automations": automations,
        "group_sections": group_sections,
        "selected_type": selected_type,
        "is_scheduled": is_scheduled,
        "is_immediate": not is_scheduled,
        "scheduled_url": _automation_list_url(Automation.Type.SCHEDULED),
        "immediate_url": _automation_list_url(Automation.Type.IMMEDIATE),
    })


@login_required(login_url="common:login")
def automation_create(request):
    requested_type = str(request.GET.get("type") or request.POST.get("automation_type") or Automation.Type.SCHEDULED)
    if requested_type not in {Automation.Type.IMMEDIATE, Automation.Type.SCHEDULED}:
        requested_type = Automation.Type.SCHEDULED
    automation = Automation(automation_type=requested_type, enabled=True)

    # Old formset POSTs remain accepted during rollout so existing tests,
    # bookmarks and emergency rollback workflows continue to work.
    graph_post = request.method == "POST" and "graph_json" in request.POST
    legacy_requested = request.GET.get("legacy") == "1" or (request.method == "POST" and not graph_post)
    if legacy_requested:
        form, step_formset, trigger_formset, action_formset = _automation_forms(request, automation)
        if request.method == "POST":
            valid = all([form.is_valid(), step_formset.is_valid(), trigger_formset.is_valid(), action_formset.is_valid()])
            if valid:
                valid = _validate_graph(step_formset, trigger_formset, action_formset)
            if valid:
                with transaction.atomic():
                    automation = form.save()
                    _replace_graph(automation, step_formset, trigger_formset, action_formset)
                messages.success(request, "자동화를 저장했습니다.")
                return redirect(_automation_list_url(automation.automation_type))
        return _render_form(request, automation=automation, form=form, step_formset=step_formset, trigger_formset=trigger_formset, action_formset=action_formset, is_update=False)

    form = AutomationForm(request.POST or None, instance=automation)
    graph_payload = AutomationGraph.new_editor_payload()
    if request.method == "POST":
        form_valid = form.is_valid()
        graph_valid = True
        try:
            graph_payload = _parse_graph_post(request)
            graph_payload = AutomationGraph.normalize_editor_payload(graph_payload)
        except GraphValidationError as exc:
            graph_valid = False
            form.add_error(None, str(exc))
        if form_valid and graph_valid:
            try:
                with transaction.atomic():
                    automation = form.save()
                    AutomationGraph.replace_from_payload(automation, graph_payload)
                    AutomationService.recalculate_automation(automation)
                messages.success(request, "자동화 그래프를 저장했습니다.")
                return redirect(_automation_list_url(automation.automation_type))
            except GraphValidationError as exc:
                form.add_error(None, str(exc))
    return _render_graph_form(
        request, automation=automation, form=form, is_update=False,
        graph_payload=graph_payload,
    )


@login_required(login_url="common:login")
def automation_update(request, automation_id):
    automation = get_object_or_404(Automation, pk=automation_id)
    graph_post = request.method == "POST" and "graph_json" in request.POST
    legacy_requested = request.GET.get("legacy") == "1" or (request.method == "POST" and not graph_post)
    if legacy_requested:
        form, step_formset, trigger_formset, action_formset = _automation_forms(request, automation)
        if request.method == "POST":
            valid = all([form.is_valid(), step_formset.is_valid(), trigger_formset.is_valid(), action_formset.is_valid()])
            if valid:
                valid = _validate_graph(step_formset, trigger_formset, action_formset)
            if valid:
                with transaction.atomic():
                    automation = form.save()
                    _replace_graph(automation, step_formset, trigger_formset, action_formset)
                messages.success(request, "자동화를 저장했습니다.")
                return redirect(_automation_list_url(automation.automation_type))
        return _render_form(request, automation=automation, form=form, step_formset=step_formset, trigger_formset=trigger_formset, action_formset=action_formset, is_update=True)

    form = AutomationForm(request.POST or None, instance=automation)
    graph_payload = AutomationGraph.editor_payload(automation)
    if request.method == "POST":
        form_valid = form.is_valid()
        graph_valid = True
        try:
            graph_payload = _parse_graph_post(request)
            graph_payload = AutomationGraph.normalize_editor_payload(graph_payload)
        except GraphValidationError as exc:
            graph_valid = False
            form.add_error(None, str(exc))
        if form_valid and graph_valid:
            try:
                with transaction.atomic():
                    automation = form.save()
                    AutomationGraph.replace_from_payload(automation, graph_payload)
                    AutomationService.recalculate_automation(automation)
                messages.success(request, "자동화 그래프를 저장했습니다.")
                return redirect(_automation_list_url(automation.automation_type))
            except GraphValidationError as exc:
                form.add_error(None, str(exc))
    return _render_graph_form(
        request, automation=automation, form=form, is_update=True,
        graph_payload=graph_payload,
    )


@login_required(login_url="common:login")
@require_POST
def automation_run(request, automation_id):
    automation = get_object_or_404(Automation, pk=automation_id)
    AutomationExecutor.enqueue(automation, source="manual")
    messages.success(request, f'"{automation.name}" 실행 요청을 등록했습니다.')
    return _redirect_back(request)


@login_required(login_url="common:login")
@require_POST
def automation_run_cancel(request, run_id):
    run = get_object_or_404(AutomationRun, pk=run_id)
    cancelled, message = AutomationExecutor.cancel_run(run)
    if cancelled:
        messages.success(
            request,
            f'"{run.automation_name or "자동화"}" 작업을 취소했습니다. '
            "현재 기기 상태는 변경하지 않습니다.",
        )
    else:
        messages.warning(request, message)
    return _redirect_back(request)


def _redirect_back(request):
    target = str(request.POST.get("next") or "")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(target)
    return redirect("iotcore:automation_list")


@login_required(login_url="common:login")
@require_POST
def automation_favorite_toggle(request, automation_id):
    automation = get_object_or_404(Automation, pk=automation_id)
    automation.is_favorite = not automation.is_favorite
    automation.save(update_fields=["is_favorite"])
    return _redirect_back(request)


@login_required(login_url="common:login")
@require_POST
def automation_toggle(request, automation_id):
    automation = get_object_or_404(Automation, pk=automation_id)
    automation.enabled = not automation.enabled
    automation.save(update_fields=["enabled", "updated_at"])
    AutomationService.recalculate_automation(automation)
    return _redirect_back(request)


@login_required(login_url="common:login")
@require_POST
def automation_delete(request, automation_id):
    automation = get_object_or_404(Automation, pk=automation_id)
    try:
        automation.delete()
    except ProtectedError as exc:
        referencing_names = set()
        for protected in exc.protected_objects:
            if isinstance(protected, Action):
                referencing_names.add(protected.step.automation.name)
            elif isinstance(protected, AutomationActionItem):
                referencing_names.add(protected.node.automation.name)
        referencing_names = sorted(referencing_names)
        referencing_text = ", ".join(referencing_names) or "다른 자동화"
        messages.error(
            request,
            f'"{automation.name}"을 삭제할 수 없습니다. '
            f'다음 자동화에서 사용 중입니다: {referencing_text}. '
            "해당 자동화의 호출 액션을 변경하거나 제거한 뒤 삭제하세요.",
        )
        return _redirect_back(request)
    messages.success(request, "자동화를 삭제했습니다.")
    return _redirect_back(request)


@login_required(login_url="common:login")
def automation_group_manage(request):
    if request.method == "POST":
        action = str(request.POST.get("action") or "create")
        instance = None
        if request.POST.get("group_id"):
            instance = get_object_or_404(AutomationGroup, pk=request.POST["group_id"])
        if action == "delete" and instance is not None:
            instance.delete()
            return redirect("iotcore:automation_group_manage")
        form = AutomationGroupForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            return redirect("iotcore:automation_group_manage")
    else:
        form = AutomationGroupForm()
    groups = list(AutomationGroup.objects.order_by("order", "name", "id"))
    for group in groups:
        group.item_count = group.automations.count()
    return render(request, "iotcore/group_manage.html", {
        "form": form,
        "groups": groups,
        "title": "자동화 그룹 관리",
        "eyebrow": "AUTOMATION GROUPS",
        "description": "즉시 실행과 예약 실행에서 공통으로 사용할 그룹을 관리합니다.",
        "back_url_name": "iotcore:automation_list",
    })
