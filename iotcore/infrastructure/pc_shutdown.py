"""Short-lived, signed MQTT PC shutdown requests; no HTTP fallback/retry."""
import json
import os
from pathlib import Path
import threading
import time
import uuid

from django.conf import settings
import paho.mqtt.client as mqtt

from .pc_control import NODES, command, encoded, valid_result


class PCShutdownClient:
    @classmethod
    def execute(cls, node_uid, *, dry_run=False):
        if node_uid not in NODES or type(dry_run) is not bool:
            return False, '지원하지 않는 PC 종료 대상입니다.'
        path = getattr(settings, 'PC_CONTROL_CONFIG', '') or os.environ.get('IOTCORE_PC_CONTROL_CONFIG')
        path = Path(path) if path else Path.home()/'IoTCoreAI/Telemetry/control.private.json'
        try:
            config = json.loads(path.read_text(encoding='utf-8-sig'))
            broker = config['mqtt']
            key = config['command_keys'][node_uid]
            if len(bytes.fromhex(key)) != 32:
                raise ValueError('Invalid key')
            host, port = str(broker['host']), int(broker['port'])
            username, password = broker['username'], broker['password']
        except (OSError, ValueError, KeyError, TypeError):
            return False, 'PC MQTT 제어 설정을 읽을 수 없습니다. 서버 설정을 확인해 주세요.'
        timeout = min(10., max(1., float(getattr(settings, 'PC_CONTROL_TIMEOUT', 8))))
        return cls._send(node_uid, key, dry_run, host, port, username, password, timeout)

    @staticmethod
    def _send(node, key, dry_run, host, port, username, password, timeout):
        request = command(node, key, dry_run)
        result_topic = f"iotcore/pc/{node}/results/{request['request_id']}"
        subscribed, completed = threading.Event(), threading.Event()
        failure = []
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id='iotcore-pc-control-'+uuid.uuid4().hex[:20], protocol=mqtt.MQTTv5)
        client.connect_timeout = min(3., timeout)
        client.username_pw_set(username, password)

        def on_connect(client, userdata, flags, reason, properties):
            if reason != 0:
                failure.append('MQTT 인증 또는 연결이 거부되었습니다.')
                subscribed.set()
                return
            rc, _ = client.subscribe(result_topic,
                options=mqtt.SubscribeOptions(qos=1, retainAsPublished=True, retainHandling=2))
            if rc != mqtt.MQTT_ERR_SUCCESS:
                failure.append('MQTT 응답 구독을 시작할 수 없습니다.')
                subscribed.set()

        def on_subscribe(client, userdata, mid, reasons, properties):
            if not reasons or any(reason.is_failure for reason in reasons):
                failure.append('MQTT 응답 구독 권한이 없습니다.')
            subscribed.set()

        def on_publish(client, userdata, mid, reason, properties):
            if reason.is_failure:
                failure.append('MQTT 종료 명령 전송 권한이 없습니다.')
                completed.set()

        def on_message(client, userdata, message):
            if message.topic != result_topic or message.retain or len(message.payload) > 2048:
                return
            try:
                value = json.loads(message.payload)
                if valid_result(value, request, key):
                    completed.set()
            except (ValueError, TypeError):
                pass

        client.on_connect, client.on_subscribe = on_connect, on_subscribe
        client.on_publish, client.on_message = on_publish, on_message
        deadline = time.monotonic()+timeout
        started = False
        sent = False
        try:
            # Async connect keeps DNS/TCP failures inside the bounded wait.
            client.connect_async(host, port, keepalive=20)
            client.loop_start()
            started = True
            if not subscribed.wait(max(0, deadline-time.monotonic())):
                return False, 'MQTT 브로커에 연결하지 못했습니다. 네트워크와 계정을 확인해 주세요.'
            if failure:
                return False, failure[0]
            properties = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
            properties.MessageExpiryInterval = 20
            result = client.publish(f'iotcore/pc/{node}/commands', encoded(request),
                                    qos=1, retain=False, properties=properties)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                return False, 'MQTT 종료 명령을 전송하지 못했습니다.'
            sent = True
            if not completed.wait(max(0, deadline-time.monotonic())):
                return False, 'PC 종료 응답 시간 초과: PC 상태와 워커 로그를 확인해 주세요. 자동 재전송하지 않습니다.'
            if failure:
                return False, failure[0]
            return True, ('모의 종료 명령 수신을 확인했습니다. PC는 종료하지 않았습니다.' if dry_run
                          else 'PC가 종료 요청을 접수했습니다. 저장하지 않은 작업이 있으면 종료가 지연될 수 있습니다.')
        except (OSError, ValueError, RuntimeError):
            return False, ('MQTT 종료 응답을 확인하지 못했습니다. PC 상태를 확인해 주세요.' if sent
                           else 'MQTT 제어 연결에 실패했습니다. 서버 설정과 네트워크를 확인해 주세요.')
        finally:
            if started:
                client.disconnect()
                client.loop_stop()
