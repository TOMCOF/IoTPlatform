import uvicorn
from fastapi import FastAPI, Request, Form, UploadFile, File, BackgroundTasks
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from datetime import datetime
import json
import os
import time
import threading
import pymssql
from PIL import Image
from dotenv import load_dotenv
import base64
import requests
import shutil
import database
import sys

# 引入工厂模式驱动
from drivers import get_device_driver

load_dotenv()

# ================= 配置区域 =================
SOURCE_PHOTO_DIR = os.getenv("PHOTO_DIR", "C:/Photos")
SQL_CONFIG = {
    "server": os.getenv("DB_SERVER", "127.0.0.1"),
    "user": os.getenv("DB_USER", "sa"),
    "password": os.getenv("DB_PASSWORD", "password"),
    "database": os.getenv("DB_NAME", "AccessControlDB")
}
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin888")
SECRET_KEY = os.getenv("SECRET_KEY", "secret123")
OFFLINE_THRESHOLD = 60

BASE_DIRS = {
    "data": "data",
    "log": "logs/log",
    "photos": "photos",
    "temp": "temp"
}

for d in BASE_DIRS.values():
    if not os.path.exists(d): os.makedirs(d)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# ================= 内存变量 =================
online_devices = {}
system_logs_memory = []
device_locks = set()
io_lock = threading.Lock()

# ================= 文件与日志系统 =================

def get_day_dir(category):
    now = datetime.now()
    path = os.path.join(BASE_DIRS[category], str(now.year), str(now.month), str(now.day))
    if not os.path.exists(path): os.makedirs(path)
    return path

def write_file(filepath, content):
    with io_lock:
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(content + "\n")
        except Exception: pass

def record_log(msg, sn=None, level="INFO"):
    now = datetime.now()
    full_msg = f"[{now.strftime('%H:%M:%S')}] [{level}] {msg}"
    print(full_msg)
    system_logs_memory.insert(0, full_msg)
    if len(system_logs_memory) > 50: system_logs_memory.pop()
    try:
        log_dir = get_day_dir("log")
        write_file(os.path.join(log_dir, "system.log"), full_msg)
        if sn: write_file(os.path.join(log_dir, f"{sn}.log"), full_msg)
    except Exception: pass

def record_data(sn, data_content):
    try:
        data_dir = get_day_dir("data")
        write_file(os.path.join(data_dir, f"{sn}_records.txt"), json.dumps(data_content, ensure_ascii=False))
    except Exception: pass

def save_event_photo(sn, base64_str, user_name):
    if not base64_str: return None
    try:
        photo_dir = get_day_dir("photos")
        filename = f"{datetime.now().strftime('%H%M%S%f')[:9]}_{sn}_{user_name}.jpg"
        filepath = os.path.join(photo_dir, filename)
        
        if "," in base64_str: base64_str = base64_str.split(",")[-1]
        base64_str = base64_str.strip().replace("\n", "").replace("\r", "").replace(" ", "")
        remainder = len(base64_str) % 4
        if remainder: base64_str += "=" * (4 - remainder)

        with open(filepath, 'wb') as f: f.write(base64.urlsafe_b64decode(base64_str))
        return filepath
    except Exception: return None

# ================= 辅助函数：下载与转换 =================
def download_img_from_device(ip, url_path):
    if not url_path: return None
    try:
        if not url_path.startswith("/"): url_path = "/" + url_path
        if not url_path.startswith("http"):
            full_url = f"http://{ip}:8086{url_path}"
        else:
            full_url = url_path
        
        resp = requests.get(full_url, timeout=3)
        if resp.status_code == 200:
            return base64.b64encode(resp.content).decode('utf-8')
    except Exception: pass
    return None

def download_file_to_local(ip, url_path, save_path):
    if not url_path: return False
    try:
        if not url_path.startswith("/"): url_path = "/" + url_path
        if not url_path.startswith("http"):
            full_url = f"http://{ip}:8086{url_path}"
        else:
            full_url = url_path
            
        resp = requests.get(full_url, timeout=2)
        if resp.status_code == 200:
            with open(save_path, 'wb') as f:
                f.write(resp.content)
            return True
    except Exception: pass
    return False

# ================= 数据库交互业务 =================

