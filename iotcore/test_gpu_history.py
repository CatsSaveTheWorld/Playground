from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from .models import Device
from .monitoring.service import NodeTelemetryService


class GPUHistoryTests(TestCase):
    def setUp(self):
        self.uid='gpu-history-test'
        Device.objects.create(device_uid=self.uid,name='GPU history',device_type='pc')

    def sample(self,at,age=0,name='GPU A',utilization=0):
        with patch('django.utils.timezone.now',return_value=at):
            return NodeTelemetryService.record_sample('iotcore/nodes/'+self.uid+'/telemetry', {
                'cpu_percent':1,'memory_percent':2,'network':{'download_mbps':3,'upload_mbps':4},
                'ai':{'gpu':{'status':'available','age_seconds':age,'devices':[{
                    'name':name,'utilization_percent':utilization,'memory_used_mb':2048,
                    'memory_total_mb':8192,'temperature_c':35}]}}})

    def test_fresh_historical_probe_is_retained_even_after_current_ttl(self):
        now=timezone.now()
        self.sample(now-timedelta(seconds=30),utilization=0)
        self.sample(now-timedelta(seconds=1),utilization=40)
        result=NodeTelemetryService.snapshot(self.uid,history_seconds=60)
        self.assertEqual(len(result['history']),2)
        self.assertEqual(result['history'][0]['gpu'][0]['utilization_percent'],0)
        self.assertEqual(result['history'][1]['gpu'][0]['utilization_percent'],40)
        self.assertEqual(result['history'][0]['gpu'][0]['memory_used_mb'],2048)
        self.assertEqual(result['history'][0]['upload_mbps'],4)

    def test_stale_and_missing_gpu_produce_gaps_not_zero_values(self):
        now=timezone.now()
        old=self.sample(now-timedelta(seconds=3),age=60)
        missing=self.sample(now-timedelta(seconds=2))
        missing.ai_status={}
        missing.save(update_fields=['ai_status'])
        result=NodeTelemetryService.snapshot(self.uid,history_seconds=60)
        self.assertEqual([point['gpu'] for point in result['history']],[[],[]])

    def test_other_pc_and_outside_window_are_excluded(self):
        now=timezone.now()
        self.sample(now-timedelta(seconds=90),name='expired')
        self.sample(now-timedelta(seconds=1),name='GPU A')
        self.uid='gpu-other-test'
        Device.objects.create(device_uid=self.uid,name='Other',device_type='pc')
        self.sample(now,name='GPU B')
        first=NodeTelemetryService.snapshot('gpu-history-test',history_seconds=60)
        second=NodeTelemetryService.snapshot(self.uid,history_seconds=60)
        self.assertEqual([p['gpu'][0]['name'] for p in first['history']],['GPU A'])
        self.assertEqual([p['gpu'][0]['name'] for p in second['history']],['GPU B'])
