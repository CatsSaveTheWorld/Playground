#!/usr/bin/env python3
"""Pi agent for collecting a YouTube Music cookie and updating Music Assistant."""

from __future__ import annotations

from collections import OrderedDict
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any
import uuid

import paho.mqtt.client as mqtt
import requests


ACTION = "ytmusic.refresh_cookie"
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class AgentError(RuntimeError):
    pass


class Config:
    mqtt_host = os.environ.get("IOTCORE_MQTT_HOST", "192.168.0.8")
    mqtt_port = int(os.environ.get("IOTCORE_MQTT_PORT", "1883"))
    mqtt_username = os.environ.get("IOTCORE_MQTT_USERNAME", "")
    mqtt_password_file = Path(
        os.environ.get(
            "IOTCORE_MQTT_PASSWORD_FILE",
            "/home/leedowon/.config/ytmusic-cookie-agent/mqtt-password",
        )
    )
    agent_id = os.environ.get("IOTCORE_AGENT_ID", "pi5")
    topic_prefix = os.environ.get("IOTCORE_TOPIC_PREFIX", "iotcore/agents").rstrip("/")
    collector = Path(
        os.environ.get(
            "YTMUSIC_COLLECTOR",
            "/home/leedowon/ytmusic-cookie-collector/collector.py",
        )
    )
    cookie_file = Path(
        os.environ.get(
            "YTMUSIC_COOKIE_FILE",
            "/home/leedowon/.local/state/ytmusic-cookie-collector/login_cookie",
        )
    )
    collector_timeout = int(os.environ.get("YTMUSIC_COLLECTOR_TIMEOUT", "60"))
    mass_url = os.environ.get("MASS_URL", "http://127.0.0.1:8095").rstrip("/")
    mass_token_file = Path(
        os.environ.get(
            "MASS_TOKEN_FILE",
            "/home/leedowon/.config/ytmusic-cookie-agent/music-assistant-token",
        )
    )
    mass_timeout = int(os.environ.get("MASS_TIMEOUT", "30"))

    @classmethod
    def command_topic(cls) -> str:
        return f"{cls.topic_prefix}/{cls.agent_id}/commands"

    @classmethod
    def result_topic(cls, request_id: str) -> str:
        return f"{cls.topic_prefix}/{cls.agent_id}/results/{request_id}"


