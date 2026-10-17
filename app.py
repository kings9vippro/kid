# =====================================================================
# app.py — ZALO LOGIN (PHONE/PASSWORD & QR CODE)
# =====================================================================
import os
import uuid
import time
import threading
import requests
import qrcode
import base64
from io import BytesIO
from urllib.parse import urlencode
from flask import Flask, jsonify, request, send_file, Response, abort

app = Flask(__name__)
instances = {}
lock = threading.Lock()

# ==================== CORE ZALO LOGIN LOGIC =======================
def login_with_password(phone, password):
    """Đăng nhập bằng SĐT và Mật khẩu (Có thể bị Zalo chặn)"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://id.zalo.me",
        "Referer": "https://id.zalo.me/account/login",
        "zpw_ver": "648",
        "zpw_type": "30"
    }
    session = requests.Session()
    
    # Bước 1: Lấy cookie mồi
    try:
        session.get("https://id.zalo.me/account/login", headers=headers, timeout=10)
    except:
        pass

    # Bước 2: Gửi request đăng nhập
    try:
        # Zalo có thể yêu cầu mã hóa RSA ở bước này, ta gửi dạng raw form
        payload = {
            "phone": phone,
            "password": password,
            "continue": "https://chat.zalo.me/",
            "v": "241123"
        }
        
        resp = session.post("https://id.zalo.me/account/login", 
                            headers=headers, 
                            data=urlencode(payload), 
                            timeout=15)
        
        # Zalo thường trả về HTML nếu sai pass hoặc bắt OTP, hoặc JSON nếu thành công
        if "application/json" in resp.headers.get("Content-Type", ""):
            data = resp.json()
            if data.get("error_code") == 0:
                cookies = session.cookies.get_dict()
                user_info = data.get("data", {}).get("account", {})
                return {
                    "success": True,
                    "cookies": cookies,
                    "imei": user_info.get("imei", "N/A"),
                    "user_info": user_info
                }
            else:
                return {"success": False, "error": data.get("error_message", "Sai SĐT hoặc Mật khẩu.")}
        else:
            # Bị chặn, bắt OTP hoặc Zalo trả về HTML
            return {"success": False, "error": "Zalo yêu cầu xác minh (OTP) hoặc đã chặn IP máy chủ."}
            
    except Exception as e:
        return {"success": False, "error": f"Lỗi kết nối: {str(e)}"}

# ==================== ROUTES ===================================
@app.route("/")
def index():
    return Response(HTML_PAGE, mimetype="text/html")

@app.route("/api/login_pass", methods=["POST"])
def api_login_pass():
    data = request.json or {}
    phone = data.get("phone", "").strip()
    password = data.get("password", "").strip()
    
    if not phone or not password:
        return jsonify({"error": "Vui lòng nhập đầy đủ SĐT và Mật khẩu."}), 400
        
    res = login_with_password(phone, password)
    if res["success"]:
        return jsonify(res)
    else:
        return jsonify({"error": res["error"]}), 401

# -------------------- QR LOGIC (Giữ nguyên) --------------------
def login_qr_sync(callback):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://id.zalo.me",
        "Referer": "https://id.zalo.me/account/login",
        "zpw_ver": "648",
        "zpw_type": "30"
    }
    session = requests.Session()

    try:
        session.get("https://id.zalo.me/account/login", headers=headers, timeout=10)
        resp = session.post("https://id.zalo.me/account/authen/qr/generate", 
                            headers=headers, 
                            data="v=241123&continue=https%3A%2F%2Fchat.zalo.me%2F", 
                            timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        qr_data = data.get("data", {})
        qr_code = qr_data.get("code")
        qr_image_base64 = qr_data.get("image")
    except Exception as e:
        callback({"type": "error", "data": {"message": f"Zalo chặn kết nối: {str(e)}"}})
        return None

    if not qr_code:
        callback({"type": "error", "data": {"message": "Zalo từ chối cấp mã QR."}})
        return None

    qr_bytes = None
    if qr_image_base64:
        try:
            if "," in qr_image_base64:
                qr_image_base64 = qr_image_base64.split(",")[1]
            qr_image_base64 += "=" * ((4 - len(qr_image_base64) % 4) % 4)
            qr_bytes = base64.b64decode(qr_image_base64)
        except Exception:
            qr_bytes = None

    if not qr_bytes and qr_code:
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=4)
        qr.add_data(qr_code if qr_code.startswith("zalo://") else f"zalo://login?code={qr_code}")
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = BytesIO()
        img.save(buf, format="PNG")
        qr_bytes = buf.getvalue()

    callback({
        "type": "QRCodeGenerated",
        "data": {"qrCode": qr_code},
        "actions": {"saveToFile": lambda path: open(path, "wb").write(qr_bytes)}
    })

    for _ in range(150):  
        time.sleep(2)
        try:
            poll_headers = headers.copy()
            poll_headers["Referer"] = "https://chat.zalo.me/"
            form_data = {"code": qr_code, "gToken": "", "gAction": "CONFIRM_QR", "continue": "https://chat.zalo.me/", "v": "241123"}
            
            check_resp = session.post("https://id.zalo.me/account/authen/qr/waiting-confirm", 
                                      headers=poll_headers, data=urlencode(form_data), timeout=10)
            check_data = check_resp.json()
        except:
            continue

        error_code = check_data.get("error_code")
        
        if error_code == 0:
            try:
                session.get("https://id.zalo.me/account/checksession?continue=https%3A%2F%2Fchat.zalo.me%2F", headers=headers, timeout=10)
                cookies = session.cookies.get_dict()
                user_info = check_data.get("data", {}).get("account", {})
                callback({"type": "GotLoginInfo", "data": {}})
                return {"cookies": cookies, "imei": user_info.get("imei", "N/A"), "user_info": user_info}
            except Exception as e:
                callback({"type": "error", "data": {"message": f"Lỗi lấy cookie: {str(e)}"}})
                return None
        elif error_code == 200:
            continue
        elif error_code == 201:
            callback({"type": "QRCodeScanned", "data": {"display_name": check_data.get("data", {}).get("display_name", "Người dùng")}})
        elif error_code in [202, 203]:
            callback({"type": "QRCodeExpired"})
            return None

    callback({"type": "QRCodeExpired"})
    return None

def worker(instance_id):
    inst = instances[instance_id]
    def callback(event):
        et = event.get("type"); data = event.get("data", {}); actions = event.get("actions", {})
        if et == "QRCodeGenerated":
            actions["saveToFile"](f"qr_{instance_id}.png")
            with open(f"qr_{instance_id}.png", "rb") as f: inst["qr_bytes"] = f.read()
            if os.path.exists(f"qr_{instance_id}.png"): os.remove(f"qr_{instance_id}.png")
            inst["status"] = "qr_ready"; inst["message"] = "Quét QR bằng ứng dụng Zalo (Hết hạn sau 5 phút)."
        elif et == "QRCodeScanned":
            inst["status"] = "scanned"; inst["message"] = f"Đã quét bởi {data.get('display_name', 'Ai đó')}. Xác nhận trên đt."; inst["qr_bytes"] = None
        elif et == "QRCodeExpired":
            inst["status"] = "expired"; inst["message"] = "QR đã hết hạn."; inst["qr_bytes"] = None
        elif et == "GotLoginInfo":
            inst["status"] = "processing"; inst["message"] = "Đang lấy dữ liệu..."
        elif et == "error":
            inst["status"] = "error"; inst["message"] = data.get("message", "Lỗi không xác định"); inst["qr_bytes"] = None

    try:
        result = login_qr_sync(callback)
        if result:
            inst["result"] = result; inst["status"] = "success"; inst["message"] = "Đăng nhập thành công."
        else:
            if inst["status"] not in ["expired", "error"]:
                inst["status"] = "error"; inst["message"] = "Không lấy được thông tin."
    except Exception as e:
        inst["status"] = "error"; inst["message"] = str(e)
    finally:
        inst["qr_bytes"] = None

@app.route("/api/instances", methods=["GET"])
def get_instances():
    with lock:
        res = []
        for iid, inst in instances.items():
            res.append({"id": iid, "status": inst["status"], "message": inst["message"], "has_qr": inst["qr_bytes"] is not None, "result": inst["result"]})
        return jsonify(res)

@app.route("/api/instance", methods=["POST"])
def create_instance():
    iid = str(uuid.uuid4())
    with lock:
        instances[iid] = {"status": "starting", "message": "Đang khởi tạo QR...", "qr_bytes": None, "result": None, "created_at": time.time()}
    threading.Thread(target=worker, args=(iid,), daemon=True).start()
    return jsonify({"id": iid})

@app.route("/api/qr/<iid>", methods=["GET"])
def get_qr(iid):
    with lock:
        inst = instances.get(iid)
        if not inst: abort(404)
        if inst["status"] == "success": return jsonify({"error": "Đã đăng nhập."}), 400
        if not inst["qr_bytes"]: abort(404)
        return send_file(BytesIO(inst["qr_bytes"]), mimetype="image/png", max_age=0)

@app.route("/health")
def health(): return jsonify({"ok": True})

# ==================== FRONTEND HTML ============================
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zalo Multi-Login</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f172a;color:#e2e8f0;padding:20px}
.box{max-width:900px;margin:0 auto}
h1{text-align:center;margin-bottom:20px;color:#60a5fa}
.tabs{display:flex;justify-content:center;margin-bottom:20px;gap:10px}
.tab{background:#1e293b;color:#94a3b8;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;font-weight:bold;transition:.2s}
.tab.active{background:#2563eb;color:#fff}
.section{display:none}
.section.active{display:block}
.btn{background:#2563eb;color:#fff;border:none;padding:12px 24px;border-radius:8px;cursor:pointer;font-weight:bold;margin-bottom:20px;display:block;margin:0 auto 20px auto;font-size:16px;transition:.2s;width:100%}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn:hover:not(:disabled){background:#1d4ed8}
.input-group{margin-bottom:15px;max-width:400px;margin-left:auto;margin-right:auto}
.input-group label{display:block;margin-bottom:5px;color:#94a3b8;font-size:14px}
.input-group input{width:100%;padding:10px;border-radius:6px;border:1px solid #334155;background:#0f172a;color:#fff}
.grid{display:grid;grid-template-columns:repeat(auto-fill, minmax(280px, 1fr));gap:20px}
.card{background:#1e293b;padding:20px;border-radius:12px;box-shadow:0 4px 10px rgba(0,0,0,.3);text-align:center;border:2px solid transparent;position:relative}
.card.success{border-color:#22c55e}
.card.error{border-color:#ef4444}
.card h3{margin-bottom:10px;font-size:16px;color:#f8fafc}
.card p{font-size:13px;color:#94a3b8;margin-bottom:12px;min-height:36px}
.qr{width:100%;max-width:220px;border-radius:8px;margin:0 auto;display:block;background:#fff;padding:10px}
pre{background:#0f172a;padding:10px;border-radius:6px;font-size:11px;text-align:left;overflow-x:auto;max-height:150px;white-space:pre-wrap;word-break:break-all;margin-top:10px}
.data-text{text-align:left;font-size:13px;margin-top:10px;line-height:1.5}
.alert-error{background:#7f1d1d;color:#fca5a5;padding:10px;border-radius:6px;text-align:center;margin-bottom:15px;display:none}
</style>
</head>
<body>
<div class="box">
  <h1>💻 Zalo Multi-Login</h1>
  
  <div class="tabs">
    <button class="tab active" onclick="switchTab('pass')">Đăng nhập SĐT/Pass</button>
    <button class="tab" onclick="switchTab('qr')">Đăng nhập QR</button>
  </div>

  <!-- Tab Đăng nhập SĐT/Pass -->
  <div id="sec-pass" class="section active">
    <div id="pass-err" class="alert-error"></div>
    <div class="input-group">
        <label>Số điện thoại Zalo</label>
        <input type="text" id="phone" placeholder="09xxxxxxx">
    </div>
    <div class="input-group">
        <label>Mật khẩu</label>
        <input type="password" id="password" placeholder="••••••••">
    </div>
    <button id="btn-pass" class="btn" onclick="loginPass()">Đăng nhập ngay</button>
    
    <div id="pass-result" class="card success" style="display:none; margin-top:20px">
        <h3>Đăng nhập thành công</h3>
        <div class="data-text" id="pass-data"></div>
    </div>
  </div>

  <!-- Tab Đăng nhập QR -->
  <div id="sec-qr" class="section">
    <button id="btn-add" class="btn" onclick="addInstance()">+ Tạo Máy Tính Mới (QR)</button>
    <div id="grid" class="grid"></div>
  </div>

</div>
<script>
function switchTab(tab) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    event.target.classList.add('active');
    document.getElementById('sec-' + tab).classList.add('active');
}

async function loginPass() {
    const phone = document.getElementById('phone').value.trim();
    const password = document.getElementById('password').value.trim();
    const btn = document.getElementById('btn-pass');
    const err = document.getElementById('pass-err');
    const resBox = document.getElementById('pass-result');
    
    if(!phone || !password) { alert("Nhập đủ SĐT và Pass!"); return; }

    btn.disabled = true; btn.textContent = 'Đang xử lý...';
    err.style.display = 'none'; resBox.style.display = 'none';

    try {
        const res = await fetch('/api/login_pass', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({phone, password})
        });
        const data = await res.json();
        
        if(!res.ok) throw new Error(data.error || "Lỗi không xác định");

        // Thành công
        resBox.style.display = 'block';
        document.getElementById('pass-data').innerHTML = `
            <strong>Tên:</strong> ${data.user_info?.displayName || 'N/A'}<br>
            <strong>SĐT:</strong> ${data.user_info?.phoneNumber || 'N/A'}<br>
            <strong>IMEI:</strong> ${data.imei}<br>
            <strong>Cookie:</strong>
            <pre>${JSON.stringify(data.cookies, null, 2)}</pre>
        `;
        
    } catch(e) {
        err.textContent = e.message;
        err.style.display = 'block';
    } finally {
        btn.disabled = false; btn.textContent = 'Đăng nhập ngay';
    }
}

// ================== QR LOGIC ==================
async function addInstance() {
    const btn = document.getElementById('btn-add');
    btn.disabled = true; btn.textContent = 'Đang tạo...';
    
    try {
        const res = await fetch('/api/instance', {method:'POST'});
        const data = await res.json();
        if(!res.ok) throw new Error(data.error || "Lỗi máy chủ");
        await poll();
    } catch(e) { alert("Lỗi: " + e.message); }
    finally { btn.disabled = false; btn.textContent = '+ Tạo Máy Tính Mới (QR)'; }
}

async function poll() {
    try {
        const res = await fetch('/api/instances');
        const list = await res.json();
        const grid = document.getElementById('grid');
        
        if(grid.children.length !== list.length) grid.innerHTML = '';
        
        list.forEach(inst => {
            let div = document.getElementById(`card-${inst.id}`);
            if(!div) {
                div = document.createElement('div');
                div.id = `card-${inst.id}`;
                grid.appendChild(div);
            }
            div.className = `card ${inst.status === 'success' ? 'success' : ''} ${inst.status === 'error' ? 'error' : ''}`;
            
            let html = `<h3>Máy tính ${inst.id.slice(0,4)}</h3><p>${inst.message}</p>`;
            if(inst.has_qr) html += `<img class="qr" src="/api/qr/${inst.id}?t=${Date.now()}" alt="QR">`;
            
            if(inst.status === 'success' && inst.result) {
                html += `<div class="data-text">
                            <strong>Tên:</strong> ${inst.result.user_info?.displayName || 'N/A'}<br>
                            <strong>IMEI:</strong> ${inst.result.imei}<br>
                            <strong>Cookie:</strong>
                            <pre>${JSON.stringify(inst.result.cookies, null, 2)}</pre>
                         </div>`;
            }
            if(div.innerHTML !== html) div.innerHTML = html;
        });
    } catch(e) { console.error("Poll error", e); }
}
setInterval(poll, 2000);
</script>
</body>
</html>"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
