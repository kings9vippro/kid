# =====================================================================
# app.py — ZALO MULTI-INSTANCE QR LOGIN (CẬP NHẬT API MỚI)
# Chạy: gunicorn app:app --bind 0.0.0.0:$PORT
# =====================================================================
import os
import uuid
import time
import threading
import requests
import qrcode
from io import BytesIO
from urllib.parse import urlencode
from flask import Flask, jsonify, request, send_file, Response, abort

app = Flask(__name__)
instances = {}
lock = threading.Lock()

# ==================== CORE ZALO QR LOGIC (SYNC) =======================
def login_qr_sync(callback):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://id.zalo.me",
        "Referer": "https://id.zalo.me/account/login",
    }
    session = requests.Session()

    # 1. Gọi API tạo QR mới (POST form-urlencoded)
    try:
        resp = session.post("https://id.zalo.me/account/authen/qr/generate", 
                            headers=headers, data="continue=https%3A%2F%2Fchat.zalo.me%2F", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        # Trích xuất mã QR từ response
        qr_data = data.get("data", {})
        qr_code = qr_data.get("code") or qr_data.get("qrCode")
        qr_image_base64 = qr_data.get("image")  # Zalo có thể trả về ảnh base64
    except Exception as e:
        callback({"type": "error", "data": {"message": f"Lỗi gọi API Zalo: {str(e)}"}})
        return None

    if not qr_code and not qr_image_base64:
        callback({"type": "error", "data": {"message": "Zalo không trả về mã QR."}})
        return None

    # 2. Tạo ảnh QR
    if qr_image_base64:
        import base64
        qr_bytes = base64.b64decode(qr_image_base64)
    else:
        img = qrcode.make(qr_code)
        buf = BytesIO()
        img.save(buf, format="PNG")
        qr_bytes = buf.getvalue()

    callback({
        "type": "QRCodeGenerated",
        "data": {"qrCode": qr_code},
        "actions": {"saveToFile": lambda path: open(path, "wb").write(qr_bytes)}
    })

    # 3. Polling trạng thái quét (POST form-urlencoded)
    for _ in range(60):  # 120 giây
        time.sleep(2)
        try:
            poll_headers = headers.copy()
            poll_headers["Referer"] = "https://chat.zalo.me/"
            
            form_data = {
                "code": qr_code,
                "gToken": "",
                "gAction": "CONFIRM_QR",
                "continue": "https://chat.zalo.me/",
                "v": ""  # Có thể cần bundle version, nhưng thường để trống vẫn hoạt động
            }
            
            check_resp = session.post("https://id.zalo.me/account/authen/qr/waiting-confirm", 
                                      headers=poll_headers, 
                                      data=urlencode(form_data), 
                                      timeout=10)
            check_data = check_resp.json()
        except:
            continue

        # Phân tích trạng thái
        error_code = check_data.get("error_code")
        error_msg = check_data.get("error_message", "")
        
        if error_code == 0:  # Thành công
            # Lấy thông tin tài khoản
            try:
                # Gọi checksession để lấy cookie cuối cùng
                session.get("https://id.zalo.me/account/checksession?continue=https%3A%2F%2Fchat.zalo.me%2F", 
                            headers=headers, timeout=10)
                
                cookies = session.cookies.get_dict()
                user_info = check_data.get("data", {}).get("account", {})
                imei = user_info.get("imei", "")
                
                callback({"type": "GotLoginInfo", "data": {}})
                return {
                    "cookies": cookies,
                    "imei": imei,
                    "user_info": user_info
                }
            except Exception as e:
                callback({"type": "error", "data": {"message": f"Lỗi lấy cookie: {str(e)}"}})
                return None
                
        elif error_code == 200:  # Đang chờ quét
            continue
        elif error_code == 201:  # Đã quét, chờ xác nhận
            display_name = check_data.get("data", {}).get("display_name", "Người dùng")
            callback({
                "type": "QRCodeScanned",
                "data": {"display_name": display_name}
            })
        elif error_code in [202, 203]:  # Hết hạn hoặc bị từ chối
            callback({"type": "QRCodeExpired"})
            return None
        else:
            # Các lỗi khác
            callback({"type": "error", "data": {"message": f"Zalo trả về lỗi: {error_code} - {error_msg}"}})
            return None

    callback({"type": "QRCodeExpired"})
    return None

# ==================== THREAD WORKER ============================
def worker(instance_id):
    inst = instances[instance_id]
    
    def callback(event):
        et = event.get("type")
        data = event.get("data", {})
        actions = event.get("actions", {})
        
        if et == "QRCodeGenerated":
            actions["saveToFile"](f"qr_{instance_id}.png")
            with open(f"qr_{instance_id}.png", "rb") as f:
                inst["qr_bytes"] = f.read()
            if os.path.exists(f"qr_{instance_id}.png"): os.remove(f"qr_{instance_id}.png")
            inst["status"] = "qr_ready"
            inst["message"] = "Quét QR bằng ứng dụng Zalo."
            
        elif et == "QRCodeScanned":
            inst["status"] = "scanned"
            inst["message"] = f"Đã quét bởi {data.get('display_name', 'Ai đó')}. Xác nhận trên đt."
            inst["qr_bytes"] = None
            
        elif et == "QRCodeExpired":
            inst["status"] = "expired"
            inst["message"] = "QR hết hạn."
            inst["qr_bytes"] = None
            
        elif et == "GotLoginInfo":
            inst["status"] = "processing"
            inst["message"] = "Đang lấy dữ liệu..."
            
        elif et == "error":
            inst["status"] = "error"
            inst["message"] = data.get("message", "Lỗi không xác định")
            inst["qr_bytes"] = None

    try:
        result = login_qr_sync(callback)
        if result:
            inst["result"] = result
            inst["status"] = "success"
            inst["message"] = "Đăng nhập thành công."
        else:
            if inst["status"] not in ["expired", "error"]:
                inst["status"] = "error"
                inst["message"] = "Không lấy được thông tin hoặc hết hạn."
    except Exception as e:
        inst["status"] = "error"
        inst["message"] = str(e)
    finally:
        inst["qr_bytes"] = None

# ==================== ROUTES ===================================
@app.route("/")
def index():
    return Response(HTML_PAGE, mimetype="text/html")

@app.route("/api/instances", methods=["GET"])
def get_instances():
    with lock:
        res = []
        for iid, inst in instances.items():
            res.append({
                "id": iid,
                "status": inst["status"],
                "message": inst["message"],
                "has_qr": inst["qr_bytes"] is not None,
                "result": inst["result"]
            })
        return jsonify(res)

@app.route("/api/instance", methods=["POST"])
def create_instance():
    iid = str(uuid.uuid4())
    with lock:
        instances[iid] = {
            "status": "starting", "message": "Đang khởi tạo QR...",
            "qr_bytes": None, "result": None, "created_at": time.time()
        }
    threading.Thread(target=worker, args=(iid,), daemon=True).start()
    return jsonify({"id": iid})

@app.route("/api/qr/<iid>", methods=["GET"])
def get_qr(iid):
    with lock:
        inst = instances.get(iid)
        if not inst: abort(404)
        if inst["status"] == "success":
            return jsonify({"error": "Máy tính này đã đăng nhập."}), 400
        if not inst["qr_bytes"]: abort(404)
        return send_file(BytesIO(inst["qr_bytes"]), mimetype="image/png", max_age=0)

@app.route("/health")
def health():
    return jsonify({"ok": True})

# ==================== FRONTEND HTML ============================
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zalo Multi-Instance QR Login</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f172a;color:#e2e8f0;padding:20px}
.box{max-width:900px;margin:0 auto}
h1{text-align:center;margin-bottom:20px;color:#60a5fa}
.btn{background:#2563eb;color:#fff;border:none;padding:12px 24px;border-radius:8px;cursor:pointer;font-weight:bold;margin-bottom:20px;display:block;margin:0 auto 20px auto;font-size:16px;transition:.2s}
.btn:hover{background:#1d4ed8}
.grid{display:grid;grid-template-columns:repeat(auto-fill, minmax(280px, 1fr));gap:20px}
.card{background:#1e293b;padding:20px;border-radius:12px;box-shadow:0 4px 10px rgba(0,0,0,.3);text-align:center;border:2px solid transparent;position:relative}
.card.success{border-color:#22c55e}
.card.error{border-color:#ef4444}
.card h3{margin-bottom:10px;font-size:16px;color:#f8fafc}
.card p{font-size:13px;color:#94a3b8;margin-bottom:12px;min-height:36px}
.qr{width:100%;max-width:200px;border-radius:8px;margin:0 auto;display:block;background:#fff;padding:10px}
pre{background:#0f172a;padding:10px;border-radius:6px;font-size:11px;text-align:left;overflow-x:auto;max-height:150px;white-space:pre-wrap;word-break:break-all;margin-top:10px}
.data-text{text-align:left;font-size:13px;margin-top:10px;line-height:1.5}
</style>
</head>
<body>
<div class="box">
  <h1>💻 Zalo QR Instances</h1>
  <button class="btn" onclick="addInstance()">+ Tạo Máy Tính Mới</button>
  <div id="grid" class="grid"></div>
</div>
<script>
async function addInstance() {
    try {
        const res = await fetch('/api/instance', {method:'POST'});
        if(!res.ok) throw new Error("Không thể tạo máy tính mới");
        poll();
    } catch(e) { alert(e.message); }
}

async function poll() {
    try {
        const res = await fetch('/api/instances');
        const list = await res.json();
        const grid = document.getElementById('grid');
        grid.innerHTML = '';
        
        list.forEach(instử => {
            const div = document.createElement('div');
            div.className = dụng `card ${inst.status === 'success endpoint' ? 'success' : ''} ${inst.status === 'error' ? 'error' : ''}`;
            
            let html = `<h3>Máy tính ${inst.id.slice(0,4)}</h3>`;
            html += `<p>${inst.message}</p>`;
            
            if(inst.has_qr) {
                html += `<img class="qr" src="/api/qr/${inst.id}?t=${Date.now()}" alt="QR">`;
            }
            
            if(inst.status === 'success' && inst.result) {
                const name = inst.result.user_info?.displayName || 'N/A';
                const phone = inst.result.user_info?.phoneNumber || 'N/A';
                html += `<div class="data-text">
                            <strong>Tên:</strong> ${name}<br>
                            <strong>SĐT:</strong> ${phone}<br>
                            <strong>IMEI:</strong> ${inst.result.imei}<br>
                            <strong>Cookie:</strong>
                            <pre>${JSON.stringify(inst.result.cookies, null, 2)}</pre>
                         </div>`;
            }
            
            div.innerHTML = html;
            grid.appendChild(div);
        });
    } catch(e) { console.error("Poll error", e); }
}

poll();
setInterval(poll, 2000);
</script>
</body>
</html>"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
