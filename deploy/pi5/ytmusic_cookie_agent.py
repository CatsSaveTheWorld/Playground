#!/usr/bin/env python3
"""IoTCore MQTT agent for Pi-side cookie refresh and projector media playback."""

from __future__ import annotations

import argparse
from collections import OrderedDict
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any

import paho.mqtt.client as mqtt


COOKIE_ACTION = "ytmusic.refresh_cookie"
MEDIA_LIST_ACTION = "media.list_videos"
MEDIA_PLAY_ACTION = "media.play_video"
MEDIA_STOP_ACTION = "media.stop"
MEDIA_ACTIONS = {MEDIA_LIST_ACTION, MEDIA_PLAY_ACTION, MEDIA_STOP_ACTION}
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


class AgentError(RuntimeError):
    """Expected error that is safe to return to the Django server."""


class Config:
    mqtt_host = os.environ.get("IOTCORE_MQTT_HOST", "192.168.0.8")
    mqtt_port = int(os.environ.get("IOTCORE_MQTT_PORT", "1884"))
    mqtt_username = os.environ.get("IOTCORE_MQTT_USERNAME", "")
    mqtt_password_file = Path(
        os.environ.get(
            "IOTCORE_MQTT_PASSWORD_FILE",
            "/home/leedowon/.config/ytmusic-cookie-agent/mqtt-password",
        )
    )
    agent_id = os.environ.get("IOTCORE_AGENT_ID", "pi5")
    topic_prefix = os.environ.get("IOTCORE_TOPIC_PREFIX", "iotcore/agents").rstrip("/")
    python = os.environ.get("YTMUSIC_PYTHON", "/usr/bin/python3")
    collector = Path(
        os.environ.get(
            "YTMUSIC_COLLECTOR",
            "/home/leedowon/ytmusic-cookie-collector/collector.py",
        )
    )
    applier = Path(
        os.environ.get(
            "YTMUSIC_APPLIER",
            "/home/leedowon/ytmusic-cookie-collector/applier.py",
        )
    )
    collector_timeout = int(os.environ.get("YTMUSIC_COLLECTOR_TIMEOUT", "60"))
    applier_timeout = int(os.environ.get("YTMUSIC_APPLIER_TIMEOUT", "90"))
    collector_status = Path(
        os.environ.get(
            "YTMUSIC_COLLECTOR_STATUS",
            "/home/leedowon/.local/state/ytmusic-cookie-collector/status.json",
        )
    )
    applier_status = Path(
        os.environ.get(
            "YTMUSIC_APPLIER_STATUS",
            "/home/leedowon/.local/state/ytmusic-cookie-collector/apply_status.json",
        )
    )

    media_root = Path(
        os.environ.get(
            "IOTCORE_MEDIA_ROOT",
            "/home/leedowon/qleto_2tb/wallpaper/videos",
        )
    )
    mpv_binary = Path(os.environ.get("IOTCORE_MPV_BINARY", "/usr/bin/mpv"))
    xdg_runtime_dir = os.environ.get("IOTCORE_XDG_RUNTIME_DIR", "/run/user/1000")
    wayland_display = os.environ.get("IOTCORE_WAYLAND_DISPLAY", "wayland-0")
    dbus_session_bus = os.environ.get(
        "IOTCORE_DBUS_SESSION_BUS_ADDRESS",
        "unix:path=/run/user/1000/bus",
    )
    media_extensions = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}

    @classmethod
    def command_topic(cls) -> str:
        return f"{cls.topic_prefix}/{cls.agent_id}/commands"

    @classmethod
    def result_topic(cls, request_id: str) -> str:
        return f"{cls.topic_prefix}/{cls.agent_id}/results/{request_id}"


def read_private_secret(path: Path, label: str) -> str:
    if not path.is_file():
        raise AgentError(f"{label} 파일이 없습니다. ({path})")
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise AgentError(
            f"{label} 파일 권한이 안전하지 않습니다. (현재 {mode:o}, 필요 600)"
        )
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise AgentError(f"{label} 파일이 비어 있습니다.")
    if "\n" in value or "\r" in value:
        raise AgentError(f"{label} 값은 한 줄이어야 합니다.")
    return value


