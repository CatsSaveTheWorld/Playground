import json

from ..mqtt.client import MQTTClient


class ZigbeeClient:
    @staticmethod
    def send_payload(device_uid, payload):
        """Publish an arbitrary Zigbee2MQTT `/set` payload."""
        topic = f"zigbee2mqtt/{device_uid}/set"
        return MQTTClient.publish(
            topic=topic,
            payload=json.dumps(payload),
        )

    @staticmethod
    def send_zigbee_request(device_uid, state):
        # Backward-compatible helper used by simple ON/OFF style devices.
        return ZigbeeClient.send_payload(
            device_uid,
            {"state": state},
        )