def update_device_status_db(sn, ip):
    try:
        sn_int = int(sn)
    except ValueError: return 
    conn = None
    try:
        conn = pymssql.connect(**SQL_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT Clock_id FROM Clocks WHERE Clock_id = %d", sn_int)
        if cursor.fetchone():
            cursor.execute("UPDATE Clocks SET TCPIP_Address = %s, LastConDatetime = %s WHERE Clock_id = %d", 
                           (ip, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sn_int))
        else:
            cursor.execute("INSERT INTO Clocks (Clock_id, Clock_type, TCPIP_Address, LastConDatetime, Clock_name) VALUES (%d, '3', %s, %s, %s)",
                           (sn_int, ip, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), f"Dev_{sn}"))
        conn.commit()
    except Exception as e: record_log(f"DB状态更新失败: {e}", sn, "ERROR")
    finally:
        if conn: conn.close()

def insert_passtime_db(sn, user_id, pass_time_str):
    try:
        clock_id = int(sn)
    except ValueError: return
    conn = None
    try:
        conn = pymssql.connect(**SQL_CONFIG)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO PassTime (emp_id, passTime, clock_id) VALUES (%s, %s, %d)", 
                       (user_id, pass_time_str, clock_id))
        conn.commit()
    except Exception: pass
    finally:
        if conn: conn.close()

# ================= 【核心逻辑】备份与回滚 =================

def backup_device_user_photo(driver, uid, ip):
    try:
        success, data = driver.query_persons() 
        if not success: return None
        
        user_list = data.get("data", {}).get("Userlist", [])
        target_user = next((u for u in user_list if str(u.get("userId") or u.get("workId")) == str(uid)), None)
        
        if not target_user: return None

        img_url = None
        if target_user.get("faces") and len(target_user.get("faces")) > 0:
            img_url = target_user["faces"][0].get("imgurl")
        elif target_user.get("images") and len(target_user.get("images")) > 0:
            img_url = target_user["images"][0].get("imgurl")
        
        if img_url:
            backup_path = os.path.join(BASE_DIRS["temp"], f"backup_{uid}_{int(time.time())}.jpg")
            if download_file_to_local(ip, img_url, backup_path):
                return backup_path
    except Exception as e:
        print(f"备份照片失败: {e}")
    return None

