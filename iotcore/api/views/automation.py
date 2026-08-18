import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from urllib.parse import urlencode

from ...device_actions import DeviceActionRegistry
from ...forms import (
    AutomationActionFormSet,
    AutomationConditionFormSet,
    AutomationForm,
    AutomationGroupForm,
    AutomationTriggerFormSet,
)
from ...models import (
    Automation,
    AutomationAction,
    AutomationGroup,
    AutomationCondition,
    AutomationTrigger,
    Device,
)
from ...scheduler.calculator import describe_trigger
from ...scheduler.service import AutomationService


def _automation_forms(request, automation):
    data = request.POST if request.method == "POST" else None
    trigger_queryset = AutomationTrigger.objects.none()
    action_queryset = AutomationAction.objects.none()
    condition_queryset = AutomationCondition.objects.none()
    if automation.pk:
        # TriggerSet is the parent. Conditions and actions are independent
        # 1..N child collections that point back to the owning set.
        trigger_queryset = automation.triggers.order_by("id")
        action_queryset = automation.actions.order_by("trigger_id", "order", "id")
        condition_queryset = automation.conditions.order_by(
            "trigger_id", "order", "id"
        )
    return (
        AutomationForm(data, instance=automation),
        AutomationTriggerFormSet(
            data,
            instance=automation,
            prefix="triggers",
            queryset=trigger_queryset,
        ),
        AutomationConditionFormSet(
            data,
            instance=automation,
            prefix="conditions",
            queryset=condition_queryset,
        ),
        AutomationActionFormSet(
            data,
            instance=automation,
            prefix="actions",
            queryset=action_queryset,
        ),
    )


def _active_form_indexes(formset, value_key=None):
    indexes = set()
    for index, form in enumerate(formset.forms):
        cleaned = form.cleaned_data
        if not cleaned or cleaned.get("DELETE"):
            continue
        if value_key is not None and not cleaned.get(value_key):
            continue
        indexes.add(index)
    return indexes


def _action_trigger_index(form):
    raw = form["trigger_index"].value()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _trigger_sets_have_actions(trigger_formset, action_formset):
    """Require one or more actions under every active TriggerSet.

    v4 POSTs did not contain ``actions-N-trigger_index`` because the action
    index was implicitly identical to the trigger index. Keep that one-action
    shape as a compatibility fallback while the v5 editor always posts the
    explicit owner index.
    """
    set_indexes = _active_form_indexes(trigger_formset)
    counts = {index: 0 for index in set_indexes}
    invalid_action_forms = []
    active_actions = 0

    for form_index, form in enumerate(action_formset.forms):
        cleaned = form.cleaned_data
        if not cleaned or cleaned.get("DELETE") or not cleaned.get("action_type"):
            continue
        active_actions += 1
        trigger_index = cleaned.get("trigger_index")
        if trigger_index is None and form_index in set_indexes:
            # v4 compatibility: exactly one action occupied the same form
            # index as its TriggerSet.
            trigger_index = form_index
        if trigger_index not in counts:
            invalid_action_forms.append(form_index + 1)
            continue
        counts[trigger_index] += 1

    missing = [index + 1 for index, count in counts.items() if count == 0]
    errors = []
    if not set_indexes:
        errors.append("트리거 세트를 하나 이상 등록하세요.")
    elif missing:
        errors.append(
            "각 트리거 세트에는 실행 동작이 하나 이상 필요합니다. "
            f"동작이 없는 세트: {', '.join(map(str, missing))}"
        )
    if invalid_action_forms:
        errors.append(
            "일부 실행 동작의 트리거 세트 연결 정보가 올바르지 않습니다. "
            f"동작 폼: {', '.join(map(str, invalid_action_forms))}"
        )
    if active_actions == 0:
        errors.append("실행 동작을 하나 이상 등록하세요.")

    if errors:
        action_formset._non_form_errors = action_formset.error_class(errors)
        return False
    return True


