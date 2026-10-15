from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .automation_graph import AutomationGraph
from .execution_history import execution_detail_context
from .manual_control_history import record_manual_control
from .models import Action, ActionRun, Automation, AutomationActionItem, AutomationConditionItem, AutomationEdge, AutomationNode, AutomationNodeRun, AutomationRun, Device, DeviceState, Step, Trigger
from .scheduler.executor import AutomationExecutor
from .scheduler.service import AutomationService


class ExecutionHistoryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('history-user', password='test')
        self.client.force_login(self.user)
        self.automation = Automation.objects.create(name='온도 관리', automation_type='scheduled')
        self.device = Device.objects.create(name='실내 센서', device_uid='history-sensor', device_type='sensor', device_role='sensor', protocol='zigbee', location='test')

    def run_record(self, **kwargs):
        return AutomationRun.objects.create(automation=self.automation, automation_name=self.automation.name, **kwargs)

    def test_login_required_and_detail_is_read_only(self):
        run = self.run_record()
        url = reverse('iotcore:execution_history_detail', args=[run.pk])
        self.assertEqual(self.client.post(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        self.assertEqual(self.client.get(reverse('iotcore:execution_history')).status_code, 302)

    def test_kst_date_range_children_grouping_and_status_filters(self):
        # 15:00 UTC is midnight in Korea; do not filter by the UTC date.
        inside = self.run_record(status='failed', started_at=datetime(2026, 10, 14, 15, 0, 2, tzinfo=dt_timezone.utc))
        outside = self.run_record(status='failed', started_at=datetime(2026, 10, 14, 14, 59, 59, tzinfo=dt_timezone.utc))
        child = self.run_record(root_run=inside, status='failed', started_at=inside.started_at)
        response = self.client.get(reverse('iotcore:execution_history'), {'start': '2026-10-15', 'end': '2026-10-15', 'status': 'failed'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '00:00:02')
        self.assertEqual([run.pk for run in response.context['history_page']], [inside.pk])
        self.assertNotContains(response, f'id="history-run-{outside.pk}"')
        self.assertEqual(self.client.get(reverse('iotcore:execution_history_detail', args=[child.pk])).status_code, 404)

    def test_no_action_is_distinct_from_completed_command(self):
        empty = self.run_record(status='success')
        done = self.run_record(status='success')
        ActionRun.objects.create(automation_run=done, root_run=done, action_type=Action.Type.DEVICE, order=1, status='success')
        response = self.client.get(reverse('iotcore:execution_history'), {'start': '', 'end': '', 'status': 'no_action'})
        self.assertEqual([run.pk for run in response.context['history_page']], [empty.pk])

    def test_filters_validation_pagination_and_escaped_names(self):
        self.automation.name = '<script>alert(1)</script>'
        self.automation.save()
        for _ in range(51): self.run_record()
        url = reverse('iotcore:execution_history')
        response = self.client.get(url, {'start': '', 'end': '', 'page': '2'})
        self.assertEqual(len(response.context['history_page']), 1)
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        response = self.client.get(url, {'start': '2026-10-15', 'end': '2026-10-14'})
        self.assertContains(response, '종료일은 시작일 이후')
        self.assertEqual(response.context['history_page'].paginator.count, 0)
        for value in ['0001-01-01','9999-12-31']:
            self.assertEqual(self.client.get(url,{'start':value,'end':value}).status_code,200)

    def test_detail_uses_recorded_evidence_and_snapshot_not_current_sensor(self):
        run = self.run_record(status='success', graph_version=1)
        import uuid
        key = uuid.uuid4()
        run.graph_snapshot = {'revision': 3, 'nodes': [{'key': str(key), 'title': '기록 당시 조건'}]}
        run.save()
        AutomationNodeRun.objects.create(automation_run=run, node_key=key, node_type='condition', status='success', started_at=timezone.now(), result={'matched': True, 'evidence': [{'type': 'device_state', 'name': '실내 센서', 'observed': 22.1, 'observed_known': True, 'observed_at': '2026-10-15T03:13:48+09:00', 'evaluated_at': '2026-10-15T03:14:02+09:00', 'operator': 'lte', 'expected': 22.5}]})
        DeviceState.objects.update_or_create(topic=AutomationService.canonical_state_topic(self.device), key='temperature', defaults={'value': 99.9})
        with patch('iotcore.weather.service.KmaWeatherService.snapshot', side_effect=AssertionError('History must not fetch live weather')):
            response = self.client.get(reverse('iotcore:execution_history_detail', args=[run.pk]))
        self.assertContains(response, '판단 값 22.1')
        self.assertContains(response, '03:13:48.000')
        self.assertContains(response, '기록 당시 조건')
        self.assertNotContains(response, '99.9')

    def test_old_detail_nested_calls_and_missing_start(self):
        root = self.run_record(status='running')
        action = ActionRun.objects.create(automation_run=root, root_run=root, action_type=Action.Type.AUTOMATION, order=1, target_automation_name='창문 닫기')
        child = self.run_record(root_run=root, parent_action_run=action, status='success', started_at=timezone.now(), finished_at=timezone.now())
        import uuid
        AutomationNodeRun.objects.create(automation_run=child, node_key=uuid.uuid4(), node_type='condition', status='success', result={'matched': False})
        response = self.client.get(reverse('iotcore:execution_history_detail', args=[root.pk]))
        self.assertContains(response, '창문 닫기 호출')
        self.assertContains(response, '하위 실행')
        self.assertContains(response, '조건 불충족')
        self.assertContains(response, '이 기록에 저장되어 있지 않습니다')

    def test_manual_control_records_once_and_never_enqueues_device_work(self):
        control = Mock(return_value=(True, '명령 접수'))
        result = record_manual_control(control, device_id=self.device.pk, motion='power_off')
        self.assertEqual(result, (True, '명령 접수'))
        control.assert_called_once_with(device_id=self.device.pk, motion='power_off')
        run = AutomationRun.objects.get()
        self.assertEqual(run.status, 'success')
        self.assertIsNotNone(run.planned_at)
        self.assertIsNotNone(run.finished_at)
        self.assertLessEqual(run.created_at, run.started_at)
        self.assertLessEqual(run.started_at, run.finished_at)
        self.assertEqual(run.action_runs.get().status, 'success')
        self.assertEqual(run.trigger_payload['kind'], 'device_control')

    def test_manual_control_exception_is_recorded_and_not_retried(self):
        control = Mock(side_effect=RuntimeError('offline'))
        with self.assertRaises(RuntimeError): record_manual_control(control, self.device.pk, 'power_off')
        self.assertEqual(control.call_count, 1)
        self.assertEqual(AutomationRun.objects.get().status, 'failed')

    def test_evidence_is_from_the_exact_evaluation_and_does_not_change_truth(self):
        topic = AutomationService.canonical_state_topic(self.device)
        state, _ = DeviceState.objects.update_or_create(topic=topic, key='temperature', defaults={'value': 22.1})
        trigger = SimpleNamespace(pk=1, trigger_type=Trigger.Type.DEVICE_STATE, config={'device_id': self.device.pk, 'key': 'temperature', 'operator': 'lte', 'value': 22.5})
        evidence = []
        self.assertTrue(AutomationService.trigger_list_matches([trigger], timezone.now(), evidence=evidence))
        self.assertEqual(evidence[0]['observed'], 22.1)
        self.assertEqual(evidence[0]['observed_at'], state.updated_at.isoformat())
        self.assertEqual(evidence[0]['matched'], True)
        self.assertTrue(AutomationService.trigger_list_matches([trigger], timezone.now()))
        state.value = None; state.save()
        evidence = []
        self.assertFalse(AutomationService.trigger_list_matches([trigger], timezone.now(), evidence=evidence))
        self.assertFalse(evidence[0]['observed_known'])

    def test_weather_evidence_does_not_fetch_a_second_snapshot(self):
        trigger = SimpleNamespace(pk=1, trigger_type='weather', config={'metric':'temperature', 'operator':'lt', 'value':20})
        with patch('iotcore.scheduler.service.KmaWeatherService.snapshot', return_value={'temperature':16.1,'fetched_at':'2026-10-15T03:10:00+09:00'}) as fetch:
            evidence=[]
            self.assertTrue(AutomationService.trigger_list_matches([trigger],timezone.now(),evidence=evidence))
        fetch.assert_called_once()
        self.assertEqual(evidence[0]['observed'],16.1)

    def test_executor_persists_evidence_without_creating_extra_control_runs(self):
        automation = self.automation
        automation.graph_version = 1
        automation.save()
        start = AutomationNode.objects.create(automation=automation,node_type='start')
        condition = AutomationNode.objects.create(automation=automation,node_type='condition')
        action = AutomationNode.objects.create(automation=automation,node_type='action')
        light = Device.objects.create(name='시험 전등',device_uid='history-light',device_type='light',device_role=Device.Role.CONTROL,protocol='zigbee',location='test')
        AutomationActionItem.objects.create(node=action,order=1,action_type='device',device=light,function='power_off')
        end = AutomationNode.objects.create(automation=automation,node_type='end')
        AutomationConditionItem.objects.create(node=condition,order=1,condition_type='device_state',config={'device_id':self.device.pk,'key':'temperature','operator':'lte','value':22.5})
        AutomationEdge.objects.create(automation=automation,source=start,target=condition,outcome='always',order=1)
        AutomationEdge.objects.create(automation=automation,source=condition,target=action,outcome='true',order=1)
        AutomationEdge.objects.create(automation=automation,source=condition,target=end,outcome='false',order=1)
        AutomationEdge.objects.create(automation=automation,source=action,target=end,outcome='always',order=1)
        DeviceState.objects.update_or_create(topic=AutomationService.canonical_state_topic(self.device),key='temperature',defaults={'value':22.1})
        with patch('iotcore.scheduler.executor.DeviceService.execute_step',return_value=(True,'ok')) as command:
            success,_=AutomationExecutor.execute(automation)
        self.assertTrue(success)
        command.assert_called_once()
        self.assertEqual(AutomationRun.objects.count(),1)
        result=AutomationNodeRun.objects.get(node_key=condition.key).result
        self.assertTrue(result['matched'])
        self.assertEqual(result['evidence'][0]['observed'],22.1)

    def test_audit_write_failure_still_sends_command_exactly_once(self):
        control=Mock(return_value=(True,'ok'))
        with patch('iotcore.manual_control_history.AutomationRun.objects.create',side_effect=RuntimeError('audit unavailable')):
            self.assertEqual(record_manual_control(control,self.device.pk,'power_off'),(True,'ok'))
        control.assert_called_once_with(self.device.pk,'power_off')

    def test_legacy_step_conditions_also_keep_frozen_evidence(self):
        step=Step.objects.create(automation=self.automation,order=1)
        Trigger.objects.create(step=step,order=1,trigger_type='device_state',config={'device_id':self.device.pk,'key':'temperature','operator':'lte','value':22.5})
        Action.objects.create(step=step,order=1,action_type='device',device=self.device,function='power_off')
        DeviceState.objects.update_or_create(topic=AutomationService.canonical_state_topic(self.device),key='temperature',defaults={'value':22.1})
        with patch('iotcore.scheduler.executor.DeviceService.execute_step',return_value=(True,'ok')):
            self.assertTrue(AutomationExecutor.execute(self.automation)[0])
        run=AutomationRun.objects.get()
        self.assertEqual(run.graph_snapshot['history_steps'][0]['evidence'][0]['observed'],22.1)
        self.assertContains(self.client.get(reverse('iotcore:execution_history_detail',args=[run.pk])),'판단 값 22.1')
