#!/usr/bin/env python3
"""Publish one-second CPU/RAM/network/storage telemetry to IoTCore over MQTT."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import time

import paho.mqtt.client as mqtt
import psutil


class Config:
    host = os.environ.get("IOTCORE_MQTT_HOST", "192.168.0.8")
    port = int(os.environ.get("IOTCORE_MQTT_PORT", "1884"))
    username = os.environ.get("IOTCORE_MQTT_USERNAME", "").strip()
    password_file = Path(os.environ.get("IOTCORE_MQTT_PASSWORD_FILE", ""))
    node_uid = os.environ.get("IOTCORE_NODE_UID", socket.gethostname().lower()).strip()
    interval = max(0.5, float(os.environ.get("IOTCORE_TELEMETRY_INTERVAL", "1")))
    storage_path = os.environ.get("IOTCORE_STORAGE_PATH", "").strip()
    network_interface = os.environ.get("IOTCORE_NETWORK_INTERFACE", "").strip()

    @classmethod
    def topic(cls) -> str:
        return f"iotcore/nodes/{cls.node_uid}/telemetry"


def read_password() -> str:
    if not Config.username:
        return ""
    if not Config.password_file.is_file():
        raise RuntimeError(f"MQTT password file not found: {Config.password_file}")
    return Config.password_file.read_text(encoding="utf-8").strip()


def network_counters():
    if Config.network_interface:
        per_nic = psutil.net_io_counters(pernic=True)
        if Config.network_interface not in per_nic:
            raise RuntimeError(
                f"network interface not found: {Config.network_interface}; "
                f"available={', '.join(per_nic.keys())}"
            )
        return per_nic[Config.network_interface]
    return psutil.net_io_counters(pernic=False)


def storage_usage():
    path = Config.storage_path
    if not path:
        path = Path.cwd().anchor or "/"
    try:
        usage = psutil.disk_usage(path)
    except (FileNotFoundError, OSError):
        return None
    gb = 1024 ** 3
    return {
        "used_percent": round(float(usage.percent), 2),
        "used_gb": round(usage.used / gb, 2),
        "total_gb": round(usage.total / gb, 2),
    }


def memory_usage():
    memory = psutil.virtual_memory()
    gb = 1024 ** 3
    used = memory.total - memory.available
    return {
        "percent": round(float(memory.percent), 2),
        "used_gb": round(used / gb, 2),
        "total_gb": round(memory.total / gb, 2),
    }


def _read_linux_cpu_max_ghz():
    """Best-effort Linux fallback when psutil reports max frequency as 0."""
    for filename in ("cpuinfo_max_freq", "scaling_max_freq"):
        path = Path(f"/sys/devices/system/cpu/cpu0/cpufreq/{filename}")
        try:
            khz = float(path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, OSError, ValueError):
            continue
        if khz > 0:
            return round(khz / 1_000_000, 2)
    return None


def cpu_frequency():
    freq = psutil.cpu_freq()
    if freq is None:
        return {
            "current_ghz": None,
            "max_ghz": _read_linux_cpu_max_ghz(),
        }

    current_ghz = (
        round(float(freq.current) / 1000, 2)
        if freq.current and freq.current > 0
        else None
    )
    max_ghz = (
        round(float(freq.max) / 1000, 2)
        if freq.max and freq.max > 0
        else _read_linux_cpu_max_ghz()
    )
    return {
        "current_ghz": current_ghz,
        "max_ghz": max_ghz,
    }


def main() -> None:
    if not Config.node_uid:
        raise RuntimeError("IOTCORE_NODE_UID is required")

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"iotcore-{Config.node_uid}-telemetry",
    )
    if Config.username:
        client.username_pw_set(Config.username, read_password())
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.connect(Config.host, Config.port, 60)
    client.loop_start()

    psutil.cpu_percent(interval=None)
    previous = network_counters()
    previous_time = time.monotonic()
    next_storage_at = 0.0
    cached_storage = None

    print(
        f"telemetry started: {Config.topic()} / every {Config.interval:.1f}s",
        flush=True,
    )

    try:
        while True:
            loop_started = time.monotonic()
            current = network_counters()
            current_time = time.monotonic()
            elapsed = max(0.001, current_time - previous_time)

            download_mbps = max(0.0, (current.bytes_recv - previous.bytes_recv) * 8 / elapsed / 1_000_000)
            upload_mbps = max(0.0, (current.bytes_sent - previous.bytes_sent) * 8 / elapsed / 1_000_000)
            previous = current
            previous_time = current_time

            if current_time >= next_storage_at:
                cached_storage = storage_usage()
                next_storage_at = current_time + 30.0

            cpu = cpu_frequency()
            memory = memory_usage()

            payload = {
                "cpu_percent": round(psutil.cpu_percent(interval=None), 2),
                "cpu_current_ghz": cpu["current_ghz"],
                "cpu_max_ghz": cpu["max_ghz"],
                "memory_percent": memory["percent"],
                "memory_used_gb": memory["used_gb"],
                "memory_total_gb": memory["total_gb"],
                "network": {
                    "download_mbps": round(download_mbps, 3),
                    "upload_mbps": round(upload_mbps, 3),
                },
                "storage": cached_storage or {},
            }

            client.publish(
                Config.topic(),
                json.dumps(payload, separators=(",", ":")),
                qos=0,
                retain=False,
            )

            remaining = Config.interval - (time.monotonic() - loop_started)
            if remaining > 0:
                time.sleep(remaining)
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
