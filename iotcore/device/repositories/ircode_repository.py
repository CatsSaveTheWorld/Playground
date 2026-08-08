from django.conf import settings
import pandas as pd
import os


DATA_DIR = os.path.join(settings.BASE_DIR, "iotcore", "management", "data")
DEVICE_CODE_PATH = os.path.join(DATA_DIR, "device_codes.csv").replace("\\", "/")
PROJECTOR_CODE_PATH = os.path.join(
    DATA_DIR,
    "zeus_l1300_ir_codes.csv",
).replace("\\", "/")


def _load_codes(path):
    codes = pd.read_csv(path, encoding="utf-8-sig")
    codes["bits"] = codes["bits"].astype(int)
    return codes


device_codes = _load_codes(DEVICE_CODE_PATH)
projector_codes = _load_codes(PROJECTOR_CODE_PATH)


class IRCodeRepository:

    @staticmethod
    def get_ir_code(motion, bits=24):
        """기존 IR 장치(device_codes.csv)의 코드를 조회한다."""
        query = device_codes.motion == motion
        if bits:
            query &= device_codes.bits == int(bits)

        try:
            return device_codes.loc[query, "code"].iloc[0]
        except IndexError:
            return None

    @staticmethod
    def get_projector_ir_code(motion, bits=32):
        """ZEUS L1300 전용 IR 코드 CSV에서 코드를 조회한다."""
        query = projector_codes.motion == motion
        if bits:
            query &= projector_codes.bits == int(bits)

        try:
            return projector_codes.loc[query, "code"].iloc[0]
        except IndexError:
            return None