def _condition_trigger_index(form):
    raw = form["trigger_index"].value()
    if raw in (None, ""):
        raw = form["action_index"].value()
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _trigger_sets_have_conditions(trigger_formset, condition_formset):
    set_indexes = _active_form_indexes(trigger_formset)
    counts = {index: 0 for index in set_indexes}
    schedule_counts = {index: 0 for index in set_indexes}
    source_counts = {index: 0 for index in set_indexes}

    for form in condition_formset.forms:
        cleaned = form.cleaned_data
        if not cleaned or cleaned.get("DELETE") or not cleaned.get("condition_type"):
            continue
        index = cleaned.get("trigger_index")
        if index is None:
            index = cleaned.get("action_index")
        if index not in counts:
            continue
        counts[index] += 1
        condition_type = cleaned.get("condition_type")
        if condition_type == AutomationCondition.ConditionType.SCHEDULE:
            schedule_counts[index] += 1
        if condition_type in {
            AutomationCondition.ConditionType.SCHEDULE,
            AutomationCondition.ConditionType.DEVICE_STATE,
            AutomationCondition.ConditionType.MQTT_EVENT,
            AutomationCondition.ConditionType.EVENT_VALUE,
        }:
            source_counts[index] += 1

    # Backward-compatible POSTs from the v3 editor may still carry a legacy
    # trigger_type/config instead of an explicit source condition. Treat that
    # source as one condition; _replace_children() converts it to v5 data.
    for index in set_indexes:
        trigger_cleaned = trigger_formset.forms[index].cleaned_data
        legacy_type = trigger_cleaned.get("trigger_type")
        if legacy_type in {
            AutomationTrigger.TriggerType.TIME,
            AutomationTrigger.TriggerType.MQTT_EVENT,
            AutomationTrigger.TriggerType.DEVICE_STATE,
        }:
            counts[index] += 1
            source_counts[index] += 1
            if legacy_type == AutomationTrigger.TriggerType.TIME:
                schedule_counts[index] += 1

    missing = [index + 1 for index, count in counts.items() if count == 0]
    too_many_schedules = [
        index + 1 for index, count in schedule_counts.items() if count > 1
    ]
    no_source = [index + 1 for index, count in source_counts.items() if count == 0]
    errors = []
    if missing:
        errors.append(
            "각 트리거 세트에는 조건이 하나 이상 필요합니다. "
            f"조건이 없는 세트: {', '.join(map(str, missing))}"
        )
    if too_many_schedules:
        errors.append(
            "한 트리거 세트에는 '예약 시간' 조건을 하나만 둘 수 있습니다. "
            f"해당 세트: {', '.join(map(str, too_many_schedules))}"
        )
    if no_source:
        errors.append(
            "각 트리거 세트에는 상태 변화나 이벤트를 발생시키는 조건이 하나 이상 필요합니다. "
            "'시간대' 조건은 보조 조건이므로 단독으로는 실행 시점을 만들지 않습니다. "
            f"해당 세트: {', '.join(map(str, no_source))}"
        )
    if errors:
        condition_formset._non_form_errors = condition_formset.error_class(errors)
        return False
    return True


