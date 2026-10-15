"""Audit explicit web device commands without putting them on the scheduler queue."""
import logging

from django.db import transaction
from django.utils import timezone

from .execution_history import action_label
from .models import Action, ActionRun, AutomationRun, Device

logger = logging.getLogger(__name__)


def record_manual_control(control, *args, **kwargs):
    device_id = args[0] if args else kwargs.get('device_id')
    motion = args[1] if len(args) > 1 else kwargs.get('motion', '')
    run = action = None
    try:
        with transaction.atomic():
            device = Device.objects.filter(pk=device_id).first()
            now = timezone.now()
            name = (device.name if device else '기기') + ' · ' + action_label(motion, device.device_type if device else '')
            run = AutomationRun.objects.create(automation_name=name[:100], source=AutomationRun.Source.MANUAL, status=AutomationRun.Status.RUNNING, planned_at=now, trigger_payload={'kind': 'device_control', 'device_name': device.name if device else '기기'})
            now = timezone.now()
            run.started_at = now
            run.save(update_fields=['started_at'])
            parameters = {key: (value[:256] if isinstance(value, str) else value) for key, value in kwargs.items() if key in {'bits', 'playlist_id', 'music_id', 'volume', 'repeat_mode', 'fan_value', 'window_position'} and value is not None and isinstance(value, (str, int, float, bool))}
            action = ActionRun.objects.create(automation_run=run, root_run=run, device=device, order=1, action_type=Action.Type.DEVICE, function=motion, parameter=parameters or None, status=AutomationRun.Status.RUNNING, started_at=now, available_at=now)
    except Exception:
        # An audit failure must not repeat or prevent an already authorized command.
        run = action = None
        logger.exception('Could not start manual control history')
    try:
        success, message = control(*args, **kwargs)
    except Exception:
        _finish(run, action, False, '기기 제어 중 오류가 발생했습니다.')
        raise
    _finish(run, action, success, message)
    return success, message


def _finish(run, action, success, message):
    if run is None:
        return
    try:
        with transaction.atomic():
            fields = {'finished_at': timezone.now(), 'status': AutomationRun.Status.SUCCESS if success else AutomationRun.Status.FAILED, 'message': str(message or '')[:2000]}
            AutomationRun.objects.filter(pk=run.pk, status=AutomationRun.Status.RUNNING).update(**fields)
            ActionRun.objects.filter(pk=action.pk, status=AutomationRun.Status.RUNNING).update(**fields)
    except Exception:
        logger.exception('Could not finish manual control history')


def record_manual_result(control, device, command, *args, **kwargs):
    result = {}

    def invoke(device_id, motion):
        result['value'] = control(device, command, *args, **kwargs)
        return bool(result['value'].get('success')), result['value'].get('message', '')

    record_manual_control(invoke, device.pk, command)
    return result['value']
