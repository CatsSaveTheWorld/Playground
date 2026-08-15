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
from ...scheduler.calculator import describe_condition, describe_trigger
from ...scheduler.service import AutomationService


def _automation_forms(request, automation):
    data = request.POST if request.method == "POST" else None
    return (
        AutomationForm(data, instance=automation),
        AutomationTriggerFormSet(data, instance=automation, prefix="triggers"),
        AutomationConditionFormSet(data, instance=automation, prefix="conditions"),
        AutomationActionFormSet(data, instance=automation, prefix="actions"),
    )


def _replace_children(automation, trigger_formset, condition_formset, action_formset):
    automation.triggers.all().delete()
    for form in trigger_formset.forms:
        cleaned = form.cleaned_data
        if not cleaned or cleaned.get("DELETE") or not cleaned.get("trigger_type"):
            continue
        enabled = cleaned.get("enabled", True)
        previous_schedule_type = (form.instance.config or {}).get(
            "schedule_type"
        )
        current_schedule_type = (cleaned.get("config") or {}).get(
            "schedule_type"
        )
        # Dynamically added checkbox controls can be omitted by the browser.
        # A new trigger must be active by default; the automation-level toggle
        # remains the user-facing way to pause execution.
        if not form.instance.pk and form.add_prefix("enabled") not in form.data:
            enabled = True
        # A one-time trigger turns itself off after it runs.  If the user
        # changes that completed trigger into a repeating schedule, make the
        # new schedule runnable without requiring them to notice this
        # per-trigger checkbox.
        if (
            previous_schedule_type == AutomationTrigger.ScheduleType.ONCE
            and current_schedule_type
            in {
                AutomationTrigger.ScheduleType.DAILY,
                AutomationTrigger.ScheduleType.WEEKLY,
                AutomationTrigger.ScheduleType.INTERVAL,
            }
        ):
            enabled = True
        trigger = AutomationTrigger.objects.create(
            automation=automation,
            trigger_type=cleaned["trigger_type"],
            config=cleaned["config"],
            enabled=enabled,
        )
        AutomationService.recalculate_trigger(trigger)

    automation.conditions.all().delete()
    order = 1
    for form in condition_formset.forms:
        cleaned = form.cleaned_data
        if not cleaned or cleaned.get("DELETE") or not cleaned.get("condition_type"):
            continue
        AutomationCondition.objects.create(
            automation=automation,
            condition_type=cleaned["condition_type"],
            config=cleaned["config"],
            order=order,
        )
        order += 1

    automation.actions.all().delete()
    order = 1
    for form in action_formset.forms:
        cleaned = form.cleaned_data
        if not cleaned or cleaned.get("DELETE") or not cleaned.get("action_type"):
            continue
        AutomationAction.objects.create(
            automation=automation,
            order=order,
            action_type=cleaned["action_type"],
            device=cleaned.get("device"),
            function=cleaned.get("function") or "",
            parameter=cleaned.get("parameter"),
            sequence=cleaned.get("sequence"),
            delay=cleaned.get("delay") or 0,
        )
        order += 1


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
    return render(request, "iotcore/automation_form.html", context)


def _decorate_automation(automation):
    triggers = list(automation.triggers.all())
    automation.summary = " / ".join(
        describe_trigger(trigger) for trigger in triggers
    ) or "실행 계기 없음"
    next_runs = [
        trigger.next_run_at for trigger in triggers
        if trigger.next_run_at is not None
    ]
    automation.next_run_at = min(next_runs) if next_runs else None
    automation.condition_summary = " / ".join(
        describe_condition(condition)
        for condition in automation.conditions.all()
    ) or "조건 없음"

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
    valid_triggers = {value for value, _ in AutomationTrigger.TriggerType.choices}
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
            "triggers",
            "conditions",
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
        queryset = queryset.filter(triggers__trigger_type=trigger_filter)
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
        "trigger_choices": AutomationTrigger.TriggerType.choices,
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
