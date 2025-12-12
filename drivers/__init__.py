# drivers/__init__.py
from .haiou import HaiOuDevice
from .haikang import HaiKangDevice

# 定义支持的类型映射
DRIVER_MAP = {
    "haiou": HaiOuDevice,
    "haikang": HaiKangDevice,
    # "dahua": DahuaDevice 以后加
}

def get_device_driver(device_type, ip, **kwargs):
    """
    工厂函数：根据 device_type 返回对应的驱动实例
    :param device_type: 'haiou' 或 'haikang'
    :param ip: 设备IP
    :param kwargs: 其他参数 (port, token, username, password)
    :return: 具体驱动对象
    """
    driver_class = DRIVER_MAP.get(device_type)
    if not driver_class:
        raise ValueError(f"未知的设备类型: {device_type}")
    
    return driver_class(ip, **kwargs)