def _replace_children(automation, trigger_formset, condition_formset, action_formset):
    active_indexes = sorted(_active_form_indexes(trigger_formset))

    trigger_rows = {}
    for form_index in active_indexes:
        form = trigger_formset.forms[form_index]
        cleaned = form.cleaned_data
        enabled = cleaned.get("enabled", True)
        if not form.instance.pk and form.add_prefix("enabled") not in form.data:
            enabled = True
        trigger_rows[form_index] = {
            "enabled": enabled,
            "condition_operator": (
                cleaned.get("condition_operator")
                or AutomationTrigger.ConditionOperator.AND
            ),
            "legacy_trigger_type": cleaned.get("trigger_type"),
            "legacy_config": cleaned.get("config") or {},
        }

    # The editor treats each card as one atomic TriggerSet:
    # 1..N conditions + 1..N ordered actions.
    automation.triggers.all().delete()  # cascades owned actions/conditions
    automation.conditions.all().delete()  # malformed legacy orphans
    automation.actions.all().delete()  # legacy actions without a trigger

    trigger_by_form_index = {}
    for form_index in active_indexes:
        row = trigger_rows[form_index]
        trigger = AutomationTrigger.objects.create(
            automation=automation,
            trigger_type=AutomationTrigger.TriggerType.SET,
            config={},
            enabled=row["enabled"],
            condition_operator=row["condition_operator"],
            last_result=False,
        )
        trigger_by_form_index[form_index] = trigger

    action_order_by_trigger = {}
    for form_index, form in enumerate(action_formset.forms):
        cleaned = form.cleaned_data
        if not cleaned or cleaned.get("DELETE") or not cleaned.get("action_type"):
            continue
        trigger_index = cleaned.get("trigger_index")
        if trigger_index is None and form_index in trigger_by_form_index:
            # v4 compatibility: action form index == TriggerSet form index.
            trigger_index = form_index
        trigger = trigger_by_form_index.get(trigger_index)
        if trigger is None:
            continue
        order = action_order_by_trigger.get(trigger.pk, 0) + 1
        AutomationAction.objects.create(
            automation=automation,
            trigger=trigger,
            order=order,
            action_type=cleaned["action_type"],
            device=cleaned.get("device"),
            function=cleaned.get("function") or "",
            parameter=cleaned.get("parameter"),
            sequence=cleaned.get("sequence"),
            delay=cleaned.get("delay") or 0,
        )
        action_order_by_trigger[trigger.pk] = order

    condition_order_by_trigger = {}
    for form in condition_formset.forms:
        cleaned = form.cleaned_data
        if not cleaned or cleaned.get("DELETE") or not cleaned.get("condition_type"):
            continue
        trigger_index = cleaned.get("trigger_index")
        if trigger_index is None:
            trigger_index = cleaned.get("action_index")
        trigger = trigger_by_form_index.get(trigger_index)
        if trigger is None:
            continue
        order = condition_order_by_trigger.get(trigger.pk, 0) + 1
        AutomationCondition.objects.create(
            automation=automation,
            trigger=trigger,
            action=None,
            condition_type=cleaned["condition_type"],
            config=cleaned["config"],
            order=order,
        )
        condition_order_by_trigger[trigger.pk] = order

    # Convert any legacy v3 trigger fields received by an old browser/test
    # into an explicit source condition. New UI never enters this branch.
    for form_index in active_indexes:
        row = trigger_rows[form_index]
        legacy_type = row.get("legacy_trigger_type")
        if legacy_type not in {
            AutomationTrigger.TriggerType.TIME,
            AutomationTrigger.TriggerType.MQTT_EVENT,
            AutomationTrigger.TriggerType.DEVICE_STATE,
        }:
            continue
        trigger = trigger_by_form_index[form_index]
        config = dict(row.get("legacy_config") or {})
        order = condition_order_by_trigger.get(trigger.pk, 0) + 1
        if legacy_type == AutomationTrigger.TriggerType.TIME:
            condition_type = AutomationCondition.ConditionType.SCHEDULE
        elif legacy_type == AutomationTrigger.TriggerType.MQTT_EVENT:
            condition_type = AutomationCondition.ConditionType.MQTT_EVENT
            config.setdefault("field", "value")
            config.setdefault("operator", "received")
            config.setdefault("value", None)
        else:
            condition_type = AutomationCondition.ConditionType.DEVICE_STATE
            # If the v3 POST already supplied a condition on the same device,
            # that condition is a better source than the old broad watcher.
            same_device_exists = False
            for existing in trigger.conditions.filter(
                condition_type=AutomationCondition.ConditionType.DEVICE_STATE
            ):
                existing_config = existing.config or {}
                if (
                    config.get("device_id")
                    and str(existing_config.get("device_id")) == str(config.get("device_id"))
                ) or (
                    config.get("device_uid")
                    and str(existing_config.get("device_uid")) == str(config.get("device_uid"))
                ):
                    same_device_exists = True
                    break
            if same_device_exists:
                continue
            config.update({"key": "*", "operator": "changed", "value": None})
        AutomationCondition.objects.create(
            automation=automation,
            trigger=trigger,
            action=None,
            condition_type=condition_type,
            config=config,
            order=order,
        )
        condition_order_by_trigger[trigger.pk] = order

    # Scheduling and edge state can only be calculated after all conditions
    # have been attached to the set.
    for trigger in trigger_by_form_index.values():
        AutomationService.recalculate_trigger(trigger)
        AutomationService.refresh_trigger_result(trigger)


