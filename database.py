import sqlite3
import datetime

DB_NAME = "iot_data.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # 1. 通行记录表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS access_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_sn TEXT,
        log_time TEXT,
        raw_data TEXT
    )
    ''')

    # 2. 【新增】操作日志表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS op_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        op_time TEXT,
        op_type TEXT,   -- 操作类型：下发/删除/查询
        target_sn TEXT, -- 对哪台设备操作
        details TEXT    -- 详情：如下发了张三
    )
    ''')
    
    conn.commit()
    conn.close()

# --- 通行记录相关 ---
def save_log(device_sn, data_str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("INSERT INTO access_logs (device_sn, log_time, raw_data) VALUES (?, ?, ?)", (device_sn, now, str(data_str)))
    conn.commit()
    conn.close()

def get_all_logs(limit=20):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM access_logs ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return rows

# --- 【新增】操作日志相关 ---
def add_op_log(op_type, target_sn, details):
    """记录一条操作"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("INSERT INTO op_logs (op_time, op_type, target_sn, details) VALUES (?, ?, ?, ?)", 
                   (now, op_type, target_sn, details))
    conn.commit()
    conn.close()
    print(f"📝 [审计] {op_type}: {details}")

def get_op_logs(limit=50):
    """查询操作日志"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM op_logs ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return rows

if __name__ == "__main__":
    init_db()
    print("数据库结构升级完成！")