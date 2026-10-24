import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse

from .device.services.device_service import DeviceService
from .infrastructure.pc_control import command, encoded, signed
from .infrastructure.pc_shutdown import PCShutdownClient
from .models import Device


class PCMQTTIntegrationTests(TestCase):
    def setUp(self):
        self.pc, _ = Device.objects.update_or_create(device_uid='home-ai-main', defaults={
            'name': 'Main', 'device_type': 'pc', 'protocol': 'tcpip', 'device_role': 'hybrid',
            'location': '내 방', 'control_config': {'shutdown_transport': 'mqtt'}})
        self.client.force_login(get_user_model().objects.create_user(username='pc-control-user'))

    @patch('iotcore.device.services.device_service.requests.post')
    @patch.object(PCShutdownClient, 'execute', return_value=(True, '종료 요청 접수'))
    def test_button_uses_server_uid_and_no_http(self, execute, post):
        response = self.client.post(reverse('iotcore:pc_power_off'),
            {'device_id': self.pc.pk, 'node_uid': 'home-ai-sub1', 'dry_run': 'true', 'pc_ip': '1.2.3.4'})
        self.assertEqual(response.status_code, 200)
        execute.assert_called_once_with('home-ai-main')
        post.assert_not_called()
        self.assertIsNone(DeviceService._get_power_state(self.pc))

    @patch('iotcore.device.services.device_service.requests.post')
    @patch.object(PCShutdownClient, 'execute', return_value=(False, 'PC 종료 응답 시간 초과'))
    def test_failure_never_falls_back_or_changes_power(self, execute, post):
        self.assertEqual(DeviceService.control(self.pc.pk, 'power_off'), (False, 'PC 종료 응답 시간 초과'))
        post.assert_not_called()
        self.assertIsNone(DeviceService._get_power_state(self.pc))


class SenderTests(SimpleTestCase):
    key = 'ab'*32

    def mock_client(self, factory, mode='ok'):
        client = factory.return_value
        success = SimpleNamespace(is_failure=False)
        client.subscribe.return_value = (0, 7)
        client.publish.return_value = SimpleNamespace(rc=0)

        def loop_start():
            client.on_connect(client, None, None, 0, None)
            client.on_subscribe(client, None, 7, [success], None)
        client.loop_start.side_effect = loop_start

        def publish(topic, payload, **kwargs):
            request = json.loads(payload)
            self.assertEqual(topic, 'iotcore/pc/home-ai-main/commands')
            self.assertFalse(kwargs['retain'])
            self.assertEqual(kwargs['qos'], 1)
            result = signed({k: request[k] for k in ('version','request_id','node_uid','action','dry_run')}
                            | {'accepted': True}, self.key)
            response_topic = f"iotcore/pc/home-ai-main/results/{request['request_id']}"
            if mode == 'forged':
                result['signature'] = '0'*64
            if mode == 'wrong_target':
                result = signed(result | {'node_uid': 'home-ai-sub1'}, self.key)
            client.on_message(client, None, SimpleNamespace(topic=response_topic,
                payload=encoded(result), retain=mode == 'retained'))
            return SimpleNamespace(rc=0)
        client.publish.side_effect = publish
        return client

    @patch('iotcore.infrastructure.pc_shutdown.mqtt.Client')
    def test_signed_dry_run_response(self, factory):
        client = self.mock_client(factory)
        ok, message = PCShutdownClient._send('home-ai-main', self.key, True, 'broker', 1884, 'user', 'pass', .02)
        self.assertTrue(ok, message)
        self.assertIn('PC는 종료하지 않았습니다', message)
        client.loop_stop.assert_called_once()

    @patch('iotcore.infrastructure.pc_shutdown.mqtt.Client')
    def test_rejects_forged_retained_and_wrong_target_results(self, factory):
        for mode in ('forged', 'retained', 'wrong_target'):
            with self.subTest(mode=mode):
                self.mock_client(factory, mode)
                ok, message = PCShutdownClient._send('home-ai-main', self.key, True, 'broker', 1884, 'u', 'p', .02)
                self.assertFalse(ok)
                self.assertIn('시간 초과', message)

    @patch('iotcore.infrastructure.pc_shutdown.mqtt.Client')
    def test_subscription_denied_does_not_publish(self, factory):
        client = factory.return_value
        client.subscribe.return_value = (0, 7)
        def start():
            client.on_connect(client, None, None, 0, None)
            client.on_subscribe(client, None, 7, [SimpleNamespace(is_failure=True)], None)
        client.loop_start.side_effect = start
        ok, message = PCShutdownClient._send('home-ai-main', self.key, False, 'broker', 1884, 'u', 'p', .02)
        self.assertFalse(ok)
        client.publish.assert_not_called()

    def test_config_missing_fails_closed_and_unknown_pc_rejected(self):
        with override_settings(PC_CONTROL_CONFIG='does-not-exist.private.json'):
            self.assertFalse(PCShutdownClient.execute('home-ai-main')[0])
        self.assertFalse(PCShutdownClient.execute('unknown')[0])