def _build_execution_blocks(trigger_formset, action_formset, condition_formset):
    trigger_index_by_id = {
        trigger_form.instance.pk: index
        for index, trigger_form in enumerate(trigger_formset.forms)
        if trigger_form.instance.pk
    }

    actions_by_trigger_index = {}
    for form_index, action_form in enumerate(action_formset.forms):
        if (
            not action_form.is_bound
            and action_form.instance.pk
            and action_form.instance.trigger_id
        ):
            trigger_index = trigger_index_by_id.get(action_form.instance.trigger_id)
            action_form.fields["trigger_index"].initial = trigger_index
        else:
            trigger_index = _action_trigger_index(action_form)
            if trigger_index is None and form_index < len(trigger_formset.forms):
                # Render old v4 POSTs that lack the explicit owner field.
                trigger_index = form_index
        if trigger_index is None:
            continue
        actions_by_trigger_index.setdefault(trigger_index, []).append(action_form)

    conditions_by_trigger_index = {}
    for condition_form in condition_formset.forms:
        if (
            not condition_form.is_bound
            and condition_form.instance.pk
            and condition_form.instance.trigger_id
        ):
            trigger_index = trigger_index_by_id.get(
                condition_form.instance.trigger_id
            )
            condition_form.fields["trigger_index"].initial = trigger_index
        else:
            trigger_index = _condition_trigger_index(condition_form)
        if trigger_index is None:
            continue
        conditions_by_trigger_index.setdefault(trigger_index, []).append(
            condition_form
        )

    blocks = []
    for index, trigger_form in enumerate(trigger_formset.forms):
        blocks.append({
            "index": index,
            "trigger_form": trigger_form,
            "actions": actions_by_trigger_index.get(index, []),
            "conditions": conditions_by_trigger_index.get(index, []),
        })
    return blocks


def _action_ui_context():
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
    device_types = {
        str(device.pk): device.device_type
        for device in Device.objects.filter(
            device_role__in=[Device.Role.CONTROL, Device.Role.HYBRID]
        )
    }
    return {
        "action_registry_json": json.dumps(action_registry, ensure_ascii=False),
        "device_types_json": json.dumps(device_types, ensure_ascii=False),
    }


def _render_form(request, context):
    context.update(_action_ui_context())
    if all(
        key in context
        for key in ("trigger_formset", "action_formset", "condition_formset")
    ):
        context["execution_blocks"] = _build_execution_blocks(
            context["trigger_formset"],
            context["action_formset"],
            context["condition_formset"],
        )
    return render(request, "iotcore/automation_form.html", context)


