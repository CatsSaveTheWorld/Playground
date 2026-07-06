from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from ...models import Device, Sequence, SequenceStep
from ...forms import SequenceForm, SequenceStepForm
from ...device_actions import DeviceActionRegistry
from ...device.services.sequence_executor import SequenceExecutor
from django.db.models import Max


# Create your views here.
@login_required(login_url="common:login")
def sequence_list(request):
    sequences = Sequence.objects.all().order_by("name")
    context = {
        "sequences": sequences,
    }
    return render(
        request,
        "iotcore/sequence_list.html",
        context
    )

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
    # print(f"[DEBUG] 시퀀스 실행 준비 완료 : {sequence}")
    SequenceExecutor.execute(sequence)
    messages.success(
        request,
        f'"{sequence.name}" 시퀀스를 실행했습니다.'
    )
    return redirect(
        "iotcore:sequence_edit",
        sequence_id
    )


@login_required(login_url="common:login")
def sequence_delete(request, sequence_id:int):
    if request.method != "POST":
        messages.error(request, "잘못된 요청입니다.")
        return redirect("iotcore:sequence_list")

    sequence = get_object_or_404(Sequence, pk=sequence_id)
    sequence.delete()
    messages.success(request, f'"{sequence.name}" 시퀀스를 삭제했습니다.')
    return redirect("iotcore:sequence_list")


@login_required(login_url="common:login")
def sequence_step_create(request, sequence_id:int):
    sequence = get_object_or_404(Sequence, id=sequence_id)
    if request.method == 'POST':
        form = SequenceStepForm(request.POST)
        if form.is_valid():
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
            step.function = request.POST.get('function')
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
    devices = Device.objects.all().order_by("name")
    device_actions = {}
    for device_type in Device.objects.values_list("device_type", flat=True).distinct():
        actions = DeviceActionRegistry.get_actions(device_type)
        device_actions[device_type] = [
            {
                "code": action.code,
                "display_name": action.display_name,
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



