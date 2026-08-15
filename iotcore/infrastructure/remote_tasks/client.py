import json
import threading
import time
import uuid

import paho.mqtt.client as mqtt
from django.conf import settings
from django.utils import timezone


class RemoteTaskClient:
    """Send one command to a device agent and wait for its correlated result."""

    @classmethod
    def execute(cls, action, parameters=None, agent_id=None, timeout=None):
        result = cls.execute_result(
            action=action,
            parameters=parameters,
            agent_id=agent_id,
            timeout=timeout,
        )
        success = bool(result.get("success"))
        message = result.get("message") or (
            "원격 작업이 완료되었습니다."
            if success
            else "원격 작업이 실패했습니다."
        )
        return success, message

    @classmethod
    def execute_result(cls, action, parameters=None, agent_id=None, timeout=None):
        agent_id = agent_id or getattr(settings, "IOTCORE_PI_AGENT_ID", "pi5")
        timeout = float(
            timeout
            if timeout is not None
            else getattr(settings, "IOTCORE_REMOTE_TASK_TIMEOUT", 180)
        )
        topic_prefix = getattr(
            settings,
            "IOTCORE_REMOTE_TASK_TOPIC_PREFIX",
            "iotcore/agents",
        ).rstrip("/")
        request_id = uuid.uuid4().hex
        command_topic = f"{topic_prefix}/{agent_id}/commands"
        result_topic = f"{topic_prefix}/{agent_id}/results/{request_id}"
        completed = threading.Event()
        subscribed = threading.Event()
        response = {}

        def on_connect(client, userdata, flags, reason_code, properties=None):
            client.subscribe(result_topic, qos=1)

        def on_message(client, userdata, message):
            try:
                data = json.loads(message.payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                response.update(
                    success=False,
                    message="Pi 에이전트가 올바르지 않은 응답을 보냈습니다.",
                )
            else:
                response.update(data)
            completed.set()

        def on_subscribe(client, userdata, mid, reason_codes, properties=None):
            subscribed.set()

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.on_connect = on_connect
        client.on_message = on_message
        client.on_subscribe = on_subscribe

        try:
            client.connect(settings.MQTT_HOST, settings.MQTT_PORT, 60)
            client.loop_start()
            deadline = time.monotonic() + min(timeout, 10)
            while not client.is_connected() and time.monotonic() < deadline:
                time.sleep(0.05)
            if not client.is_connected():
                return {"success": False, "message": "MQTT 브로커에 연결하지 못했습니다."}
            if not subscribed.wait(min(timeout, 10)):
                return {"success": False, "message": "MQTT 결과 토픽 구독 시간이 초과되었습니다."}

            payload = {
                "request_id": request_id,
                "action": action,
                "parameters": parameters or {},
                "requested_at": timezone.now().isoformat(),
            }
            publish_result = client.publish(
                command_topic,
                json.dumps(payload, ensure_ascii=False),
                qos=1,
            )
            if publish_result.rc != mqtt.MQTT_ERR_SUCCESS:
                return {"success": False, "message": f"원격 작업 요청 전송에 실패했습니다. (rc={publish_result.rc})"}
            if not completed.wait(timeout):
                return {"success": False, "message": f"Pi 에이전트 응답 시간이 초과되었습니다. ({timeout:g}초)"}
        except OSError as exc:
            return {"success": False, "message": f"MQTT 통신에 실패했습니다. ({exc})"}
        finally:
            client.loop_stop()
            client.disconnect()

        if "success" not in response:
            response["success"] = False
        if not response.get("message"):
            response["message"] = (
                "원격 작업이 완료되었습니다."
                if response.get("success")
                else "원격 작업이 실패했습니다."
            )
        return response