def _decorate_automation(automation):
    triggers = list(automation.triggers.all())
    automation.summary = " / ".join(
        describe_trigger(trigger) for trigger in triggers
    ) or "트리거 세트 없음"
    next_runs = [
        trigger.next_run_at for trigger in triggers
        if trigger.next_run_at is not None
    ]
    automation.next_run_at = min(next_runs) if next_runs else None

    conditions = list(automation.conditions.all())
    condition_count_by_trigger = {}
    for condition in conditions:
        if condition.trigger_id is None:
            continue
        condition_count_by_trigger[condition.trigger_id] = (
            condition_count_by_trigger.get(condition.trigger_id, 0) + 1
        )
    if triggers:
        and_count = sum(
            1 for trigger in triggers
            if trigger.condition_operator == AutomationTrigger.ConditionOperator.AND
        )
        or_count = len(triggers) - and_count
        parts = [f"세트 {len(triggers)}개", f"조건 {len(conditions)}개"]
        if and_count:
            parts.append(f"AND {and_count}개")
        if or_count:
            parts.append(f"OR {or_count}개")
        automation.condition_summary = " / ".join(parts)
    else:
        automation.condition_summary = "조건 없음"

    action_summaries = []
    for action in automation.actions.all():
        if action.action_type == AutomationAction.ActionType.SEQUENCE:
            label = action.sequence.name if action.sequence else "-"
        else:
            device_name = action.device.name if action.device else "-"
            device_type = action.device.device_type if action.device else ""
            function_name = DeviceActionRegistry.get_display_name(
                device_type,
                action.function,
            )
            label = f"{device_name}: {function_name}"
        condition_count = condition_count_by_trigger.get(action.trigger_id, 0)
        if condition_count:
            operator_label = "AND"
            if action.trigger_id:
                try:
                    operator_label = (
                        "OR"
                        if action.trigger.condition_operator == AutomationTrigger.ConditionOperator.OR
                        else "AND"
                    )
                except Exception:
                    pass
            label += f" [{operator_label} 조건 {condition_count}개]"
        if action.delay:
            label += f" ({action.delay}초 후)"
        action_summaries.append(label)
    automation.action_summary = " / ".join(action_summaries) or "실행 동작 없음"


