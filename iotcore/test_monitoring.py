from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from .models import Device, NodeMetricSample
from .monitoring.service import NodeTelemetryService


class NodeTelemetryServiceTests(TestCase):
    def setUp(self):
        self.device = Device.objects.create(
            device_uid="monitor-test",
            name="Monitor Test",
            device_type="pc",
            protocol=Device.Protocol.MQTT,
            location="test",
        )

    def test_record_sample_stores_one_row_per_payload(self):
        sample = NodeTelemetryService.record_sample(
            "iotcore/nodes/monitor-test/telemetry",
            {
                "cpu_percent": 12.5,
                "cpu_current_ghz": 1.8,
                "cpu_max_ghz": 2.4,
                "memory_percent": 44.2,
                "memory_used_gb": 3.5,
                "memory_total_gb": 8.0,
                "network": {
                    "download_mbps": 21.3,
                    "upload_mbps": 3.4,
                },
                "storage": {
                    "used_percent": 60.0,
                    "used_gb": 600,
                    "total_gb": 1000,
                },
            },
        )
        self.assertEqual(NodeMetricSample.objects.count(), 1)
        self.assertEqual(sample.device, self.device)
        self.assertEqual(sample.download_mbps, 21.3)
        self.assertEqual(sample.storage_percent, 60.0)
        self.assertEqual(sample.cpu_current_ghz, 1.8)
        self.assertEqual(sample.cpu_max_ghz, 2.4)
        self.assertEqual(sample.memory_used_gb, 3.5)
        self.assertEqual(sample.memory_total_gb, 8.0)

    def test_snapshot_returns_current_and_history(self):
        NodeMetricSample.objects.create(
            device=self.device,
            cpu_percent=10,
            cpu_current_ghz=1.5,
            cpu_max_ghz=2.4,
            memory_percent=20,
            memory_used_gb=1.6,
            memory_total_gb=8.0,
            download_mbps=3,
            upload_mbps=1,
            storage_percent=40,
            storage_used_gb=40,
            storage_total_gb=100,
        )
        snapshot = NodeTelemetryService.snapshot(
            "monitor-test",
            history_seconds=60,
        )
        self.assertTrue(snapshot["online"])
        self.assertEqual(snapshot["current"]["cpu_percent"], 10)
        self.assertEqual(snapshot["current"]["cpu_current_ghz"], 1.5)
        self.assertEqual(snapshot["current"]["cpu_max_ghz"], 2.4)
        self.assertEqual(snapshot["current"]["memory_used_gb"], 1.6)
        self.assertEqual(snapshot["current"]["memory_total_gb"], 8.0)
        self.assertEqual(len(snapshot["history"]), 1)

    def test_old_sample_is_offline(self):
        sample = NodeMetricSample.objects.create(
            device=self.device,
            cpu_percent=10,
            memory_percent=20,
            download_mbps=0,
            upload_mbps=0,
        )
        NodeMetricSample.objects.filter(pk=sample.pk).update(
            recorded_at=timezone.now() - timedelta(minutes=1)
        )
        snapshot = NodeTelemetryService.snapshot("monitor-test")
        self.assertFalse(snapshot["online"])
