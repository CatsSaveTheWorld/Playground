"""Development-only telemetry ingestion; never invokes automations."""
import json
import os
from pathlib import Path
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from django.utils import timezone
import paho.mqtt.client as mqtt

from iotcore.monitoring.service import NodeTelemetryService
from iotcore.models import NodeMetricSample

ALLOWED = {'iotcore/nodes/'+uid+'/telemetry' for uid in ('home-ai-main','home-ai-sub1','pi5')}


def ingest(message):
    if message.retain or message.topic not in ALLOWED or len(message.payload)>65536:
        return False
    payload=json.loads(message.payload.decode('utf-8'))
    close_old_connections()
    try:
        NodeTelemetryService.record_sample(message.topic,payload)
        return True
    finally:
        close_old_connections()


class Command(BaseCommand):
    help='Read only PC/Pi telemetry into this Django database; no automation execution.'

    def add_arguments(self, parser):
        parser.add_argument('--config', required=True)

    def handle(self,*args,**options):
        path=Path(options['config']).resolve()
        cfg=json.loads(path.read_text(encoding='utf-8-sig'))
        if cfg.get('node_uid')!='home-ai-main':
            raise CommandError('Development receiver requires the Main telemetry config.')
        broker=cfg['mqtt']
        status=path.parent/'logs/development-listener-status.json'
        state={'pid':os.getpid(),'mqtt_connected':False,'samples_saved':0,'last_error':''}
        client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
            client_id='iotcore-development-node-telemetry',protocol=mqtt.MQTTv5)
        client.username_pw_set(broker['username'],broker['password'])

        def on_connect(client,userdata,flags,reason,properties):
            state['mqtt_connected']=reason==0
            state['last_error']='' if reason==0 else 'MQTT connect: '+str(reason)
            if reason==0:
                client.subscribe([(topic,1) for topic in sorted(ALLOWED)])

        def on_disconnect(client,userdata,flags,reason,properties):
            state['mqtt_connected']=False

        def on_message(client,userdata,message):
            try:
                if ingest(message):
                    state['samples_saved']+=1
                    state['last_sample_at']=timezone.now().isoformat()
                    state['last_topic']=message.topic
                    state['last_error']=''
            except Exception as exc:
                state['last_error']='Telemetry ingest failed: '+type(exc).__name__

        client.on_connect,client.on_disconnect,client.on_message=on_connect,on_disconnect,on_message
        client.reconnect_delay_set(min_delay=1,max_delay=30)
        client.connect_async(broker['host'],int(broker['port']),30)
        client.loop_start()
        next_cleanup=time.monotonic()+3600
        try:
            while True:
                state['updated_at']=timezone.now().isoformat()
                temp=status.with_suffix('.tmp')
                temp.write_text(json.dumps(state,indent=2),encoding='utf-8')
                temp.replace(status)
                if time.monotonic()>=next_cleanup:
                    # Only raw one-second resource samples; never inference or execution records.
                    from datetime import timedelta
                    close_old_connections()
                    NodeMetricSample.objects.filter(recorded_at__lt=timezone.now()-timedelta(hours=24)).delete()
                    close_old_connections()
                    next_cleanup=time.monotonic()+3600
                time.sleep(5)
        finally:
            client.disconnect()
            client.loop_stop()