@login_required(login_url="common:login")
def automation_list(request):
    query = str(request.GET.get("q") or "").strip()
    scope = str(request.GET.get("scope") or "all").strip()
    status = str(request.GET.get("status") or "all").strip()
    trigger_filter = str(request.GET.get("trigger") or "all").strip()
    action_filter = str(request.GET.get("action") or "all").strip()
    sort = str(request.GET.get("sort") or "next").strip()

    if status not in {"all", "enabled", "disabled"}:
        status = "all"
    trigger_choices = [
        (AutomationCondition.ConditionType.SCHEDULE, "예약 시간"),
        (AutomationCondition.ConditionType.TIME_WINDOW, "시간대"),
        (AutomationCondition.ConditionType.DEVICE_STATE, "기기 상태"),
        (AutomationCondition.ConditionType.MQTT_EVENT, "MQTT 이벤트"),
    ]
    valid_triggers = {value for value, _ in trigger_choices}
    if trigger_filter != "all" and trigger_filter not in valid_triggers:
        trigger_filter = "all"
    valid_actions = {value for value, _ in AutomationAction.ActionType.choices}
    if action_filter != "all" and action_filter not in valid_actions:
        action_filter = "all"
    if sort not in {"next", "updated", "name"}:
        sort = "next"

    queryset = (
        Automation.objects
        .select_related("group")
        .prefetch_related(
            "triggers__conditions",
            "conditions",
            "actions__trigger",
            "actions__device",
            "actions__sequence",
        )
    )

    if scope == "favorite":
        queryset = queryset.filter(is_favorite=True)
    elif scope == "ungrouped":
        queryset = queryset.filter(group__isnull=True)
    elif scope.startswith("group:"):
        try:
            group_id = int(scope.split(":", 1)[1])
        except (TypeError, ValueError):
            scope = "all"
        else:
            if AutomationGroup.objects.filter(pk=group_id).exists():
                queryset = queryset.filter(group_id=group_id)
            else:
                scope = "all"

    if status == "enabled":
        queryset = queryset.filter(enabled=True)
    elif status == "disabled":
        queryset = queryset.filter(enabled=False)
    if trigger_filter != "all":
        queryset = queryset.filter(conditions__condition_type=trigger_filter)
    if action_filter != "all":
        queryset = queryset.filter(actions__action_type=action_filter)

    automations = list(queryset.distinct())
    for automation in automations:
        _decorate_automation(automation)

    if query:
        needle = query.casefold()
        automations = [
            automation
            for automation in automations
            if needle in " ".join([
                automation.name,
                automation.group.name if automation.group else "미분류",
                "활성" if automation.enabled else "비활성",
                automation.summary,
                automation.condition_summary,
                automation.action_summary,
            ]).casefold()
        ]

    if sort == "name":
        automations.sort(key=lambda item: (item.name.casefold(), item.id))
    elif sort == "updated":
        automations.sort(
            key=lambda item: (item.updated_at, item.id),
            reverse=True,
        )
    else:
        automations.sort(
            key=lambda item: (
                item.next_run_at is None,
                item.next_run_at,
                item.name.casefold(),
            )
        )

    groups = list(
        AutomationGroup.objects
        .annotate(item_count=Count("automations"))
        .order_by("order", "name", "id")
    )
    grouped = {group.id: [] for group in groups}
    ungrouped = []
    for automation in automations:
        if automation.group_id in grouped:
            grouped[automation.group_id].append(automation)
        else:
            ungrouped.append(automation)

    sections = [
        {"name": group.name, "group": group, "items": grouped[group.id]}
        for group in groups
        if grouped[group.id]
    ]
    if ungrouped:
        sections.append({"name": "미분류", "group": None, "items": ungrouped})

    def scope_url(value):
        params = {
            "scope": value,
            "status": status,
            "trigger": trigger_filter,
            "action": action_filter,
            "sort": sort,
        }
        if query:
            params["q"] = query
        return "?" + urlencode(params)

    group_tabs = [
        {
            "name": group.name,
            "count": group.item_count,
            "scope": f"group:{group.id}",
            "url": scope_url(f"group:{group.id}"),
        }
        for group in groups
    ]

    total_count = Automation.objects.count()
    clear_search_url = "?" + urlencode({
        "scope": scope,
        "status": status,
        "trigger": trigger_filter,
        "action": action_filter,
        "sort": sort,
    })
    context = {
        "automations": automations,
        "automation_sections": sections,
        "groups": groups,
        "group_tabs": group_tabs,
        "query": query,
        "current_scope": scope,
        "current_status": status,
        "current_trigger": trigger_filter,
        "current_action": action_filter,
        "current_sort": sort,
        "trigger_choices": trigger_choices,
        "action_choices": AutomationAction.ActionType.choices,
        "total_count": total_count,
        "active_count": Automation.objects.filter(enabled=True).count(),
        "inactive_count": Automation.objects.filter(enabled=False).count(),
        "favorite_count": Automation.objects.filter(is_favorite=True).count(),
        "ungrouped_count": Automation.objects.filter(group__isnull=True).count(),
        "scope_all_url": scope_url("all"),
        "scope_favorite_url": scope_url("favorite"),
        "scope_ungrouped_url": scope_url("ungrouped"),
        "clear_search_url": clear_search_url,
        "has_any_automations": total_count > 0,
    }
    return render(request, "iotcore/automation_list.html", context)


def _automation_redirect_back(request):
    target = str(request.POST.get("next") or "")
    if target and url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(target)
    return redirect("iotcore:automation_list")


@login_required(login_url="common:login")
@require_POST
def automation_favorite_toggle(request, automation_id):
    automation = get_object_or_404(Automation, pk=automation_id)
    automation.is_favorite = not automation.is_favorite
    automation.save(update_fields=["is_favorite"])
    return _automation_redirect_back(request)


