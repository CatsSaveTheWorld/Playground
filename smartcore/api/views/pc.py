from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from .common import control_internal, parse_request_data
from django.conf import settings
from ...infrastructure.wol.client import WOLClient
import requests



# PC 제어
@csrf_exempt
def pc_power_on(request):
    if request.method != "POST":
        return JsonResponse({'status': 'fail', 'message': 'POST 요청만 허용됩니다.'}, status=400)
    
    data = parse_request_data(request)
    pc_name = data.get("pc_name")
    pc_mac = data.get("pc_mac")
    pc_ip = data.get("pc_ip")

    # print(pc_name, pc_mac, pc_ip)     # 디버그용
    
    if not pc_mac or not pc_ip:
        return JsonResponse({'status': 'fail', 'message': 'pc_name / pc_mac / pc_ip 값이 없습니다.'}, status=400)

    try:
        WOLClient.send_wol(pc_mac)
    except Exception as e:
        return JsonResponse({'status': 'fail', 'message': f'WOL 전송 실패: {e}'}, status=400)

    success_message = f"{pc_name} PC가 정상적으로 켜졌습니다!"
    return JsonResponse({'status': 'success', 'message': success_message})


@csrf_exempt
def pc_power_off(request):
    if request.method != "POST":
        return JsonResponse({'status': 'fail', 'message': 'POST 요청만 허용됩니다.'}, status=400)

    data = parse_request_data(request)

    # 프론트에서 내려주는 값 (PC Power On에서 쓰던 방식 그대로)
    pc_name = data.get("pc_name") or data.get("pc_id") or "PC"
    pc_ip   = data.get("pc_ip")   # 예: 192.168.0.7

    if not pc_ip:
        return JsonResponse({'status': 'fail', 'message': 'pc_ip 값이 없습니다.'}, status=400)

    # ✅ 에이전트 접속 정보 (기본값)
    agent_port = int(data.get("agent_port") or getattr(settings, "PC_AGENT_PORT", 5050))
    agent_path = data.get("agent_path") or getattr(settings, "PC_AGENT_SHUTDOWN_PATH", "/shutdown")

    url = f"http://{pc_ip}:{agent_port}{agent_path}"

    # ✅ (선택) 토큰 인증
    headers = {}
    token = "q6qCz88KzVnvaKIsGrXa4YC0XeTYtqVwuXruHMw63nT8-1S09f0fHQ1DBWnRCrwE"
    if token:
        headers["X-Token"] = token

    try:
        res = requests.post(url, headers=headers, timeout=2)
        res.raise_for_status()

        # 에이전트가 JSON을 반환하면 메시지 전달
        try:
            payload = res.json()
        except ValueError:
            payload = {}

        msg = payload.get("msg") or payload.get("message") or f"{pc_name} 종료 요청을 전송했습니다."
        return JsonResponse({'status': 'success', 'message': msg})

    except requests.exceptions.Timeout:
        return JsonResponse({'status': 'fail', 'message': f'{pc_name} 종료 요청 시간 초과(Timeout)'}, status=504)

    except requests.exceptions.ConnectionError:
        return JsonResponse({'status': 'fail', 'message': f'{pc_name} 에이전트 연결 실패(PC가 꺼져있거나 포트/방화벽 확인)'}, status=502)

    except requests.exceptions.HTTPError:
        # 에이전트가 401/403/500 같은 걸 주는 경우
        return JsonResponse({'status': 'fail', 'message': f'{pc_name} 에이전트 오류: HTTP {res.status_code}'}, status=502)

    except Exception as e:
        return JsonResponse({'status': 'fail', 'message': f'예외 발생: {str(e)}'}, status=500)
