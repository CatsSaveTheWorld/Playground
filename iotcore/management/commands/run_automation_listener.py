import json

import paho.mqtt.client as mqtt
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

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
                        f"{message.topic}: {len(runs)}개 자동화 실행 요청 등록"
                    )
            except Exception as exc:
                self.stderr.write(
                    f"{message.topic} 이벤트 처리 실패: {type(exc).__name__}: {exc}"
                )
            finally:
                close_old_connections()

        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(settings.MQTT_HOST, settings.MQTT_PORT, 60)
        client.loop_forever(retry_first_connection=True)
