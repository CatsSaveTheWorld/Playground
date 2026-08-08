import json

import paho.mqtt.client as mqtt
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from ...monitoring.service import NodeTelemetryService
from ...scheduler.service import AutomationService


class Command(BaseCommand):
    help = "Listen for MQTT sensor events and enqueue matching automations."

    def add_arguments(self, parser):
        parser.add_argument("--topic", default="#")

    def handle(self, *args, **options):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        username = str(getattr(settings, "MQTT_USERNAME", "")).strip()
        password = str(getattr(settings, "MQTT_PASSWORD", ""))
        if username:
            client.username_pw_set(username, password)

        def on_connect(mqtt_client, userdata, flags, reason_code, properties=None):
            if reason_code != 0:
                self.stderr.write(f"MQTT 연결 실패: {reason_code}")
                return
            mqtt_client.subscribe(options["topic"], qos=1)
            self.stdout.write(f"센서 이벤트 대기 중: {options['topic']}")

        def on_message(mqtt_client, userdata, message):
            try:
                decoded = message.payload.decode("utf-8")
                try:
                    payload = json.loads(decoded)
                except json.JSONDecodeError:
                    payload = {"value": decoded}

                if not isinstance(payload, dict):
                    payload = {"value": payload}

                close_old_connections()

                # Zigbee2MQTT 자체 관리/메타데이터는 DeviceState에 저장하지 않는다.
                if message.topic.startswith("zigbee2mqtt/bridge/"):
                    return

                # 1초 단위 시스템 telemetry는 범용 DeviceState/예약 실행 경로를
                # 거치지 않고 전용 시계열 테이블에 한 행으로 저장한다.
                if NodeTelemetryService.is_telemetry_topic(message.topic):
                    # retained telemetry는 오래된 값을 새 샘플처럼 재삽입할 수 있으므로 무시한다.
                    if not message.retain:
                        NodeTelemetryService.record_sample(
                            message.topic,
                            payload,
                        )
                    return

                if message.retain:
                    AutomationService.update_device_state(
                        message.topic,
                        payload,
                    )
                    self.stdout.write(
                        f"{message.topic}: retained 상태 동기화"
                    )
                    return

                runs = AutomationService.process_event(
                    message.topic,
                    payload,
                )
                if runs:
                    self.stdout.write(
                        f"{message.topic}: {len(runs)}개 예약 실행 요청 등록"
                    )

            except Exception as exc:
                self.stderr.write(
                    f"{message.topic} 이벤트 처리 실패: "
                    f"{type(exc).__name__}: {exc}"
                )
            finally:
                close_old_connections()
                
        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(settings.MQTT_HOST, settings.MQTT_PORT, 60)
        client.loop_forever(retry_first_connection=True)
