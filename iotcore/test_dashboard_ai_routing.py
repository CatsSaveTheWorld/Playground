from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .ai.routing import assign_role
from .models import AIServiceRoute, Device
from .monitoring.routing import routing_summary
from .monitoring.service import NodeTelemetryService


@override_settings(VOICE_ASR_ENABLED=True, VOICE_NLP_ENABLED=True,
                   VOICE_ASR_BASE_URL='http://192.168.0.4:6101',
                   VOICE_NLP_BASE_URL='http://192.168.0.4:6201',
                   IOTCORE_AI_NODE_UID='home-ai-main')
class DashboardAIRoutingTests(TestCase):
    def setUp(self):
        self.main, _ = Device.objects.update_or_create(device_uid='home-ai-main', defaults={
            'name': 'Home-AI-Main', 'device_type': 'pc', 'control_config': {'ip_address': '192.168.0.4'}})
        self.sub, _ = Device.objects.update_or_create(device_uid='home-ai-sub1', defaults={
            'name': 'Home-AI-Sub1', 'device_type': 'pc', 'control_config': {'ip_address': '192.168.0.2'}})
        for service in ('asr', 'nlp', 'tts'):
            AIServiceRoute.objects.create(service=service, configured=True,
                active_device=self.sub, standby_device=self.main)
        user = get_user_model().objects.create_user('dashboard-viewer', is_staff=True)
        self.client.force_login(user)

    def rows(self):
        return {row['service']: row for row in routing_summary()}

    def test_each_service_uses_its_saved_route_and_port(self):
        AIServiceRoute.objects.filter(pk='nlp').update(active_device=self.main, standby_device=self.sub)
        rows = self.rows()
        self.assertEqual(rows['asr']['active'], {'name': self.sub.name, 'address': '192.168.0.2:6101'})
        self.assertEqual(rows['nlp']['active'], {'name': self.main.name, 'address': '192.168.0.4:6201'})
        self.assertEqual(rows['asr']['standby']['name'], self.main.name)
        self.assertEqual(rows['tts']['state'], 'pending')
        self.assertEqual(rows['tts']['active']['name'], self.sub.name)
        self.assertEqual(rows['tts']['active']['address'], '')

    def test_missing_standby_and_disabled_failover_are_explicit(self):
        AIServiceRoute.objects.filter(pk='asr').update(standby_device=None)
        AIServiceRoute.objects.filter(pk='nlp').update(failover_enabled=False)
        rows = self.rows()
        self.assertEqual(rows['asr']['standby']['name'], '미지정')
        self.assertEqual(rows['asr']['note'], '스탠바이 미지정')
        self.assertEqual(rows['nlp']['note'], '자동 전환 꺼짐')

    def test_unconfigured_route_shows_effective_legacy_target(self):
        AIServiceRoute.objects.filter(pk='asr').update(configured=False)
        AIServiceRoute.objects.filter(pk='nlp').delete()
        rows = self.rows()
        self.assertEqual(rows['asr']['active']['name'], self.main.name)
        self.assertEqual(rows['asr']['state'], 'legacy')
        self.assertEqual(rows['asr']['standby']['name'], '미지정')
        self.assertEqual(rows['nlp']['active']['address'], '192.168.0.4:6201')

    @override_settings(VOICE_ASR_ENABLED=False)
    def test_disabled_and_invalid_targets_are_not_reported_as_ready(self):
        self.sub.control_config = {}
        self.sub.save()
        rows = self.rows()
        self.assertEqual(rows['asr']['state'], 'disabled')
        self.assertEqual(rows['nlp']['state'], 'unassigned')
        self.assertEqual(rows['nlp']['active']['address'], '주소 확인 필요')
        self.assertEqual(rows['nlp']['note'], '액티브 PC 주소 확인 필요')

    def test_deleted_active_is_not_replaced_by_legacy_setting(self):
        self.sub.delete()
        row = self.rows()['asr']
        self.assertEqual(row['active']['name'], '미지정')
        self.assertEqual(row['state'], 'unassigned')
        self.assertEqual(row['standby']['name'], self.main.name)

    @patch('iotcore.api.views.base.KmaWeatherService.snapshot', return_value={})
    @patch('iotcore.api.views.base.RoomEntryService.snapshot', return_value={})
    def test_offline_dashboard_and_detail_render_escaped_routes(self, *_):
        self.sub.name = '<script>worker()</script>'
        self.sub.save()
        for page in ('iotcore:dashboard', 'iotcore:ai_monitor'):
            response = self.client.get(reverse(page))
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.context['ai_node']['online'])
            self.assertContains(response, '모델 사용 현황')
            self.assertNotContains(response, 'data-route-active-address')
            self.assertNotContains(response, 'data-route-standby-address')
            self.assertContains(response, 'data-ai-node-tab="home-ai-main"')
            self.assertContains(response, 'data-ai-node-tab="home-ai-sub1"')
            self.assertContains(response, 'data-ai-route="tts"')
            self.assertContains(response, '&lt;script&gt;worker()&lt;/script&gt;')
            self.assertNotContains(response, '<script>worker()</script>')
            self.assertNotContains(response, 'data-route-last')
            self.assertNotContains(response, 'data-ai-history')
            self.assertNotContains(response, 'data-ai-service=')

    def test_service_status_and_current_usage_follow_active_pc(self):
        AIServiceRoute.objects.filter(pk='nlp').update(active_device=self.main, standby_device=self.sub)
        NodeTelemetryService.record_sample('iotcore/nodes/home-ai-sub1/telemetry', {
            'cpu_percent': 20, 'memory_percent': 30,
            'network': {'download_mbps': 0, 'upload_mbps': 0},
            'ai': {'services': {'asr': {'status': 'ready', 'model': 'medium', 'age_seconds': 0},
                                'nlp': {'status': 'ready', 'engine': 'sub-engine', 'age_seconds': 0}}}})
        rows = self.rows()
        self.assertEqual(rows['asr']['status'], 'ready')
        self.assertEqual(rows['asr']['model'], 'medium')
        self.assertEqual(rows['asr']['usage'], '처리 중 0건')
        self.assertEqual(rows['nlp']['status'], 'unknown')
        self.assertNotEqual(rows['nlp']['model'], 'sub-engine')
        self.assertNotIn('last_text', rows['asr'])

    @patch('iotcore.api.views.base.KmaWeatherService.snapshot', return_value={})
    @patch('iotcore.api.views.base.RoomEntryService.snapshot', return_value={})
    def test_both_pc_resources_are_independent_and_routes_stay_shared(self, *_):
        for uid, cpu, ram, gpu in [('home-ai-main', 10, 20, 30), ('home-ai-sub1', 70, 80, 90)]:
            NodeTelemetryService.record_sample(f'iotcore/nodes/{uid}/telemetry', {
                'cpu_percent': cpu, 'memory_percent': ram,
                'network': {'download_mbps': cpu, 'upload_mbps': 1},
                'ai': {'gpu': {'status': 'available', 'age_seconds': 0,
                    'devices': [{'name': uid, 'utilization_percent': gpu}]}}})
        nodes = self.client.get(reverse('iotcore:dashboard_node_metrics')).json()['nodes']
        self.assertEqual(nodes['home-ai-main']['current']['cpu_percent'], 10)
        self.assertEqual(nodes['home-ai-sub1']['current']['cpu_percent'], 70)
        self.assertEqual(nodes['home-ai-main']['current']['memory_percent'], 20)
        self.assertEqual(nodes['home-ai-sub1']['ai']['gpu']['devices'][0]['utilization_percent'], 90)
        self.assertEqual(nodes['home-ai-main']['ai']['routing'], nodes['home-ai-sub1']['ai']['routing'])
        self.assertEqual(nodes['home-ai-main']['ai']['routing'][0]['active']['name'], self.sub.name)

    @patch('iotcore.api.views.base.KmaWeatherService.snapshot', return_value={})
    @patch('iotcore.api.views.base.RoomEntryService.snapshot', return_value={})
    def test_existing_poll_endpoint_reads_new_assignment(self, *_):
        url = reverse('iotcore:dashboard_node_metrics')
        def asr():
            node = self.client.get(url).json()['nodes']['home-ai-main']
            return next(row for row in node['ai']['routing'] if row['service'] == 'asr')
        self.assertEqual(asr()['active']['name'], self.sub.name)
        assign_role('asr', self.main.pk, 'active', revision=1)
        self.assertEqual(asr()['active']['name'], self.main.name)
        self.assertEqual(asr()['standby']['name'], self.sub.name)
