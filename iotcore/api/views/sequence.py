from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.db.models import Count, Max, Q
from django.shortcuts import render, get_object_or_404, redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from ...models import Device, Sequence, SequenceGroup, SequenceRun, SequenceStep
from ...forms import SequenceForm, SequenceGroupForm, SequenceStepForm
from ...device_actions import DeviceActionRegistry
from ...device.services.sequence_service import SequenceService
from ...device.services.device_service import DeviceService
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from urllib.parse import urlencode



# Create your views here.
@login_required(login_url="common:login")
def sequence_list(request):
    query = str(request.GET.get("q") or "").strip()
    scope = str(request.GET.get("scope") or "all").strip()
    sort = str(request.GET.get("sort") or "updated").strip()
    if sort not in {"updated", "name", "steps"}:
        sort = "updated"

    queryset = (
        Sequence.objects
        .select_related("group")
        .prefetch_related("steps__device")
        .annotate(step_count=Count("steps", distinct=True))
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
            if SequenceGroup.objects.filter(pk=group_id).exists():
                queryset = queryset.filter(group_id=group_id)
            else:
                scope = "all"

    order_by = {
        "updated": ("-updated_at", "name", "id"),
        "name": ("name", "id"),
        "steps": ("-step_count", "name", "id"),
    }[sort]
    sequences = list(queryset.order_by(*order_by))

    if query:
        needle = query.casefold()
        filtered = []
        for sequence in sequences:
            searchable = [
                sequence.name,
                sequence.description,
                sequence.group.name if sequence.group else "미분류",
            ]
            for step in sequence.steps.all():
                searchable.extend([
                    step.device.name,
                    step.device.location,
                    step.function,
                    DeviceActionRegistry.get_display_name(
                        step.device.device_type,
                        step.function,
                    ),
                ])
            if needle in " ".join(str(value or "") for value in searchable).casefold():
                filtered.append(sequence)
        sequences = filtered

    groups = list(
        SequenceGroup.objects
        .annotate(item_count=Count("sequences"))
        .order_by("order", "name", "id")
    )
    grouped = {group.id: [] for group in groups}
    ungrouped = []
    for sequence in sequences:
        if sequence.group_id in grouped:
            grouped[sequence.group_id].append(sequence)
        else:
            ungrouped.append(sequence)

    sections = [
        {"name": group.name, "group": group, "items": grouped[group.id]}
        for group in groups
        if grouped[group.id]
    ]
    if ungrouped:
        sections.append({"name": "미분류", "group": None, "items": ungrouped})

    def scope_url(value):
        params = {"scope": value, "sort": sort}
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

    total_count = Sequence.objects.count()
    favorite_count = Sequence.objects.filter(is_favorite=True).count()
    ungrouped_count = Sequence.objects.filter(group__isnull=True).count()
    clear_search_url = "?" + urlencode({"scope": scope, "sort": sort})

    context = {
        "sequences": sequences,
        "sequence_sections": sections,
        "groups": groups,
        "group_tabs": group_tabs,
        "query": query,
        "current_scope": scope,
        "current_sort": sort,
        "total_count": total_count,
        "favorite_count": favorite_count,
        "ungrouped_count": ungrouped_count,
        "scope_all_url": scope_url("all"),
        "scope_favorite_url": scope_url("favorite"),
        "scope_ungrouped_url": scope_url("ungrouped"),
        "clear_search_url": clear_search_url,
        "has_any_sequences": total_count > 0,
    }
    return render(request, "iotcore/sequence_list.html", context)


def _sequence_redirect_back(request):
    target = str(request.POST.get("next") or "")
    if target and url_has_allowed_host_and_scheme(
        target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(target)
    return redirect("iotcore:sequence_list")


@login_required(login_url="common:login")
@require_POST
def sequence_favorite_toggle(request, sequence_id):
    sequence = get_object_or_404(Sequence, pk=sequence_id)
    sequence.is_favorite = not sequence.is_favorite
    sequence.save(update_fields=["is_favorite"])
    return _sequence_redirect_back(request)


@login_required(login_url="common:login")
def sequence_group_manage(request):
    if request.method == "POST":
        action = str(request.POST.get("action") or "").strip()
        if action == "create":
            form = SequenceGroupForm(request.POST)
            if form.is_valid():
                group = form.save()
                messages.success(request, f'시퀀스 그룹 "{group.name}"을 만들었습니다.')
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
                SequenceGroup,
                pk=request.POST.get("group_id"),
            )
            if action == "delete":
                group_name = group.name
                group.delete()
                messages.success(
                    request,
                    f'시퀀스 그룹 "{group_name}"을 삭제했습니다. 소속 시퀀스는 미분류로 이동했습니다.',
                )
            else:
                form = SequenceGroupForm(request.POST, instance=group)
                if form.is_valid():
                    form.save()
                    messages.success(request, "시퀀스 그룹을 수정했습니다.")
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
        return redirect("iotcore:sequence_group_manage")

    groups = (
        SequenceGroup.objects
        .annotate(item_count=Count("sequences"))
        .order_by("order", "name", "id")
    )
    return render(request, "iotcore/group_manage.html", {
        "group_kind": "sequence",
        "eyebrow": "SEQUENCE GROUPS",
        "title": "시퀀스 그룹 관리",
        "description": "시퀀스를 목적별로 묶습니다. 그룹을 삭제해도 시퀀스는 삭제되지 않고 미분류로 이동합니다.",
        "groups": groups,
        "back_url_name": "iotcore:sequence_list",
    })

@login_required(login_url="common:login")
def sequence_create(request, ):
    if request.method == "POST":
        form = SequenceForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("iotcore:sequence_list")
    else:
        form = SequenceForm()
    context = {
        "form": form,
    }
    return render(
        request,
        "iotcore/sequence_form.html",
        context,
    )


@login_required(login_url="common:login")
def sequence_run(request, sequence_id):
    if request.method != "POST":
        return redirect("iotcore:sequence_edit", sequence_id)
    sequence = get_object_or_404(
        Sequence,
        pk=sequence_id
    )
    sequence_run = SequenceRun.objects.create(
        sequence=sequence,
        sequence_name=sequence.name,
        trigger=SequenceRun.Trigger.MANUAL,
        status=SequenceRun.Status.PENDING,
    )
    messages.success(
        request,
        f'"{sequence.name}" 시퀀스를 실행 대기열에 등록했습니다. (#{sequence_run.id})'
    )
    return _sequence_redirect_back(request)


@login_required(login_url="common:login")
def sequence_update(request, sequence_id):
    sequence = get_object_or_404(
        Sequence,
        pk=sequence_id
    )

    if request.method == "POST":
        form = SequenceForm(
            request.POST,
            instance=sequence
        )

        if form.is_valid():
            form.save()

            return redirect(
                "iotcore:sequence_edit",
                sequence_id
            )

    else:
        form = SequenceForm(
            instance=sequence
        )

    context = {
        "form": form,
        "sequence": sequence,
        "is_update": True,
    }

    return render(
        request,
        "iotcore/sequence_form.html",
        context,
    )


@login_required(login_url="common:login")
def sequence_delete(request, sequence_id:int):
    if request.method != "POST":
        messages.error(request, "잘못된 요청입니다.")
        return _sequence_redirect_back(request)

    sequence = get_object_or_404(Sequence, pk=sequence_id)
    sequence_name = sequence.name

    blocking_actions = sequence.automation_actions.select_related("automation")
    blocking_action_count = blocking_actions.count()
    if blocking_action_count:
        automation_names = list(
            blocking_actions
            .order_by("automation__name")
            .values_list("automation__name", flat=True)
            .distinct()[:3]
        )
        names = ", ".join(automation_names)
        messages.error(
            request,
            f'"{sequence_name}" 시퀀스를 사용하는 예약 실행 동작이 있어 삭제할 수 없습니다. '
            f"먼저 예약 실행에서 해당 동작을 제거하세요. "
            f"({names}, 총 {blocking_action_count}개 동작)",
        )
        return _sequence_redirect_back(request)

    try:
        with transaction.atomic():
            sequence.runs.filter(
                status=SequenceRun.Status.PENDING,
            ).update(
                status=SequenceRun.Status.CANCELLED,
                finished_at=timezone.now(),
                message="원본 시퀀스가 삭제되어 대기 중 실행을 취소했습니다.",
            )
            sequence.delete()
    except ProtectedError:
        messages.error(
            request,
            f'"{sequence_name}" 시퀀스를 참조하는 설정이 있어 삭제할 수 없습니다.',
        )
        return _sequence_redirect_back(request)
    except IntegrityError:
        messages.error(
            request,
            "시퀀스 실행 이력 연결을 해제하지 못했습니다. "
            "최신 데이터베이스 마이그레이션 적용 여부를 확인하세요.",
        )
        return _sequence_redirect_back(request)

    messages.success(request, f'"{sequence_name}" 시퀀스를 삭제했습니다.')
    return _sequence_redirect_back(request)


@login_required(login_url="common:login")
def sequence_step_create(request, sequence_id:int):
    sequence = get_object_or_404(Sequence, id=sequence_id)
    if request.method == 'POST':
        form = SequenceStepForm(request.POST)
        if form.is_valid():
            device = form.cleaned_data["device"]
            function = str(request.POST.get("function") or "").strip()
            action = next(
                (
                    item
                    for item in DeviceActionRegistry.get_actions(device.device_type)
                    if item.code == function
                ),
                None,
            )
            if action is None:
                messages.error(request, "선택한 기기에서 지원하지 않는 동작입니다.")
                return redirect("iotcore:sequence_edit", sequence.id)

            parameter = None
            if action.parameter_key:
                raw_value = request.POST.get("parameter_value")
                if device.device_type == "electric_fan":
                    command, error = DeviceService.prepare_electric_fan_command(
                        function,
                        raw_value,
                    )
                    if error:
                        messages.error(request, error)
                        return redirect("iotcore:sequence_edit", sequence.id)
                    normalized_value = command[1]
                else:
                    if raw_value in (None, ""):
                        messages.error(request, "동작에 필요한 설정 값을 입력하세요.")
                        return redirect("iotcore:sequence_edit", sequence.id)
                    normalized_value = raw_value
                parameter = {action.parameter_key: normalized_value}

            # --------------------------
            # 마지막 order 계산
            # --------------------------
            last_order = (
                SequenceStep.objects
                .filter(sequence=sequence)
                .aggregate(Max("order"))["order__max"]
            )
            if last_order is None:
                last_order = 0
            # --------------------------
            # Step 생성
            # --------------------------
            step = form.save(commit=False)
            step.sequence = sequence
            step.order = last_order + 1
            step.function = function
            step.parameter = parameter
            step.full_clean()
            step.save()
            # --------------------------
            # 저장 후 다시 Edit 화면으로
            # --------------------------
            return redirect(
                "iotcore:sequence_edit",
                sequence.id
            )
    return redirect(
        "iotcore:sequence_edit",
        sequence.id
    )


@login_required(login_url="common:login")
def sequence_edit(request, sequence_id:int):
    sequence = get_object_or_404(Sequence, id=sequence_id)
    steps = (
        sequence.steps
        .select_related("device")
        .order_by("order")
    )

    for step in steps:
        step.function_display = DeviceActionRegistry.get_display_name(
            step.device.device_type,
            step.function,
        )
    step_form = SequenceStepForm()
    devices = (
        Device.objects.filter(
            device_role__in=[Device.Role.CONTROL, Device.Role.HYBRID]
        )
        .filter(~Q(device_type="electric_fan") | Q(protocol=Device.Protocol.TUYA))
        .order_by("name")
    )
    device_actions = {}
    for device_type in devices.values_list("device_type", flat=True).distinct():
        actions = DeviceActionRegistry.get_actions(device_type)
        device_actions[device_type] = [
            {
                "code": action.code,
                "display_name": action.display_name,
                "parameter_key": action.parameter_key,
            }
            for action in actions
        ]
    context = {
        "sequence": sequence,
        "steps": steps,
        "step_form": step_form,
        "devices": devices,
        "device_actions": device_actions,
    }
    return render(request, "iotcore/sequence_edit.html", context)


@login_required
@require_POST
def sequence_step_delete(request):
    step_ids = request.POST.getlist("step_ids")

    if not step_ids:
        return JsonResponse({
            "success": False,
            "message": "삭제할 Step이 없습니다."
        }, status=400)

    steps = SequenceStep.objects.filter(id__in=step_ids)
    sequence = steps.first().sequence if steps.exists() else None
    count = steps.count()
    steps.delete()

    if sequence:
        SequenceService.normalize_order(sequence)

    return JsonResponse({
        "success": True,
        "message": f"{count}개의 Step이 삭제되었습니다."
    })
