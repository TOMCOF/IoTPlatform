# drivers/base.py
from abc import ABC, abstractmethod

class BaseDevice(ABC):
    """
    设备驱动基类（所有品牌设备必须继承此类）
    """
    
    def __init__(self, ip, port, **kwargs):
        self.ip = ip
        self.port = port
        self.kwargs = kwargs

    @abstractmethod
    def add_person(self, user_id, name, image_path, card_id="", password=""):
        """下发人员：返回 (True/False, msg)"""
        pass

    @abstractmethod
    def delete_person(self, user_id):
        """删除人员：返回 (True/False, msg)"""
        pass

    @abstractmethod
    def query_persons(self, page=0, limit=10):
        """查询人员列表：返回 (True/False, data)"""
        pass

    @abstractmethod
    def check_person_exists(self, user_id):
        """反查验证人员是否存在：返回 (True/False, msg)"""
        pass