def sync_worker_task(device_sn, device_ip):
    """人员同步逻辑（已修复 TypeError 崩溃循环）"""
    if device_sn in device_locks: return
    device_locks.add(device_sn)
    
    clock_id = device_sn 
    conn = None
    try:
        conn = pymssql.connect(**SQL_CONFIG)
        cursor = conn.cursor(as_dict=True)
        
        sql = f"""
            SELECT [id], [Emp_id], [Emp_fname], [Kind], [Card_id], [PassWord], [errcount]
            FROM AssignEmp 
            WHERE [Clock_id] = '{clock_id}' AND [Kind] IN (0, 1)
        """
        cursor.execute(sql)
        tasks = cursor.fetchall()
        
        if not tasks: return 
        
        record_log(f" [同步] 发现 {len(tasks)} 个任务", device_sn)
        driver = get_device_driver("haiou", device_ip)
        
        for task in tasks:
            row_id = task['id']
            uid = str(task['Emp_id']).strip()
            name = str(task['Emp_fname']).strip()
            kind = task['Kind']
            
            current_err_count = task['errcount'] if task['errcount'] else 0
            card_id = str(task['Card_id']).strip() if task['Card_id'] else ""
            password = str(task['PassWord']).strip() if task['PassWord'] else ""
            
            is_success = False
            log_detail = ""
            action_msg = ""
            
            # =================== 【逻辑分支：新增/修改】 ===================
            if kind == 0: 
                local_path_jpg = os.path.join(SOURCE_PHOTO_DIR, f"{uid}.jpg")
                local_path_png = os.path.join(SOURCE_PHOTO_DIR, f"{uid}.png")
                final_photo_path = local_path_jpg if os.path.exists(local_path_jpg) else (local_path_png if os.path.exists(local_path_png) else None)

                if final_photo_path:
                    # --- Step 1: 备份 ---
                    backup_photo_path = backup_device_user_photo(driver, uid, device_ip)
                    if backup_photo_path:
                         # 使用 record_log 方便在网页查看 DEBUG 信息
                        record_log(f"[备份] 任务 {uid} 备份成功", device_sn, "DEBUG")

                    # --- Step 2: 强制清理 ---
                    try:
                        driver.delete_person(uid)
                        time.sleep(0.1) 
                    except: pass

                    # --- Step 3: 下发新数据 ---
                    upload_ok, upload_msg = driver.add_person(uid, name, final_photo_path, card_id=card_id, password=password)
                    
                    # --- Step 4: 验证结果 ---
                    check_ok = False
                    if upload_ok:
                        time.sleep(0.5)
                        check_ok, _ = driver.check_person_exists(uid)
                    
                    if upload_ok and check_ok:
                        is_success = True
                        action_msg = "下发并验证成功"
                        if backup_photo_path and os.path.exists(backup_photo_path):
                            os.remove(backup_photo_path)
                    else:
                        # --- Step 5: 失败回滚 (Rollback) [已修复崩溃点] ---
                        is_success = False
                        err_reason = upload_msg if not upload_ok else "验证失败(照片可能被拒)"
                        action_msg = f"更新失败: {err_reason} -> 正在回滚..."
                        
                        # 确保 rollback_photo 不是 None，否则 driver 可能会崩
                        rollback_photo = backup_photo_path if (backup_photo_path and os.path.exists(backup_photo_path)) else None
                        
                        try:
                            # 只有当照片路径有效时才调用带照片的接口
                            if rollback_photo:
                                rb_ok, rb_msg = driver.add_person(uid, name, rollback_photo, card_id=card_id, password=password)
                            else:
                                # 如果没有备份照片，我们不传 None，否则会报 TypeError
                                # 这里直接标记回滚失败，或者如果驱动支持纯文字更新可以尝试传空字符串
                                rb_ok, rb_msg = False, "无有效备份照片，无法自动恢复人脸"
                                
                            if rb_ok:
                                action_msg += " [旧数据恢复成功]"
                            else:
                                action_msg += f" [回滚失败: {rb_msg}]"
                        except Exception as e:
                            # 捕获回滚时的所有异常，防止死循环
                            action_msg += f" [回滚严重异常: {e}]"

                        if backup_photo_path and os.path.exists(backup_photo_path):
                            os.remove(backup_photo_path)

                else:
                    cursor.execute(f"UPDATE AssignEmp SET Kind = 2, Level = 401 WHERE id = {row_id}")
                    record_log(f"跳过 {name}: 无本地照片", device_sn, "WARNING")
                    conn.commit()
                    continue

            # =================== 【逻辑分支：删除】 ===================
            elif kind == 1: 
                is_success, action_msg = driver.delete_person(uid)
            
            # =================== 【数据库状态更新】 ===================
            # 这里的代码必须被执行到，errcount 增加才能停止死循环
            if is_success:
                new_kind = 2 if kind == 0 else 3
                cursor.execute(f"UPDATE AssignEmp SET Kind = {new_kind}, errcount = 0 , Level = 400 WHERE id = {row_id}")
                log_detail = f"{name}: {action_msg}"
            else:
                new_count = current_err_count + 1
                if new_count >= 3:
                    cursor.execute(f"UPDATE AssignEmp SET Kind = 2, errcount = {new_count}, Level = 404 WHERE id = {row_id}")
                    log_detail = f"{name} 失败: {action_msg} (累计{new_count}次，已停用)"
                else:
                    cursor.execute(f"UPDATE AssignEmp SET errcount = {new_count} WHERE id = {row_id}")
                    log_detail = f"{name} 失败: {action_msg} (重试 {new_count}/3)"
            
            conn.commit()
            record_log(log_detail, device_sn, "INFO" if is_success else "WARNING")
            
    except Exception as e:
        record_log(f"同步异常: {e}", device_sn, "ERROR")
    finally:
        if device_sn in device_locks: device_locks.remove(device_sn)
        if conn: conn.close()

# ================= 后台与启动 =================
def get_pending_task_count():
    conn = None
    try:
        conn = pymssql.connect(**SQL_CONFIG)
        cursor = conn.cursor()
        # 查询 Kind 为 0(新增) 或 1(删除)，且错误次数小于3的任务
        # 错误次数 >=3 的会被标记为故障，不再会被 sync_worker_task 抓取，所以不计入“待处理”
        cursor.execute("SELECT COUNT(*) FROM AssignEmp WHERE Kind IN (0, 1) AND (errcount IS NULL OR errcount < 3)")
        row = cursor.fetchone()
        return row[0] if row else 0
    except Exception as e:
        print(f"查询任务数失败: {e}")
        return 0
    finally:
        if conn: conn.close()

def device_watchdog():
    while True:
        try:
            now = datetime.now()
            for sn in list(online_devices.keys()):
                device = online_devices[sn]
                if device['status'] == 'online' and (now - device['_raw_time']).total_seconds() > OFFLINE_THRESHOLD:
                    online_devices[sn]['status'] = 'offline'
                    online_devices[sn]['last_seen'] = f"{device['last_seen']} (离线)"
                    record_log(f"⚠️ 设备 {sn} 离线", sn, "WARNING")
        except Exception: pass
        time.sleep(5)

