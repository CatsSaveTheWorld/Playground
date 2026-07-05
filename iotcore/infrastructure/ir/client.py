import requests


class IRClient:
    def send_ir_request(ip_address, code):
        url = f"http://{ip_address}/ir?code={code}"
        print(f"ESP32로 전송될 URL: {url}")
        try:
            res = requests.get(url, timeout=5)
            res.raise_for_status()
            print(f"ESP32 응답: {res.text}")
            return True, res.text
        except requests.exceptions.RequestException as e:
            print(f"ESP32 통신 오류: {e}")
            return False, str(e)