import os, uuid, json, time, asyncio, threading
from io import BytesIO
from flask import Flask, jsonify, request, send_file, Response, abort

try:
    from zalo_login import login_qr_with_unique_imei, LoginQRCallbackEventType
    ZALO_OK = True
    ZALO_ERR = ""
except Exception as e:
    ZALO_OK = False
    ZALO_ERR = str(e)

app = Flask(__name__)
instances = {}
lock = threading.Lock()

def worker(instance_id):
    inst = instances[instance_id]
    
    def callback(event):
        et = event.get("type")
        data = event.get("data", {})
        actions = event.get("actions", {})
        
        if et == LoginQRCallbackEventType.QRCodeGenerated:
            actions["saveToFile"](f"qr_{instance_id}.png")
            with open(f"qr_{instance_id}.png", "rb") as f:
                inst["qr_bytes"] = f.read()
            if os.path.exists(f"qr_{instance_id}.png"): os.remove(f"qr_{instance_id}.png")
            inst["status"] = "qr_ready"
            inst["message"] = "Quét QR bằng ứng dụng Zalo."
            
        elif et == LoginQRCallbackEventType.QRCodeScanned:
            inst["status"] = "scanned"
            inst["message"] = f"Đã quét bởi {data.get('display_name', 'Ai đó')}. Xác nhận trên đt."
            inst["qr_bytes"] = None
            
        elif et == LoginQRCallbackEventType.QRCodeExpired:
            inst["status"] = "expired"
            inst["message"] = "QR hết hạn."
            inst["qr_bytes"] = None
            
        elif et == LoginQRCallbackEventType.GotLoginInfo:
            inst["status"] = "processing"
            inst["message"] = "Đang lấy dữ liệu..."
            
        elif et == "error":
            inst["status"] = "error"
            inst["message"] = data.get("message", "Lỗi không xác định")
            inst["qr_bytes"] = None

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(asyncio.wait_for(login_qr_with_unique_imei(callback), timeout=180))
        loop.close()
        
        if result:
            inst["result"] = result
            inst["status"] = "success"
            inst["message"] = "Đăng nhập thành công."
        else:
            inst["status"] = "error"
            inst["message"] = "Không lấy được thông tin."
    except asyncio.TimeoutError:
        inst["status"] = "expired"
        inst["message"] = "Hết thời gian chờ."
    except Exception as e:
        inst["status"] = "error"
        inst["message"] = str(e)
    finally:
        inst["qr_bytes"] = None

@app.route("/")
def index():
    return Response(HTML_PAGE.replace("__ZALO_OK__", "1" if ZALO_OK else "0")
                             .replace("__ZALO_ERR__", ZALO_ERR), mimetype="text/html")

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
    if not ZALO_OK:
        return jsonify({"error": f"Module zalo_login lỗi: {ZALO_ERR}"}), 500
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
    return jsonify({"ok": True, "zalo": ZALO_OK})

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
.card{background:#1e293b;padding:20px;border-radius:12px;box-shadow:0 4px 10px rgba(0,0,0,.3);text-align:center;border:2px solid transparent}
.card.success{border-color:#22c55e}
.card.error{border-color:#ef4444}
.card h3{margin-bottom:10px;font-size:16px;color:#f8fafc}
.card p{font-size:13px;color:#94a3b8;margin-bottom:12px;min-height:36px}
.qr{width:100%;max-width:200px;border-radius:8px;margin:0 auto;display:block;background:#fff;padding:10px}
pre{background:#0f172a;padding:10px;border-radius:6px;font-size:11px;text-align:left;overflow-x:auto;max-height:150px;white-space:pre-wrap;word-break:break-all;margin-top:10px}
.data-text{text-align:left;font-size:13px;margin-top:10px;line-height:1.5}
.hidden{display:none}
.alert{color:#fca5a5;text-align:center;margin-bottom:20px;background:#7f1d1d;padding:12px;border-radius:8px}
</style>
</head>
<body>
<div class="box">
  <h1>💻 Zalo QR Instances</h1>
  <div id="warn" class="alert hidden"></div>
  <button class="btn" onclick="addInstance()">+ Tạo Máy Tính Mới</button>
  <div id="grid" class="grid"></div>
</div>
<script>
const ZALO_OK = __ZALO_OK__ === 1;
const ZALO_ERR = "__ZALO_ERR__";
if(!ZALO_OK){
    document.getElementById('warn').classList.remove('hidden');
    document.getElementById('warn').textContent = "Lỗi module Zalo: " + ZALO_ERR;
}

async function addInstance() {
    if(!ZALO_OK) return;
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
        
        list.forEach(inst => {
            const div = document.createElement('div');
            div.className = `card ${inst.status === 'success' ? 'success' : ''} ${inst.status === 'error' ? 'error' : ''}`;
            
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

if(ZALO_OK) {
    poll();
    setInterval(poll, 2000);
}
</script>
</body>
</html>"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
