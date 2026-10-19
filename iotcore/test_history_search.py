from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import ActionRun, AutomationRun, AutomationNodeRun, Device


class HistorySearchTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user('search-user'))
        self.url = reverse('iotcore:execution_history')
        self.now = datetime(2026, 10, 19, 3, 0, tzinfo=dt_timezone.utc)
        self.window = Device.objects.create(name='창문', device_uid='search-window', device_type='window_pusher', device_role='control', protocol='zigbee', location='test')
        self.aircon = Device.objects.create(name='에어컨', device_uid='search-aircon', device_type='aircon', device_role='control', protocol='zigbee', location='test')

    def record(self, name='온도 관리', status='success', **kwargs):
        return AutomationRun.objects.create(automation_name=name, status=status, started_at=self.now, **kwargs)

    def command(self, run, device=None, function='power_off', **kwargs):
        return ActionRun.objects.create(automation_run=run, root_run=run.root_run or run, device=device or self.window, function=function, action_type='device', order=1, status='success', **kwargs)

    def search(self, **params):
        return self.client.get(self.url, {'period': 'all', **params})

    def ids(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['history_filter'].errors)
        return [run.pk for run in response.context['history_page']]

    def test_default_today_hides_only_no_action_and_keeps_errors_and_active(self):
        empty = self.record()
        skipped = self.record(status='skipped')
        failed = self.record(status='failed')
        running = self.record(status='running')
        pending = self.record(status='pending')
        success = self.record(); self.command(success)
        old = self.record(status='failed'); old.started_at -= timedelta(days=1); old.save()
        with patch('iotcore.execution_history.timezone.now', return_value=self.now):
            self.assertEqual(set(self.ids(self.client.get(self.url))), {failed.pk, running.pk, pending.pk, success.pk})
        self.assertEqual(set(self.ids(self.search(include_no_action='on'))), {empty.pk, skipped.pk, failed.pk, running.pk, pending.pk, success.pk, old.pk})

    def test_skipped_commands_do_not_count_as_executed(self):
        empty = self.record()
        action = self.command(empty); action.status = 'skipped'; action.save()
        self.assertEqual(self.ids(self.search()), [])
        response = self.search(status='no_action')
        self.assertEqual(self.ids(response), [empty.pk])
        self.assertTrue(response.context['history_filter']['include_no_action'].value())

    def test_child_command_counts_for_parent_and_names_search_snapshots(self):
        root = self.record()
        child = self.record(name='삭제된 창문 닫기', root_run=root)
        self.command(child, function='move_left')
        self.command(child, function='stop')
        response = self.search(tasks=['삭제된 창문 닫기'])
        self.assertEqual(self.ids(response), [root.pk])
        self.assertEqual(self.ids(self.search(q='삭제된 창문 닫기')), [root.pk])
        self.assertEqual(self.ids(self.search(exclude_tasks=['삭제된 창문 닫기'])), [])

    def test_multiple_values_or_and_fields_and(self):
        manual = self.record(source='manual'); self.command(manual)
        scheduled = self.record(name='예약 작업', source='scheduler'); self.command(scheduled, self.aircon)
        self.assertEqual(set(self.ids(self.search(tasks=['온도 관리', '예약 작업']))), {manual.pk, scheduled.pk})
        self.assertEqual(self.ids(self.search(tasks=['온도 관리', '예약 작업'], source=['manual'], devices=[self.window.pk])), [manual.pk])

    def test_device_and_function_must_match_same_command(self):
        root = self.record()
        self.command(root, self.window, 'move_left')
        self.command(root, self.aircon, 'power_off')
        self.assertEqual(self.ids(self.search(devices=[self.window.pk], functions=['power_off'])), [])
        self.assertEqual(self.ids(self.search(devices=[self.window.pk], functions=['move_left', 'power_off'])), [root.pk])

    def test_free_text_device_function_label_and_child_error(self):
        root = self.record(status='failed')
        child = self.record(name='하위 작업', status='failed', root_run=root, message='연결 시간 초과')
        self.command(child, self.aircon)
        for text in ['에어컨', 'power_off', '전원 끄기', '시간 초과']:
            self.assertEqual(self.ids(self.search(q=text)), [root.pk])

    def test_node_error_text_search(self):
        import uuid
        root = self.record(status='failed')
        child = self.record(root_run=root, status='failed')
        AutomationNodeRun.objects.create(automation_run=child, node_key=uuid.uuid4(), node_type='condition', message='센서 응답 없음')
        self.assertEqual(self.ids(self.search(q='센서 응답')), [root.pk])

    def test_run_id_resolves_child_and_duration_excludes_unfinished(self):
        root = self.record(finished_at=self.now + timedelta(seconds=12.5)); self.command(root)
        child = self.record(root_run=root)
        self.record(status='running')
        self.assertEqual(self.ids(self.search(run_id=child.pk)), [root.pk])
        self.assertEqual(self.ids(self.search(min_duration='12.5')), [root.pk])
        self.assertEqual(self.ids(self.search(min_duration='12.501')), [])

    def test_recent_hour_and_week_are_rolling_ranges(self):
        recent = self.record(status='failed')
        old = self.record(status='failed'); old.started_at -= timedelta(hours=2); old.save()
        stale = self.record(status='failed'); stale.started_at -= timedelta(days=8); stale.save()
        future = self.record(status='failed'); future.started_at += timedelta(minutes=1); future.save()
        with patch('iotcore.execution_history.timezone.now', return_value=self.now):
            self.assertEqual(self.ids(self.search(period='hour')), [recent.pk])
            self.assertEqual(set(self.ids(self.search(period='week'))), {recent.pk, old.pk})

    def test_invalid_filters_fail_closed(self):
        self.record(status='failed')
        for params in [{'run_id':'-1'}, {'min_duration':'nan'}, {'min_duration':'-1'}, {'source':['bogus']}, {'devices':['999999']}, {'period':'bogus'}, {'q':'x'*201}]:
            response = self.search(**params)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['history_filter'].errors)
            self.assertEqual(response.context['history_page'].paginator.count, 0)

    def test_pagination_and_chip_removal_preserve_other_values_and_escape(self):
        for _ in range(51): self.record(status='failed')
        response = self.search(source=['manual', 'scheduler'], page=2)
        self.assertEqual(len(self.ids(response)), 1)
        chips = response.context['history_chips']
        self.assertFalse(any(chip['label'] == '기간: 전체 기간' for chip in chips))
        chip = next(chip for chip in chips if chip['label'] == '실행 계기: 수동')
        self.assertIn('source=scheduler', chip['query'])
        self.assertNotIn('source=manual', chip['query'])
        self.assertNotIn('page=', chip['query'])
        self.assertContains(self.search(q='<img src=x onerror=alert(1)>'), '&lt;img')

    def test_detail_highlights_matching_child_and_preserves_query(self):
        root = self.record()
        child = self.record(name='창문 닫기', root_run=root)
        self.command(child, function='move_left', started_at=self.now)
        response = self.search(devices=[self.window.pk])
        self.assertContains(response, 'devices=')
        response = self.client.get(reverse('iotcore:execution_history_detail', args=[root.pk]), {'devices':self.window.pk})
        self.assertContains(response, 'history-search-match')
        self.assertContains(response, '검색 일치')

    def test_history_search_never_calls_device_control(self):
        root = self.record(); self.command(root)
        with patch('iotcore.device.services.device_service.DeviceService.execute_step', side_effect=AssertionError('Search must be read only')):
            self.assertEqual(self.ids(self.search(q='창문', devices=[self.window.pk])), [root.pk])