@app.on_event("startup")
async def startup_event():
    database.init_db()
    threading.Thread(target=device_watchdog, daemon=True).start()
    record_log(" 系统启动完成 (V6.5 Safe Rollback Edition)")

# ================= 接口 =================

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in ["/login", "/heartbeat", "/event", "/docs", "/openapi.json", "/favicon.ico"] or path.startswith("/static"):
        return await call_next(request)
    if not request.session.get("user"): return RedirectResponse(url="/login")
    return await call_next(request)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request): return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login_submit(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        request.session["user"] = "admin"
        return {"code": 0, "msg": "登录成功"}
    return {"code": -1, "msg": "密码错误"}

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login")

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    total = len(online_devices)
    online_count = sum(1 for d in online_devices.values() if d['status'] == 'online')
    
    # 1. 调用上面的函数获取任务数
    pending_tasks = get_pending_task_count()
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request, 
        "devices": online_devices, 
        "logs": system_logs_memory,
        "count": total, 
        "online_count": online_count, 
        "offline_count": total - online_count,
        "task_count": pending_tasks  # 2. 将数据传递给前端，变量名叫 task_count
    })

@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request):
    raw_logs = database.get_all_logs(limit=100)
    parsed_logs = []
    for row in raw_logs:
        item = {"id": row[0], "sn": row[1], "time": row[2], "raw": row[3], "name": "未知", "img": None}
        try:
            d = json.loads(row[3].replace("'", '"'))
            inner = d.get("data", {})
            item["name"] = inner.get("UserName", "陌生人")
            b64 = inner.get("SignAvatarBase64") or inner.get("SnapBase64")
            if b64 and len(b64) > 100: item["img"] = b64
        except: pass
        parsed_logs.append(item)
    return templates.TemplateResponse("history.html", {"request": request, "logs": parsed_logs})

@app.post("/api/add_user")
async def api_add_user(
    sn: str = Form(...), name: str = Form(...), uid: str = Form(...),
    card_id: str = Form(""), password: str = Form(""), file: UploadFile = File(...)
):
    info = online_devices.get(sn)
    if not info or info['status'] == 'offline': return {"code": 404, "msg": "设备离线"}
    
    save_path = os.path.join(BASE_DIRS["temp"], f"upload_{uid}.jpg")
    try:
        img = Image.open(file.file).convert("RGB")
        img.thumbnail((600, 600)) 
        img.save(save_path, format="JPEG", quality=90)
    except Exception as e: return {"code": 500, "msg": f"图片处理错误: {e}"}
    
    backup_path = None
    try:
        driver = get_device_driver("haiou", info['ip'])
        
        # Step 1: 备份
        backup_path = backup_device_user_photo(driver, uid, info['ip'])
        if backup_path:
            record_log(f"手动下发备份成功", sn, "DEBUG")

        # Step 2: 强制清理
        driver.delete_person(uid)
        time.sleep(0.2)

        # Step 3: 尝试下发
        upload_ok, upload_msg = driver.add_person(uid, name, save_path, card_id=card_id, password=password)
        
        # Step 4: 验证
        check_ok = False
        if upload_ok:
            time.sleep(1.0) 
            check_ok, check_msg = driver.check_person_exists(uid)

        # Step 5: 结果处理
        if upload_ok and check_ok:
            record_log(f"手动下发 {name} 成功", sn, "INFO")
            if backup_path and os.path.exists(backup_path): os.remove(backup_path)
            if os.path.exists(save_path): os.remove(save_path)
            return {"code": 0, "msg": "下发并验证成功"}
        
        else:
            # --- 失败回滚 [修复版] ---
            fail_reason = upload_msg if not upload_ok else f"照片可能不合格({check_msg})"
            record_log(f"手动下发 {name} 失败: {fail_reason}，正在回滚...", sn, "WARNING")
            
            rb_ok = False
            rb_msg = ""
            rollback_photo = backup_path if (backup_path and os.path.exists(backup_path)) else None
            
            if rollback_photo:
                rb_ok, rb_msg = driver.add_person(uid, name, rollback_photo, card_id=card_id, password=password)
            else:
                rb_ok, rb_msg = False, "无有效备份照片"

            rollback_status = "成功" if rb_ok else f"失败({rb_msg})"
            
            if backup_path and os.path.exists(backup_path): os.remove(backup_path)
            if os.path.exists(save_path): os.remove(save_path)
            
            return {
                "code": -1, 
                "msg": f"下发失败: {fail_reason}。自动回滚: {rollback_status}"
            }

    except Exception as e: 
        if os.path.exists(save_path): os.remove(save_path)
        if backup_path and os.path.exists(backup_path): os.remove(backup_path)
        return {"code": 500, "msg": f"驱动异常: {e}"}

