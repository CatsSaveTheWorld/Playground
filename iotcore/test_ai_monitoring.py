from datetime import timedelta
import importlib.util
import io
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock, patch
from urllib.error import URLError

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .ai.asr_client import ASRClient
from .ai.nlp_client import NLPClient
from .models import AIInferenceEvent, Automation, Device, NodeMetricSample
from .monitoring.inference import AIInferenceMonitor
from .monitoring.service import NodeTelemetryService


@override_settings(VOICE_ASR_ENABLED=True, VOICE_NLP_ENABLED=True,
                   VOICE_ASR_BASE_URL='http://asr.invalid', VOICE_NLP_BASE_URL='http://nlp.invalid',
                   IOTCORE_AI_NODE_UID='monitor-ai', VOICE_SAVE_UPLOADS=False)
class AIMonitorTests(TestCase):
    def setUp(self):
        self.device = Device.objects.create(device_uid='monitor-ai', name='AI test', device_type='pc',
                                            protocol=Device.Protocol.MQTT, location='test')
        self.payload = {'cpu_percent': 10, 'memory_percent': 30,
                        'network': {'download_mbps': 0, 'upload_mbps': 0},
                        'ai': {'services': {'asr': {'status': 'ready', 'age_seconds': 0, 'model': 'medium'},
                                            'nlp': {'status': 'ready', 'age_seconds': 0, 'engine': 'rule-v0'}},
                               'gpu': {'status': 'available', 'age_seconds': 0,
                                       'devices': [{'name': 'GPU', 'memory_used_mb': 100, 'memory_total_mb': 1000,
                                                    'utilization_percent': 20, 'temperature_c': 40}]}}}

    def snapshot(self):
        return AIInferenceMonitor.snapshot(NodeTelemetryService.snapshot('monitor-ai'))

    def sample(self):
        return NodeTelemetryService.record_sample('iotcore/nodes/monitor-ai/telemetry', self.payload)

    def test_gpu_and_service_metadata_round_trip(self):
        self.sample()
        ai = self.snapshot()
        self.assertEqual(ai['services']['asr']['status'], 'ready')
        self.assertEqual(ai['services']['nlp']['engine'], 'rule-v0')
        self.assertEqual(ai['gpu']['devices'][0]['memory_used_mb'], 100)

    def test_old_agent_payload_remains_compatible(self):
        del self.payload['ai']
        self.sample()
        ai = self.snapshot()
        self.assertEqual(ai['services']['asr']['status'], 'unknown')
        self.assertFalse(ai['gpu']['devices'])

    def test_fresh_metrics_do_not_renew_old_health(self):
        self.payload['ai']['services']['asr']['age_seconds'] = 60
        self.sample()
        self.assertTrue(NodeTelemetryService.snapshot('monitor-ai')['online'])
        self.assertEqual(self.snapshot()['services']['asr']['status'], 'unknown')
        self.assertEqual(self.snapshot()['services']['nlp']['status'], 'ready')

    def test_metric_disconnect_clears_ready_and_gpu(self):
        sample = self.sample()
        NodeMetricSample.objects.filter(pk=sample.pk).update(recorded_at=timezone.now()-timedelta(minutes=1))
        ai = self.snapshot()
        self.assertEqual(ai['services']['asr']['status'], 'unknown')
        self.assertEqual(ai['gpu']['devices'], [])

    def test_invalid_optional_gpu_numbers_are_not_zero(self):
        gpu = self.payload['ai']['gpu']['devices'][0]
        gpu.update(utilization_percent=float('nan'), memory_used_mb=-1, temperature_c='N/A')
        self.sample()
        saved = self.snapshot()['gpu']['devices'][0]
        self.assertIsNone(saved['utilization_percent'])
        self.assertIsNone(saved['memory_used_mb'])
        self.assertIsNone(saved['temperature_c'])

    @override_settings(VOICE_NLP_ENABLED=False)
    def test_disabled_service_is_distinct_from_unreachable(self):
        self.payload['ai']['services']['asr']['status'] = 'unreachable'
        self.sample()
        self.assertEqual(self.snapshot()['services']['asr']['status'], 'unreachable')
        self.assertEqual(self.snapshot()['services']['nlp']['status'], 'disabled')

    def test_active_request_is_processing_and_orphan_request_expires(self):
        event = AIInferenceMonitor.start('asr', 'a'*32)
        self.assertEqual(self.snapshot()['services']['asr']['status'], 'processing')
        AIInferenceEvent.objects.filter(pk=event.pk).update(started_at=timezone.now()-timedelta(minutes=3))
        self.assertEqual(self.snapshot()['services']['asr']['status'], 'unknown')
        self.assertEqual(self.snapshot()['recent'][0]['status'], 'unknown')

    def test_client_records_failure_without_audio_or_text(self):
        response = Mock(ok=False, status_code=503)
        response.json.return_value = {'detail': {'message': 'model not loaded'}}
        with patch('iotcore.ai.asr_client.requests.post', return_value=response):
            result = ASRClient().transcribe(audio_bytes=b'audio', request_id='b'*32)
        event = AIInferenceEvent.objects.get()
        self.assertFalse(result.ok)
        self.assertEqual(event.status, 'failed')
        self.assertEqual(event.error, 'model not loaded')
        self.assertIsNotNone(event.finished_at)
        self.assertGreaterEqual(event.elapsed_ms, 0)
        self.assertNotIn('text', {field.name for field in event._meta.fields})

    def test_request_is_visible_while_client_is_waiting(self):
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {'ok': True, 'intent': '', 'text': 'test'}
        def send(*args, **kwargs):
            self.assertEqual(self.snapshot()['services']['nlp']['status'], 'processing')
            return response
        with patch('iotcore.ai.nlp_client.requests.post', side_effect=send):
            NLPClient().parse(text='test', request_id='c'*32)
        self.assertEqual(AIInferenceEvent.objects.get().status, 'success')

    def test_monitoring_failure_does_not_break_client(self):
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {'ok': True, 'text': 'ok'}
        with patch('iotcore.monitoring.inference.AIInferenceEvent.objects.create', side_effect=DatabaseError('test')), \
             patch('iotcore.ai.asr_client.requests.post', return_value=response), \
             self.assertLogs('iotcore.monitoring.inference', level='ERROR'):
            result = ASRClient().transcribe(audio_bytes=b'audio', request_id='d'*32)
        self.assertTrue(result.ok)
        self.assertTrue(Device.objects.filter(pk=self.device.pk).exists())

    def test_voice_direct_match_does_not_report_worker_nlp(self):
        automation = Automation.objects.create(name='test routine', automation_type=Automation.Type.IMMEDIATE, enabled=True)
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {'ok': True, 'text': 'test routine'}
        audio = SimpleUploadedFile('test.wav', b'RIFF\x04\x00\x00\x00WAVEpayload', content_type='audio/wav')
        from .api.views.voice import voice_upload
        from django.test import RequestFactory
        request = RequestFactory().post('/voice/', {'audio': audio})
        with patch('iotcore.ai.asr_client.requests.post', return_value=response), \
             patch('iotcore.api.views.voice.AIControlService.run_automation', return_value=Mock(id=123)), \
             patch('iotcore.ai.nlp_client.NLPClient.parse') as parse:
            result = voice_upload(request)
        self.assertEqual(result.status_code, 201)
        parse.assert_not_called()
        self.assertEqual(AIInferenceEvent.objects.get(stage='nlp').route, 'automation_match')
        self.assertEqual(json.loads(result.content)['execution']['automation_id'], automation.pk)

    def test_monitor_pages_require_staff_and_render_escaped_data(self):
        self.payload['ai']['services']['asr'].update(status='error', error='<script>bad()</script>')
        self.sample()
        url = reverse('iotcore:ai_monitor')
        self.assertEqual(self.client.get(url).status_code, 302)
        user = get_user_model().objects.create_user('viewer', password='test')
        self.client.force_login(user)
        self.assertEqual(self.client.get(url).status_code, 302)
        user.is_staff = True
        user.save()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '&lt;script&gt;bad()&lt;/script&gt;')
        self.assertNotContains(response, '<script>bad()</script>')
        with patch('iotcore.api.views.base.KmaWeatherService.snapshot', return_value={}):
            metrics = self.client.get(reverse('iotcore:dashboard_node_metrics')).json()
        self.assertEqual(metrics['nodes']['monitor-ai']['ai']['services']['asr']['status'], 'error')

    def test_retention_purges_old_events_only(self):
        old = AIInferenceMonitor.start('asr', 'e'*32)
        AIInferenceEvent.objects.filter(pk=old.pk).update(started_at=timezone.now()-timedelta(days=2))
        fresh = AIInferenceMonitor.start('nlp', 'f'*32)
        call_command('purge_node_metrics', hours=24, stdout=io.StringIO())
        self.assertFalse(AIInferenceEvent.objects.filter(pk=old.pk).exists())
        self.assertTrue(AIInferenceEvent.objects.filter(pk=fresh.pk).exists())


class AgentProbeTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = Path(__file__).resolve().parents[1] / 'deploy/node_monitor/ai_probe.py'
        spec = importlib.util.spec_from_file_location('ai_probe_tests', path)
        cls.probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.probe)

    def response(self, data):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps(data).encode()
        return response

    def test_ready_and_unloaded_asr_are_distinct(self):
        for loaded, expected in [(True, 'ready'), (False, 'starting')]:
            with patch.object(self.probe, 'urlopen', return_value=self.response(
                    {'service': 'iotcore-asr-worker', 'ok': loaded, 'model_loaded': loaded})):
                self.assertEqual(self.probe.probe_service('asr', 'http://test/health')['status'], expected)

    def test_model_error_and_wrong_service_are_not_ready(self):
        for payload in [{'service': 'iotcore-asr-worker', 'ok': False, 'last_error': 'CUDA DLL missing'},
                        {'service': 'wrong', 'ok': True}]:
            with patch.object(self.probe, 'urlopen', return_value=self.response(payload)):
                self.assertEqual(self.probe.probe_service('asr', 'http://test/health')['status'], 'error')

    def test_service_disconnect_and_missing_url(self):
        with patch.object(self.probe, 'urlopen', side_effect=URLError('refused')):
            self.assertEqual(self.probe.probe_service('nlp', 'http://test/health')['status'], 'unreachable')
        self.assertEqual(self.probe.probe_service('nlp', '')['status'], 'unconfigured')

    def test_gpu_reading_and_timeout(self):
        with patch.object(self.probe.subprocess, 'run', return_value=Mock(stdout='GPU, 20, 100, 1000, 40\n')):
            self.assertEqual(self.probe.probe_gpu()['devices'][0]['temperature_c'], 40)
        with patch.object(self.probe.subprocess, 'run', side_effect=subprocess.TimeoutExpired('nvidia-smi', 2)):
            self.assertEqual(self.probe.probe_gpu()['status'], 'unavailable')

    def test_probe_age_increases_without_new_check(self):
        collector = self.probe.AIProbeCollector({'asr': 'http://test/health'})
        collector._values['asr'] = ({'status': 'ready'}, 100)
        with patch.object(self.probe.time, 'monotonic', return_value=130):
            self.assertEqual(collector.snapshot()['services']['asr']['age_seconds'], 30)