def read_status(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise AgentError(f"{label} 상태 파일이 없습니다. ({path})")
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise AgentError(f"{label} 상태 파일 권한이 안전하지 않습니다.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AgentError(f"{label} 상태 파일을 읽지 못했습니다.") from error
    if not isinstance(value, dict):
        raise AgentError(f"{label} 상태 파일 형식이 올바르지 않습니다.")
    return value


def safe_process_error(process: subprocess.CompletedProcess[str]) -> str:
    """Return one bounded diagnostic line without returning command output wholesale."""
    lines = (process.stderr or process.stdout or "").strip().splitlines()
    return lines[-1][:300] if lines else "원인 정보 없음"


class PiAgent:
    def __init__(self) -> None:
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"iotcore-{Config.agent_id}-agent",
        )
        if Config.mqtt_username:
            password = read_private_secret(Config.mqtt_password_file, "MQTT 비밀번호")
            self.client.username_pw_set(Config.mqtt_username, password)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

        self.cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.state_lock = threading.Lock()
        self.in_flight: set[str] = set()
        self.refresh_lock = threading.Lock()
        self.media_lock = threading.Lock()
        self.media_process: subprocess.Popen[Any] | None = None
        self.now_playing: str | None = None

    def on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if reason_code != 0:
            print(f"MQTT 연결 실패: {reason_code}", flush=True)
            return
        client.subscribe(Config.command_topic(), qos=1)
        print(f"명령 대기 중: {Config.command_topic()}", flush=True)

    def on_disconnect(
        self,
        client,
        userdata,
        disconnect_flags,
        reason_code,
        properties=None,
    ) -> None:
        if reason_code != 0:
            print(f"MQTT 연결 끊김: {reason_code}; 재연결 대기 중", flush=True)

    def on_message(self, client, userdata, message) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8"))
            request_id = str(payload.get("request_id") or "")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            print("올바르지 않은 MQTT 명령을 무시했습니다.", flush=True)
            return
        if not isinstance(payload, dict) or not SAFE_REQUEST_ID.fullmatch(request_id):
            print("안전하지 않은 request_id를 가진 명령을 무시했습니다.", flush=True)
            return

        with self.state_lock:
            cached = self.cache.get(request_id)
            if cached is not None:
                self.cache.move_to_end(request_id)
            elif request_id in self.in_flight:
                return
            else:
                self.in_flight.add(request_id)

        if cached is not None:
            self.publish_result(request_id, cached)
            return

        threading.Thread(
            target=self.handle,
            args=(request_id, payload),
            name=f"cookie-refresh-{request_id[:8]}",
            daemon=True,
        ).start()

    def handle(self, request_id: str, payload: dict[str, Any]) -> None:
        started = time.monotonic()
        try:
            action = str(payload.get("action") or "")
            parameters = payload.get("parameters") or {}
            if not isinstance(parameters, dict):
                raise AgentError("parameters 형식이 올바르지 않습니다.")

            if action == COOKIE_ACTION:
                if not self.refresh_lock.acquire(blocking=False):
                    raise AgentError("다른 YouTube Music 쿠키 갱신이 이미 실행 중입니다.")
                try:
                    result = self.refresh_cookie()
                finally:
                    self.refresh_lock.release()
            elif action in MEDIA_ACTIONS:
                result = self.handle_media_action(action, parameters)
            else:
                raise AgentError(f"지원하지 않는 동작입니다. ({action})")
            result["success"] = True
        except AgentError as error:
            result = {"success": False, "message": str(error)}
        except Exception as error:
            result = {
                "success": False,
                "message": f"에이전트 내부 오류: {type(error).__name__}",
            }

        result["duration_seconds"] = round(time.monotonic() - started, 2)
        with self.state_lock:
            self.in_flight.discard(request_id)
            self.cache[request_id] = result
            self.cache.move_to_end(request_id)
            while len(self.cache) > 100:
                self.cache.popitem(last=False)
        self.publish_result(request_id, result)

    def handle_media_action(
        self,
        action: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        with self.media_lock:
            if action == MEDIA_LIST_ACTION:
                return self.list_videos()
            if action == MEDIA_PLAY_ACTION:
                return self.play_video(str(parameters.get("video_id") or ""))
            if action == MEDIA_STOP_ACTION:
                return self.stop_video()
        raise AgentError(f"지원하지 않는 미디어 동작입니다. ({action})")

    def media_root_resolved(self) -> Path:
        root = Config.media_root.expanduser().resolve()
        if not root.is_dir():
            raise AgentError(f"영상 폴더를 찾을 수 없습니다. ({root})")
        return root

    def list_videos(self) -> dict[str, Any]:
        root = self.media_root_resolved()
        videos = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in Config.media_extensions:
                continue
            relative = path.relative_to(root)
            if any(part.startswith(".") for part in relative.parts):
                continue
            videos.append(
                {
                    "id": relative.as_posix(),
                    "title": path.stem,
                    "filename": path.name,
                    "relative_path": relative.as_posix(),
                }
            )
        videos.sort(key=lambda item: (item["title"].casefold(), item["id"].casefold()))
        return {
            "message": f"영상 {len(videos)}개를 불러왔습니다.",
            "videos": videos,
            "now_playing": self.now_playing,
        }

    def resolve_video(self, video_id: str) -> tuple[Path, str]:
        video_id = video_id.strip()
        if not video_id:
            raise AgentError("재생할 영상이 지정되지 않았습니다.")

        root = self.media_root_resolved()
        candidate = (root / video_id).resolve()
        try:
            relative = candidate.relative_to(root)
        except ValueError as error:
            raise AgentError("영상 폴더 밖의 파일은 재생할 수 없습니다.") from error

        if not candidate.is_file():
            raise AgentError("선택한 영상 파일을 찾을 수 없습니다.")
        if candidate.suffix.lower() not in Config.media_extensions:
            raise AgentError("지원하지 않는 영상 형식입니다.")
        return candidate, relative.as_posix()

    def stop_managed_player(self) -> bool:
        process = self.media_process
        self.media_process = None
        self.now_playing = None
        if process is None or process.poll() is not None:
            return False
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        return True

    def play_video(self, video_id: str) -> dict[str, Any]:
        video_path, relative = self.resolve_video(video_id)
        if not Config.mpv_binary.is_file():
            raise AgentError(f"mpv 실행 파일을 찾을 수 없습니다. ({Config.mpv_binary})")

        self.stop_managed_player()

        environment = os.environ.copy()
        environment.update(
            {
                "XDG_RUNTIME_DIR": Config.xdg_runtime_dir,
                "WAYLAND_DISPLAY": Config.wayland_display,
                "DBUS_SESSION_BUS_ADDRESS": Config.dbus_session_bus,
            }
        )
        command = [
            str(Config.mpv_binary),
            "--vo=gpu",
            "--gpu-context=wayland",
            "--profile=fast",
            "--fullscreen",
            "--loop-file=inf",
            "--no-border",
            "--no-audio",
            "--",
            str(video_path),
        ]
        try:
            process = subprocess.Popen(
                command,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as error:
            raise AgentError(f"mpv를 시작하지 못했습니다: {error}") from error

        time.sleep(0.25)
        if process.poll() is not None:
            raise AgentError("mpv가 영상을 재생하지 못하고 종료되었습니다.")

        self.media_process = process
        self.now_playing = relative
        return {
            "message": f"{video_path.stem} 재생을 시작했습니다.",
            "now_playing": relative,
        }

    def stop_video(self) -> dict[str, Any]:
        stopped = self.stop_managed_player()
        return {
            "message": (
                "영상 재생을 정지했습니다."
                if stopped
                else "현재 에이전트가 재생 중인 영상이 없습니다."
            ),
            "now_playing": None,
        }

    def run_process(
        self,
        label: str,
        command: list[str],
        timeout: int,
        working_directory: Path,
    ) -> None:
        try:
            process = subprocess.run(
                command,
                cwd=working_directory,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise AgentError(f"{label} 제한 시간을 초과했습니다.") from error
        except OSError as error:
            raise AgentError(f"{label} 프로세스를 시작하지 못했습니다: {error}") from error
        if process.returncode != 0:
            raise AgentError(f"{label} 실패: {safe_process_error(process)}")

    def refresh_cookie(self) -> dict[str, Any]:
        if not Config.collector.is_file():
            raise AgentError(f"쿠키 수집기를 찾을 수 없습니다. ({Config.collector})")
        if not Config.applier.is_file():
            raise AgentError(f"쿠키 적용기를 찾을 수 없습니다. ({Config.applier})")

        self.run_process(
            "쿠키 수집",
            [
                Config.python,
                str(Config.collector),
                "--timeout",
                str(Config.collector_timeout),
            ],
            Config.collector_timeout + 30,
            Config.collector.parent,
        )
        collected = read_status(Config.collector_status, "쿠키 수집")
        collected_fingerprint = str(collected.get("fingerprint") or "")
        if not collected_fingerprint:
            raise AgentError("쿠키 수집 상태에 fingerprint가 없습니다.")

        self.run_process(
            "쿠키 적용",
            [
                Config.python,
                str(Config.applier),
                "--timeout",
                str(Config.applier_timeout),
            ],
            Config.applier_timeout + 30,
            Config.applier.parent,
        )
        applied = read_status(Config.applier_status, "쿠키 적용")
        if applied.get("verified") is not True:
            raise AgentError("Music Assistant의 쿠키 적용 검증이 완료되지 않았습니다.")
        if applied.get("fingerprint") != collected_fingerprint:
            raise AgentError("수집한 쿠키와 Music Assistant에 적용한 쿠키가 일치하지 않습니다.")

        return {
            "message": "YouTube Music 쿠키 수집 및 Music Assistant 적용을 완료했습니다.",
            "stage": "complete",
            "provider_instance_id": applied.get("provider_instance_id"),
            "collected_at": collected.get("collected_at"),
            "applied_at": applied.get("applied_at"),
            "fingerprint": collected_fingerprint,
            "verified": True,
        }

    def publish_result(self, request_id: str, result: dict[str, Any]) -> None:
        info = self.client.publish(
            Config.result_topic(request_id),
            json.dumps(result, ensure_ascii=False),
            qos=1,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            print(f"결과 발행 실패: request_id={request_id}, rc={info.rc}", flush=True)

    def run(self) -> None:
        print(
            f"MQTT broker 연결 중: {Config.mqtt_host}:{Config.mqtt_port}",
            flush=True,
        )
        self.client.connect(Config.mqtt_host, Config.mqtt_port, 60)
        self.client.loop_forever(retry_first_connection=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="MQTT 없이 collector와 applier를 한 번 실행하고 종료",
    )
    args = parser.parse_args()
    try:
        agent = CookieAgent()
        if args.run_once:
            result = agent.refresh_cookie()
            result["success"] = True
            print(json.dumps(result, ensure_ascii=False))
            return 0
        agent.run()
        return 0
    except (AgentError, OSError, ValueError) as error:
        print(f"에이전트 시작 실패: {error}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