@login_required(login_url="common:login")
def automation_group_manage(request):
    if request.method == "POST":
        action = str(request.POST.get("action") or "").strip()
        if action == "create":
            form = AutomationGroupForm(request.POST)
            if form.is_valid():
                group = form.save()
                messages.success(request, f'예약 실행 그룹 "{group.name}"을 만들었습니다.')
            else:
                messages.error(
                    request,
                    "그룹을 만들지 못했습니다. "
                    + " ".join(
                        error
                        for errors in form.errors.values()
                        for error in errors
                    ),
                )
        elif action in {"update", "delete"}:
            group = get_object_or_404(
                AutomationGroup,
                pk=request.POST.get("group_id"),
            )
            if action == "delete":
                group_name = group.name
                group.delete()
                messages.success(
                    request,
                    f'예약 실행 그룹 "{group_name}"을 삭제했습니다. 소속 예약 실행은 미분류로 이동했습니다.',
                )
            else:
                form = AutomationGroupForm(request.POST, instance=group)
                if form.is_valid():
                    form.save()
                    messages.success(request, "예약 실행 그룹을 수정했습니다.")
                else:
                    messages.error(
                        request,
                        "그룹을 수정하지 못했습니다. "
                        + " ".join(
                            error
                            for errors in form.errors.values()
                            for error in errors
                        ),
                    )
        return redirect("iotcore:automation_group_manage")

    groups = (
        AutomationGroup.objects
        .annotate(item_count=Count("automations"))
        .order_by("order", "name", "id")
    )
    return render(request, "iotcore/group_manage.html", {
        "group_kind": "automation",
        "eyebrow": "AUTOMATION GROUPS",
        "title": "예약 실행 그룹 관리",
        "description": "예약 실행을 목적별로 묶습니다. 그룹을 삭제해도 예약 실행은 삭제되지 않고 미분류로 이동합니다.",
        "groups": groups,
        "back_url_name": "iotcore:automation_list",
    })

@login_required(login_url="common:login")
def automation_create(request):
    automation = Automation()
    form, trigger_formset, condition_formset, action_formset = _automation_forms(
        request, automation
    )
    if (
        request.method == "POST"
        and form.is_valid()
        and trigger_formset.is_valid()
        and condition_formset.is_valid()
        and action_formset.is_valid()
        and _trigger_sets_have_actions(trigger_formset, action_formset)
        and _trigger_sets_have_conditions(trigger_formset, condition_formset)
    ):
        with transaction.atomic():
            automation = form.save()
            _replace_children(
                automation,
                trigger_formset,
                condition_formset,
                action_formset,
            )
        messages.success(request, "예약 실행을 생성했습니다.")
        return redirect("iotcore:automation_list")
    return _render_form(request, {
        "form": form,
        "trigger_formset": trigger_formset,
        "condition_formset": condition_formset,
        "action_formset": action_formset,
        "is_update": False,
    })


@login_required(login_url="common:login")
def automation_update(request, automation_id):
    automation = get_object_or_404(Automation, pk=automation_id)
    form, trigger_formset, condition_formset, action_formset = _automation_forms(
        request, automation
    )
    if (
        request.method == "POST"
        and form.is_valid()
        and trigger_formset.is_valid()
        and condition_formset.is_valid()
        and action_formset.is_valid()
        and _trigger_sets_have_actions(trigger_formset, action_formset)
        and _trigger_sets_have_conditions(trigger_formset, condition_formset)
    ):
        with transaction.atomic():
            automation = form.save()
            _replace_children(
                automation,
                trigger_formset,
                condition_formset,
                action_formset,
            )
        messages.success(request, "예약 실행을 수정했습니다.")
        return redirect("iotcore:automation_list")
    return _render_form(request, {
        "form": form,
        "trigger_formset": trigger_formset,
        "condition_formset": condition_formset,
        "action_formset": action_formset,
        "automation": automation,
        "is_update": True,
    })


@login_required(login_url="common:login")
@require_POST
def automation_toggle(request, automation_id):
    automation = get_object_or_404(Automation, pk=automation_id)
    automation.enabled = not automation.enabled
    automation.save(update_fields=["enabled", "updated_at"])
    AutomationService.recalculate_automation(automation)
    messages.success(
        request,
        f"예약 실행을 {'활성화' if automation.enabled else '비활성화'}했습니다.",
    )
    return _automation_redirect_back(request)


@login_required(login_url="common:login")
@require_POST
def automation_delete(request, automation_id):
    automation = get_object_or_404(Automation, pk=automation_id)
    automation.delete()
    messages.success(request, "예약 실행을 삭제했습니다.")
    return _automation_redirect_back(request)
