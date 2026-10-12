"""Read-only, bounded AI/GPU probes, independent of the one-second metric loop."""
from concurrent.futures import ThreadPoolExecutor
import csv
from io import StringIO
import json
import math
import os
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def probe_service(name, url, timeout=2):
    if not url:
        return {"status": "unconfigured", "error": "상태 확인 주소 미설정"}
    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            body = response.read(65537)
        if len(body) > 65536:
            raise ValueError("health 응답 크기 초과")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("health 응답은 JSON object여야 합니다")
        expected = f"iotcore-{name}-worker"
        if data.get("service") != expected:
            raise ValueError(f"health 서비스 식별 불일치: {expected}")
        error = str(data.get("last_error") or data.get("error") or "")[:300]
        ready = data.get("ready", data.get("ok"))
        if name == "asr" and data.get("model_loaded") is False:
            ready = False
        if ready is True:
            status = "processing" if data.get("busy") is True else "ready"
        elif error:
            status = "error"
        elif ready is False:
            status = "starting"
        else:
            status = "unknown"
        return {"status": status, "error": error,
                **{key: str(data.get(key) or "")[:100] for key in ("model", "engine", "device", "version")}}
    except HTTPError as exc:
        return {"status": "error", "error": f"health HTTP {exc.code}"}
    except (URLError, TimeoutError, OSError) as exc:
        return {"status": "unreachable", "error": f"{type(exc).__name__}: {exc}"[:300]}
    except (ValueError, TypeError) as exc:
        return {"status": "error", "error": str(exc)[:300]}


def probe_gpu(timeout=2):
    def number(value):
        try:
            result = float(value)
            return result if math.isfinite(result) and result >= 0 else None
        except ValueError:
            return None
    try:
        result = subprocess.run([
            "nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader,nounits",
        ], capture_output=True, text=True, timeout=timeout, check=True,
           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        devices = []
        for row in csv.reader(StringIO(result.stdout), skipinitialspace=True):
            if len(row) != 5:
                continue
            devices.append({"name": row[0].strip(),
                            **{key: number(value.strip()) for key, value in zip(
                                ("utilization_percent", "memory_used_mb", "memory_total_mb", "temperature_c"), row[1:])}})
        return {"status": "available" if devices else "unavailable", "devices": devices[:8],
                "error": "" if devices else "NVIDIA GPU 데이터 없음"}
    except (OSError, subprocess.SubprocessError):
        return {"status": "unavailable", "devices": [], "error": "NVIDIA GPU 상태를 수집할 수 없습니다"}


class AIProbeCollector:
    def __init__(self, urls, *, gpu_enabled=False, interval=5, timeout=2):
        self.urls = urls
        self.gpu_enabled = gpu_enabled
        self.interval = max(1, interval)
        self.timeout = max(0.2, timeout)
        self._lock = threading.Lock()
        self._values = {}
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name="iotcore-ai-probes", daemon=True)
        self._thread.start()

    def _run(self):
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="iotcore-health") as pool:
            while not self._stop.is_set():
                jobs = {name: pool.submit(probe_service, name, url, self.timeout)
                        for name, url in self.urls.items()}
                if self.gpu_enabled:
                    jobs["gpu"] = pool.submit(probe_gpu, self.timeout)
                for name, job in jobs.items():
                    value = job.result()
                    with self._lock:
                        self._values[name] = (value, time.monotonic())
                self._stop.wait(self.interval)

    def snapshot(self):
        now = time.monotonic()
        with self._lock:
            values = {name: {**value, "age_seconds": round(max(0, now - checked), 3)}
                      for name, (value, checked) in self._values.items()}
        return {"services": {name: values.get(name, {"status": "unknown"}) for name in self.urls},
                "gpu": values.get("gpu", {"status": "unavailable", "devices": [],
                                          "error": "GPU 상태 확인 대기" if self.gpu_enabled else "GPU 수집 비활성"})}

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)
