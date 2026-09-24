from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from iotcore.ai.asr_client import ASRClient
from iotcore.ai.nlp_client import NLPClient
from iotcore.ai.service import AIControlService

MAX_VOICE_UPLOAD_BYTES = 20 * 1024 * 1024


@csrf_exempt
@require_POST
def voice_upload(request):
    """Receive a WAV command and optionally run ASR/NLP/device execution.

    Android multipart/form-data:
      - audio
      - client_id
      - device_name

    WAV files are persisted only when settings.VOICE_SAVE_UPLOADS is true
    (by default, only DEBUG=True). Production requests are forwarded to the
    ASR worker from memory and leave no voice file on disk.
    """

    audio = request.FILES.get("audio")
    if audio is None:
        return JsonResponse(
            {"ok": False, "error": "audio 파일이 없습니다."},
            status=400,
        )

    if audio.size <= 0:
        return JsonResponse(
            {"ok": False, "error": "빈 오디오 파일입니다."},
            status=400,
        )

    if audio.size > MAX_VOICE_UPLOAD_BYTES:
        return JsonResponse(
            {"ok": False, "error": "오디오 파일이 20MB 제한을 초과했습니다."},
            status=413,
        )

    header = audio.read(12)
    audio.seek(0)
    if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        return JsonResponse(
            {"ok": False, "error": "유효한 WAV 파일이 아닙니다."},
            status=400,
        )

    request_id = uuid4().hex
    client_id = request.POST.get("client_id", "")
    device_name = request.POST.get("device_name", "")
    original_name = Path(audio.name).name
    stored_name = f"{request_id}.wav"

    # Keep the uploaded WAV in memory for the ASR request. The upload size is
    # already capped at MAX_VOICE_UPLOAD_BYTES above.
    audio.seek(0)
    audio_bytes = audio.read()

    save_upload = bool(getattr(settings, "VOICE_SAVE_UPLOADS", settings.DEBUG))
    saved_path = None

    if save_upload:
        upload_root = Path(
            getattr(
                settings,
                "VOICE_UPLOAD_ROOT",
                settings.BASE_DIR / "runtime" / "voice_uploads",
            )
        )
        upload_root.mkdir(parents=True, exist_ok=True)
        saved_path = upload_root / stored_name
        saved_path.write_bytes(audio_bytes)

    asr_result = ASRClient().transcribe(
        audio_bytes=audio_bytes,
        filename=stored_name,
        request_id=request_id,
        client_id=client_id,
        language=request.POST.get("language", "ko") or "ko",
    )

    nlp_result = None

    if asr_result.ok and asr_result.text:
        nlp_result = NLPClient().parse(
            text=asr_result.text,
            request_id=request_id,
            client_id=client_id,
            language=asr_result.language or "ko",
        )

    execution = None

    if nlp_result and nlp_result.ok:
        device = AIControlService.resolve_device(
            device_uid=nlp_result.device_uid,
            device_type=nlp_result.device,
            location=nlp_result.location,
        )

        if device:
            success, message = AIControlService.execute_device_action(
                device=device,
                function=nlp_result.function,
                parameter=nlp_result.parameters,
            )

            execution = {
                "ok": success,
                "device_uid": device.device_uid,
                "message": message,
            }

    message = "voice received"
    if asr_result.ok:
        message = "voice received and transcribed"
    elif ASRClient().enabled:
        message = "voice received; ASR unavailable"

    return JsonResponse(
        {
            "ok": True,
            "request_id": request_id,
            "filename": stored_name if saved_path else None,
            "saved": saved_path is not None,
            "original_filename": original_name,
            "size": audio.size,
            "client_id": client_id,
            "device_name": device_name,
            "message": message,
            "asr": asr_result.as_dict(),
            "nlp": nlp_result.as_dict() if nlp_result else None,
            "execution": execution,
        },
        status=201,
    )
