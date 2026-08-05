import json


# 요청 데이터 자동 파싱 (Form / JSON 통합)
def parse_request_data(request):
    """Form-data 또는 JSON 요청을 dict로 변환"""
    if request.content_type == "application/json":
        try:
            return json.loads(request.body)
        except json.JSONDecodeError:
            return {}
    return request.POST.dict()