from .base import BaseDevice
import logging

logger = logging.getLogger("HaiKang")

class HaiKangDevice(BaseDevice):
    """
    海康设备驱动（占位模版）
    """
    def __init__(self, ip, port=80, **kwargs):
        super().__init__(ip, port, **kwargs)
        self.username = kwargs.get("username", "admin")
        self.password = kwargs.get("password", "12345")

    def add_person(self, user_id, name, image_path, card_id="", password=""):
        logger.info(f"模拟海康下发: {name}")
        return True, "海康驱动暂未实现"

    def delete_person(self, user_id):
        return True, "海康驱动暂未实现"

    def query_persons(self, page=0, limit=10):
        return True, {}

    def check_person_exists(self, user_id):
        return True, "模拟存在"