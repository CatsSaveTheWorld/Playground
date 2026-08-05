import json

from ..mqtt.client import MQTTClient


class ZigbeeClient:
    @staticmethod
    def send_zigbee_request(device_uid, state):

        topic = f"zigbee2mqtt/{device_uid}/set"

        payload = {
            "state": state
        }
        # print(f"[DEBUG] topic : {topic}")
        # print(f"[DEBUG] payload : {payload}")

        return MQTTClient.publish(
            topic=topic,
            payload=json.dumps(payload),
        )