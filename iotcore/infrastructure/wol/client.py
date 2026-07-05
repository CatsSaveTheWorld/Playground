import socket
import re


class WOLClient:
    
    @staticmethod
    def send_wol(mac: str, ip: str = "255.255.255.255", port: int = 9) -> None:
        import re
        import socket
        """
        Send a Wake-on-LAN magic packet.

        mac: 대상 장치 MAC 주소 (예: 'AA:BB:CC:DD:EE:FF', 'AA-BB-CC-DD-EE-FF', 'AABBCCDDEEFF')
        ip : 브로드캐스트 IP (기본값 255.255.255.255). 보통 같은 서브넷이면 동작.
            라우터/서브넷에 따라 '192.168.0.255' 같은 서브넷 브로드캐스트를 쓰는 게 더 확실할 때가 있음.
        port: 보통 7 또는 9 사용
        """
        # MAC 정규화: 구분자 제거 후 12 hex인지 확인
        cleaned = re.sub(r"[^0-9A-Fa-f]", "", mac)
        if len(cleaned) != 12:
            raise ValueError(f"Invalid MAC address: {mac}")

        mac_bytes = bytes.fromhex(cleaned)

        # Magic Packet: FF 6번 + MAC 16번 반복
        packet = b"\xff" * 6 + mac_bytes * 16

        # UDP 브로드캐스트 전송
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(packet, (ip, port))
            print(f"WOL => MAC={mac}, IP={ip}, PORT={port}")