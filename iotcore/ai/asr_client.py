from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from django.conf import settings


@dataclass(frozen=True)
class ASRResult:
    ok: bool
    text: str = ""
    language: str = ""
    language_probability: float | None = None
    duration: float | None = None
    elapsed_ms: float | None = None
    error: str = ""
    http_status: int | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "ok": self.ok,
            "text": self.text,
            "language": self.language,
            "error": self.error,
        }
        if self.language_probability is not None:
            data["language_probability"] = self.language_probability
        if self.duration is not None:
            data["duration"] = self.duration
        if self.elapsed_ms is not None:
            data["elapsed_ms"] = self.elapsed_ms
        if self.http_status is not None:
            data["http_status"] = self.http_status
        return data


class ASRClient:
    def __init__(self) -> None:
        self.base_url = str(
            getattr(settings, "VOICE_ASR_BASE_URL", "")
        ).strip().rstrip("/")
        self.connect_timeout = float(
            getattr(settings, "VOICE_ASR_CONNECT_TIMEOUT", 2.0)
        )
        self.read_timeout = float(
            getattr(settings, "VOICE_ASR_READ_TIMEOUT", 30.0)
        )

    @property
    def enabled(self) -> bool:
        return bool(getattr(settings, "VOICE_ASR_ENABLED", False))

    def transcribe(
        self,
        *,
        audio_path: Path | None = None,
        audio_bytes: bytes | None = None,
        filename: str = "voice.wav",
        request_id: str,
        client_id: str = "",
        language: str = "ko",
    ) -> ASRResult:
        if not self.enabled:
            return ASRResult(ok=False, error="ASR disabled")

        if not self.base_url:
            return ASRResult(ok=False, error="VOICE_ASR_BASE_URL is empty")

        if audio_bytes is None and audio_path is None:
            return ASRResult(ok=False, error="audio input is empty")

        endpoint = f"{self.base_url}/v1/asr/transcribe"
        data = {
            "language": language,
            "request_id": request_id,
            "client_id": client_id,
        }

        try:
            if audio_bytes is not None:
                response = requests.post(
                    endpoint,
                    files={
                        "audio": (
                            filename or "voice.wav",
                            audio_bytes,
                            "audio/wav",
                        )
                    },
                    data=data,
                    timeout=(self.connect_timeout, self.read_timeout),
                )
            else:
                assert audio_path is not None
                with audio_path.open("rb") as audio_file:
                    response = requests.post(
                        endpoint,
                        files={
                            "audio": (
                                audio_path.name,
                                audio_file,
                                "audio/wav",
                            )
                        },
                        data=data,
                        timeout=(self.connect_timeout, self.read_timeout),
                    )
        except requests.RequestException as exc:
            return ASRResult(
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        except OSError as exc:
            return ASRResult(
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )

        try:
            payload = response.json()
        except ValueError:
            return ASRResult(
                ok=False,
                error=f"ASR worker returned non-JSON response: {response.text[:300]}",
                http_status=response.status_code,
            )

        if not response.ok or not payload.get("ok", False):
            detail = payload.get("detail")
            if isinstance(detail, dict):
                error = detail.get("message") or detail.get("last_error") or str(detail)
            else:
                error = str(detail or payload.get("error") or payload)
            return ASRResult(
                ok=False,
                error=error,
                http_status=response.status_code,
            )

        return ASRResult(
            ok=True,
            text=str(payload.get("text", "")).strip(),
            language=str(payload.get("language", "")).strip(),
            language_probability=_optional_float(payload.get("language_probability")),
            duration=_optional_float(payload.get("duration")),
            elapsed_ms=_optional_float(payload.get("elapsed_ms")),
            http_status=response.status_code,
        )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