class MusicAssistant:
    def __init__(self, url: str, token: str, timeout: int):
        self.url = url
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def command(self, command: str, args: dict[str, Any]) -> Any:
        try:
            response = requests.post(
                f"{self.url}/api",
                headers=self.headers,
                json={
                    "message_id": uuid.uuid4().hex,
                    "command": command,
                    "args": args,
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise AgentError(f"Music Assistant 연결 실패: {type(exc).__name__}") from None

        if not response.ok:
            raise AgentError(f"Music Assistant 요청 실패: HTTP {response.status_code}")
        try:
            result = response.json()
        except ValueError:
            raise AgentError("Music Assistant 응답이 JSON이 아닙니다.") from None
        if isinstance(result, dict):
            error = result.get("error_message") or result.get("error")
            if result.get("success") is False or error:
                raise AgentError(f"Music Assistant 요청 실패: {error or 'unknown error'}")
        return result

    def find_ytmusic_instance(self) -> str:
        providers = self.command(
            "config/providers",
            {"provider_domain": "ytmusic", "include_values": False},
        )
        if not isinstance(providers, list):
            raise AgentError("Music Assistant 공급자 목록 형식이 올바르지 않습니다.")
        matches = [
            item
            for item in providers
            if isinstance(item, dict) and item.get("domain") == "ytmusic"
        ]
        if len(matches) != 1:
            raise AgentError(
                f"YouTube Music 공급자가 정확히 하나여야 합니다. (현재 {len(matches)}개)"
            )
        instance_id = str(matches[0].get("instance_id") or "").strip()
        if not instance_id:
            raise AgentError("YouTube Music 공급자 instance_id가 없습니다.")
        return instance_id

    def update_cookie(self, instance_id: str, cookie: str) -> None:
        self.command(
            "config/providers/save",
            {
                "provider_domain": "ytmusic",
                "instance_id": instance_id,
                "values": {"cookie": cookie},
            },
        )


class CookieAgent:
    def __init__(self):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if Config.mqtt_username:
            password = read_private_secret(
                Config.mqtt_password_file,
                "MQTT 비밀번호",
            )
            self.client.username_pw_set(Config.mqtt_username, password)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.cache_lock = threading.Lock()

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            print(f"MQTT 연결 실패: {reason_code}", flush=True)
            return
        client.subscribe(Config.command_topic(), qos=1)
        print(f"명령 대기 중: {Config.command_topic()}", flush=True)

    def on_message(self, client, userdata, message):
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            request_id = str(payload.get("request_id") or "")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            print("올바르지 않은 MQTT 명령을 무시했습니다.", flush=True)
            return
        if not SAFE_REQUEST_ID.fullmatch(request_id):
            print("안전하지 않은 request_id를 무시했습니다.", flush=True)
            return

        with self.cache_lock:
            cached = self.cache.get(request_id)
        if cached is not None:
            self.publish_result(request_id, cached)
            return

        threading.Thread(
            target=self.handle,
            args=(request_id, payload),
            daemon=True,
        ).start()

    def handle(self, request_id: str, payload: dict[str, Any]):
        started = time.monotonic()
        try:
            if payload.get("action") != ACTION:
                raise AgentError(f"지원하지 않는 동작입니다. ({payload.get('action')})")
            result = self.refresh_cookie()
            result.update(success=True)
        except AgentError as exc:
            result = {"success": False, "message": str(exc)}
        except Exception as exc:
            result = {
                "success": False,
                "message": f"에이전트 내부 오류: {type(exc).__name__}",
            }
        result["duration_seconds"] = round(time.monotonic() - started, 2)
        self.remember(request_id, result)
        self.publish_result(request_id, result)

    def refresh_cookie(self) -> dict[str, Any]:
        if not Config.collector.is_file():
            raise AgentError(f"쿠키 수집기를 찾을 수 없습니다. ({Config.collector})")

        try:
            process = subprocess.run(
                [
                    "/usr/bin/python3",
                    str(Config.collector),
                    "--timeout",
                    str(Config.collector_timeout),
                ],
                capture_output=True,
                text=True,
                timeout=Config.collector_timeout + 30,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise AgentError("쿠키 수집 시간이 초과되었습니다.") from None
        if process.returncode != 0:
            detail = (process.stderr or "").strip().splitlines()
            safe_detail = detail[-1][:300] if detail else "원인 정보 없음"
            raise AgentError(f"쿠키 수집 실패: {safe_detail}")

        cookie = read_private_secret(Config.cookie_file, "쿠키")
        if "__Secure-3PAPISID=" not in cookie:
            raise AgentError("수집된 쿠키에 필수 로그인 항목이 없습니다.")
        token = read_private_secret(Config.mass_token_file, "Music Assistant 토큰")

        mass = MusicAssistant(Config.mass_url, token, Config.mass_timeout)
        instance_id = mass.find_ytmusic_instance()
        mass.update_cookie(instance_id, cookie)

        status_file = Config.cookie_file.with_name("status.json")
        status = read_status(status_file)
        return {
            "message": "쿠키 수집 및 Music Assistant 갱신을 완료했습니다.",
            "provider_instance_id": instance_id,
            "collected_at": status.get("collected_at"),
            "fingerprint": status.get("fingerprint"),
        }

    def remember(self, request_id: str, result: dict[str, Any]):
        with self.cache_lock:
            self.cache[request_id] = result
            self.cache.move_to_end(request_id)
            while len(self.cache) > 100:
                self.cache.popitem(last=False)

    def publish_result(self, request_id: str, result: dict[str, Any]):
        self.client.publish(
            Config.result_topic(request_id),
            json.dumps(result, ensure_ascii=False),
            qos=1,
        )

    def run(self):
        self.client.connect(Config.mqtt_host, Config.mqtt_port, 60)
        self.client.loop_forever(retry_first_connection=True)


def read_private_secret(path: Path, label: str) -> str:
    if not path.is_file():
        raise AgentError(f"{label} 파일이 없습니다. ({path})")
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise AgentError(f"{label} 파일 권한이 안전하지 않습니다. (현재 {mode:o}, 필요 600)")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise AgentError(f"{label} 파일이 비어 있습니다.")
    return value


def read_status(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


if __name__ == "__main__":
    CookieAgent().run()
