from django.conf import settings
import pandas as pd 
import os


DATA_DIR = os.path.join(settings.BASE_DIR, 'iotcore', 'management', 'data')
device_path = os.path.join(DATA_DIR, 'device_codes.csv').replace('\\', '/')
device = pd.read_csv(device_path, encoding='utf-8')
device.bits = device.bits.astype(int)

class IRCodeRepository:

    @staticmethod
    def get_ir_code(motion, bits=24):
        query = (device.motion == motion)
        if bits:
            query &= (device.bits == bits)
        try:
            return device.loc[query, 'code'].iloc[0]
        except IndexError:
            return None