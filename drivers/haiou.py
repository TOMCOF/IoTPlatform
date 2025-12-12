import requests
import base64
import os
import time
import logging
import json
from .base import BaseDevice  # 继承自 base.py 定义的基类

# 配置日志
logger = logging.getLogger("HaiOu")

class HaiOuDevice(BaseDevice):
    
    def __init__(self, ip, port=8086, **kwargs):
        """
        初始化海鸥设备驱动
        """
        super().__init__(ip, port, **kwargs)
        self.token = kwargs.get("token", "123")  # 默认token为123
        self.base_url = f"http://{ip}:{port}/fcgi-bin/fcgi_websapi.fcgi"
        self.headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

    def _image_to_base64(self, image_path):
        """内部工具：图片转Base64"""
        if not os.path.exists(image_path):
            logger.error(f"[{self.ip}] 找不到图片: {image_path}")
            return None
        with open(image_path, "rb") as f:
            return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode('utf-8')

    def check_person_exists(self, work_id):
        """
        验证人员是否存在（通过 WorkId 精确查询）
        """
        url = f"{self.base_url}?timestamp={int(time.time()*1000)}&token={self.token}"
        payload = {
            "cmd": "DEV_FIND_FACES_BYPAGE",
            "payload": {
                "Search": {
                    "Pagesize": 10,
                    "Page": 0,
                    "NeedImge": 0,  # 验证时不需要图片
                    "UserName": "",
                    "CardNum": "",
                    "Person_uuid": "",
                    "WorkId": str(work_id)
                }
            }
        }
        try:
            resp = requests.post(url, json=payload, headers=self.headers, timeout=5)
            if resp.status_code == 200:
                res_json = resp.json()
                data_node = res_json.get("data", {})
                user_list = data_node.get("Userlist", [])
                
                if user_list and len(user_list) > 0:
                    found_uid = str(user_list[0].get("workId", ""))
                    if found_uid == str(work_id):
                        return True, "验证存在"
                
                return False, "验证未找到(照片可能质量差被后台拒收)"
            else:
                return False, f"HTTP状态: {resp.status_code}"
        except Exception as e:
            return False, f"验证连接异常: {e}"

    def add_person(self, user_id, name, image_path, card_id="", password=""):
        """
        下发人员（包含 accessInfo 结构）
        """
        b64_img = self._image_to_base64(image_path)
        if not b64_img:
            return False, "图片文件不存在"

        url = f"{self.base_url}?timestamp={int(time.time()*1000)}&token={self.token}"
        
        payload = {
            "cmd": "DEV_ADD_MULTI_FACES",
            "payload": {
                "Persons": [{
                    "groupId": "1",
                    "gender": "male",
                    "name": name,
                    "workId": str(user_id),
                    "userType": 0, 
                    
                    "accessInfo": {
                        "cardNum": str(card_id),
                        "password": str(password),
                        "validtime": "0",
                        "validtimeenable": 0,
                        "validtimeend": "0",
                        "authType": 0
                    },
                    
                    "images": [{"data": b64_img, "format": "jpeg"}]
                }]
            }
        }

        max_retries = 3
        for i in range(max_retries):
            try:
                logger.info(f"[{self.ip}] 正在下发 {name} (卡:{card_id}, 密:{password})...")
                resp = requests.post(url, json=payload, headers=self.headers, timeout=30)
                
                if resp.status_code == 200:
                    res_json = resp.json()
                    if res_json.get("detail") == "success" or res_json.get("status") == 0:
                        return True, "接口调用成功"
                    else:
                        return False, f"设备拒绝: {res_json.get('detail')}"
                        
            except requests.exceptions.Timeout:
                logger.warning(f"[{self.ip}] 连接超时，重试 ({i+1}/{max_retries})...")
            except Exception as e:
                logger.error(f"[{self.ip}] 连接错误: {e}")
            
            time.sleep(2)

        return False, "多次重试失败，设备可能掉线"

    def delete_person(self, user_id):
        """删除人员"""
        url = f"{self.base_url}?timestamp={int(time.time()*1000)}&token={self.token}"
        payload = {
            "cmd": "DEV_REMOVE_FACES",
            "payload": {
                "userId": str(user_id),
                "personId": ""
            }
        }
        try:
            logger.info(f"[{self.ip}] 正在删除人员 {user_id}...")
            resp = requests.post(url, json=payload, headers=self.headers, timeout=10)
            if resp.status_code == 200:
                res_json = resp.json()
                if res_json.get("detail") == "success" or res_json.get("status") == 0:
                    return True, "成功"
                else:
                    return False, f"设备拒绝: {res_json.get('detail')}"
        except Exception as e:
            return False, f"连接错误: {e}"
        return False, "未知错误"

    def query_persons(self, page=0, limit=10):
        """
        查询人员列表
        【调试增强版】会打印返回数据的键名，方便排查照片问题
        """
        url = f"{self.base_url}?timestamp={int(time.time()*1000)}&token={self.token}"
        payload = {
            "cmd": "DEV_FIND_FACES_BYPAGE",
            "payload": {
                "Search": {
                    "Pagesize": limit,
                    "Page": page,
                    "NeedImge": 1,   # 确保这里是 1
                    "UserName": "",
                    "CardNum": "",
                    "Person_uuid": "",
                    "WorkId": ""
                }
            }
        }
        try:
            resp = requests.post(url, json=payload, headers=self.headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                
                # ====== 调试代码：查看设备到底发了什么回来 ======
                try:
                    user_list = data.get("data", {}).get("Userlist", [])
                    if user_list and len(user_list) > 0:
                        first_user = user_list[0]
                        # 打印第一条数据的 key，看看有没有 image 或 images
                        print(f"\n[DEBUG] 设备 {self.ip} 返回的字段: {list(first_user.keys())}")
                        
                        has_img = 'images' in first_user or 'image' in first_user or 'photo' in first_user
                        print(f"[DEBUG] 是否包含图片数据: {has_img}\n")
                except Exception as e:
                    print(f"[DEBUG] 解析调试信息失败: {e}")
                # ============================================

                return True, data
            else:
                return False, f"HTTP错误: {resp.status_code}"
        except Exception as e:
            return False, f"连接错误: {e}"