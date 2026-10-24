from types import SimpleNamespace
from unittest.mock import patch
import json
from django.test import TestCase
from iotcore.models import Device, NodeMetricSample
from iotcore.management.commands.run_node_telemetry_listener import ingest


class TelemetryReceiverTests(TestCase):
    def setUp(self):
        Device.objects.update_or_create(device_uid='home-ai-main',defaults={'name':'Main','device_type':'pc'})

    def message(self, **changes):
        data={'topic':'iotcore/nodes/home-ai-main/telemetry','retain':False,
              'payload':json.dumps({'cpu_percent':10,'memory_percent':20,
                  'network':{'download_mbps':1,'upload_mbps':2}}).encode()}
        return SimpleNamespace(**{**data,**changes})

    def test_saves_resource_sample_without_automation_execution(self):
        with patch('iotcore.scheduler.service.AutomationService.process_event') as automate:
            self.assertTrue(ingest(self.message()))
        automate.assert_not_called()
        self.assertEqual(NodeMetricSample.objects.get().cpu_percent,10)

    def test_retained_unrelated_and_oversized_data_are_not_ingested(self):
        self.assertFalse(ingest(self.message(retain=True)))
        self.assertFalse(ingest(self.message(topic='zigbee2mqtt/aircon')))
        self.assertFalse(ingest(self.message(payload=b'0'*65537)))
        self.assertFalse(NodeMetricSample.objects.exists())
