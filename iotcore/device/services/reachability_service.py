from __future__ import annotations

import platform
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone as dt_timezone

from ...models import Device
from .device_state_service import DeviceStateService


class DeviceReachabilityService:
    """Lightweight LAN reachability checks for Device Control cards.

    ``online`` means that the device's own configured network endpoint responds.
    ``controller_online`` means that the dedicated Controller (for example an
    ESP32 IR bridge) responds.  These are deliberately separate from physical
    power state.

    Device endpoint configuration lives in ``Device.control_config`` so no new
    schema is required.  Supported keys:

    - ``status_ip`` (preferred) or existing ``ip_address`` / ``host``
    - ``status_method``: ``icmp`` (default) or ``tcp``
    - ``status_port``: required when ``status_method`` is ``tcp``
    """

    DEVICE_HOST_KEYS = ("status_ip", "ip_address", "host")
    DEFAULT_TIMEOUT_SECONDS = 0.8
    MAX_WORKERS = 12

    @classmethod
    def _device_endpoint(cls, device: Device) -> dict | None:
        config = dict(device.control_config or {})
        host = next(
            (str(config.get(key) or "").strip() for key in cls.DEVICE_HOST_KEYS if str(config.get(key) or "").strip()),
            "",
        )
        if not host:
            return None

        method = str(config.get("status_method") or "icmp").strip().lower()
        if method not in {"icmp", "tcp"}:
            method = "icmp"

        port = config.get("status_port")
        if method == "tcp":
            if port in (None, ""):
                # PC shutdown agents already provide a useful TCP endpoint.
                port = config.get("agent_port")
            try:
                port = int(port)
            except (TypeError, ValueError):
                port = None
            if not port:
                method = "icmp"

        return {
            "host": host,
            "method": method,
            "port": port,
        }

    @classmethod
    def _controller_endpoint(cls, device: Device) -> dict | None:
        try:
            controller = device.controller
        except Exception:
            return None
        if not controller or not controller.ip_address:
            return None
        return {
            "host": str(controller.ip_address),
            "method": "icmp",
            "port": None,
        }

    @classmethod
    def describe_targets(cls, device: Device) -> dict:
        return {
            "device": cls._device_endpoint(device),
            "controller": cls._controller_endpoint(device),
        }

    @classmethod
    def _ping(cls, host: str, timeout: float) -> bool:
        system = platform.system().lower()
        if system == "windows":
            command = [
                "ping",
                "-n",
                "1",
                "-w",
                str(max(1, int(timeout * 1000))),
                host,
            ]
        else:
            # Linux iputils accepts integer seconds for -W.  subprocess timeout
            # keeps the total check bounded even on other ping implementations.
            command = ["ping", "-c", "1", "-W", "1", host]

        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=max(1.0, timeout + 0.5),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False
        return completed.returncode == 0

    @staticmethod
    def _tcp(host: str, port: int, timeout: float) -> bool:
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                return True
        except (OSError, ValueError, TypeError):
            return False

    @classmethod
    def check_endpoint(cls, endpoint: dict, *, timeout: float | None = None) -> bool:
        timeout = float(timeout or cls.DEFAULT_TIMEOUT_SECONDS)
        if endpoint.get("method") == "tcp" and endpoint.get("port"):
            return cls._tcp(endpoint["host"], endpoint["port"], timeout)
        return cls._ping(endpoint["host"], timeout)

    @classmethod
    def _persist_state(cls, device: Device, *, key: str, value: bool) -> None:
        # Reachability refreshes are observational health data.  Persist them in
        # canonical DeviceState without invoking Automation execution as a side
        # effect of simply opening the Device Control page.
        DeviceStateService.set_state(device, key, bool(value))

    @classmethod
    def check_many(cls, devices: list[Device]) -> dict[int, dict]:
        """Check all configured device/controller endpoints concurrently."""
        results: dict[int, dict] = {
            device.id: {
                "device_id": device.id,
                "device": None,
                "controller": None,
                "checked_at": None,
            }
            for device in devices
        }

        jobs = {}
        with ThreadPoolExecutor(max_workers=min(cls.MAX_WORKERS, max(1, len(devices) * 2))) as pool:
            for device in devices:
                targets = cls.describe_targets(device)
                for target_name, endpoint in targets.items():
                    if not endpoint:
                        continue
                    future = pool.submit(cls.check_endpoint, endpoint)
                    jobs[future] = (device, target_name, endpoint)

            for future in as_completed(jobs):
                device, target_name, endpoint = jobs[future]
                try:
                    online = bool(future.result())
                except Exception:
                    online = False
                results[device.id][target_name] = {
                    "online": online,
                    "host": endpoint["host"],
                    "method": endpoint["method"],
                }
                key = "online" if target_name == "device" else "controller_online"
                cls._persist_state(device, key=key, value=online)

        checked_at = datetime.now(dt_timezone.utc).isoformat()
        for result in results.values():
            if result["device"] is not None or result["controller"] is not None:
                result["checked_at"] = checked_at
        return results
