import json
import paho.mqtt.client as mqtt
from django.conf import settings


class MQTTClient:

    HOST = settings.MQTT_HOST
    PORT = settings.MQTT_PORT

    _client = mqtt.Client()

    @classmethod
    def connect(cls):
        cls._client.connect(cls.HOST, cls.PORT, 60)
        cls._client.loop_start()

    @classmethod
    def publish(cls, topic, payload):

        if isinstance(payload, dict):
            payload = json.dumps(payload)

        result = cls._client.publish(topic, payload)

        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            return True, None

        return False, f"MQTT Publish 실패 (rc={result.rc})"

    @classmethod
    def subscribe(cls, topic):
        cls._client.subscribe(topic)