@app.get("/api/del_user")
def api_del_user(sn: str, uid: str):
    info = online_devices.get(sn)
    if not info or info['status'] == 'offline': return {"code": 404, "msg": "离线"}
    try:
        driver = get_device_driver("haiou", info['ip'])
        success, msg = driver.delete_person(uid)
        record_log(f"手动删除 {uid}: {msg}", sn)
        return {"code": 0 if success else -1, "msg": msg}
    except Exception as e: return {"code": 500, "msg": str(e)}

@app.get("/api/remove_device")
def api_remove_device(sn: str):
    if sn in online_devices:
        del online_devices[sn]
        return {"code": 0, "msg": "已移除"}
    return {"code": 404, "msg": "不存在"}

@app.get("/api/query_users")
def api_query_users(sn: str):
    info = online_devices.get(sn)
    if not info or info['status'] == 'offline': return {"code": 404, "msg": "离线"}
    
    try:
        driver = get_device_driver("haiou", info['ip'])
        success, data = driver.query_persons(limit=10)
        
        if success:
            res = []
            user_list = data.get("data", {}).get("Userlist", [])
            for p in user_list:
                img_data = None
                if p.get("faces") and len(p.get("faces")) > 0:
                    face = p.get("faces")[0]
                    if face.get("data"): img_data = face.get("data")
                    elif face.get("imgurl"): img_data = download_img_from_device(info['ip'], face.get("imgurl"))

                elif p.get("images") and len(p.get("images")) > 0:
                    img_data = p.get("images")[0].get("data")
                elif p.get("image"):
                    img_data = p.get("image")
                elif p.get("photo"):
                    img_data = p.get("photo")

                res.append({
                    "uid": p.get("workId") or p.get("userId"),
                    "name": p.get("name", "未知"),
                    "img": img_data, 
                    "type": 1
                })
            return {"code": 0, "data": res}
    except Exception as e:
        return {"code": 500, "msg": str(e)}
        
    return {"code": -1, "msg": "查询失败"}

@app.post("/heartbeat")
async def handle_heartbeat(request: Request, background_tasks: BackgroundTasks):
    try:
        client_ip = request.client.host
        data = await request.json()
        payload = data.get("data", {})
        sn = payload.get("DevSN") or payload.get("deviceid")
        
        if sn:
            now = datetime.now()
            if sn not in online_devices: record_log(f" 新设备: {client_ip}", sn)
            elif online_devices[sn]['status'] == 'offline': record_log(f" 设备重连", sn)

            online_devices[sn] = {
                "ip": client_ip,
                "last_seen": now.strftime('%H:%M:%S'),
                "_raw_time": now,
                "status": "online"
            }
            background_tasks.add_task(sync_worker_task, sn, client_ip)
            background_tasks.add_task(update_device_status_db, sn, client_ip)
        return {"code": 200, "msg": "OK"}
    except: return {"code": 500}

@app.post("/event")
async def handle_event(request: Request, background_tasks: BackgroundTasks):
    try:
        data = await request.json()
        payload = data.get("data", {})
        sn = payload.get("DevSN") or "Unknown"
        name = payload.get("UserName", "陌生人")
        uid = payload.get("UserID") or "STRANGER"
        
        raw_time = str(payload.get("SignTime", ""))
        pass_time = datetime.fromtimestamp(int(raw_time)).strftime("%Y-%m-%d %H:%M:%S") if raw_time.isdigit() else datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        record_data(sn, data)
        save_event_photo(sn, payload.get("SignAvatarBase64") or payload.get("SnapBase64"), name)
        
        log_msg = f"📷 [通行] {name}({uid}) 在 {pass_time}"
        record_log(log_msg, sn)
        try: database.save_log(sn, json.dumps(data))
        except: pass

        if sn != "Unknown":
            background_tasks.add_task(insert_passtime_db, sn, uid, pass_time)

        return {"code": 200, "msg": "OK"}
    except: return {"code": 500}

if __name__ == "__main__":
    print(">>> 平台启动")
    uvicorn.run(app, host="0.0.0.0", port=8000)