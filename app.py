# =====================================================================
# app.py — SINGLE-FILE Zalo QR Web trên Render
# Chạy: python app.py   ||   gunicorn app:app --bind 0.0.0.0:$PORT
# Cần đặt cạnh: zalo_login.py + package zlapi/ (đã có sẵn của bạn)
# =====================================================================
import os, io, time, uuid, json, asyncio, threading
from io import BytesIO
from datetime import datetime

from flask import Flask, jsonify, send_file, abort, Response
from PIL import Image, ImageDraw

# ---- Import zalo_login (module QR thật của bạn) ----
try:
    from zalo_login import login_qr_with_unique_imei, LoginQRCallbackEventType
    ZALO_OK = True
    ZALO_ERR = ""
except Exception as _e:
    ZALO_OK = False
    ZALO_ERR = str(_e)
    class LoginQRCallbackEventType:
        QRCodeGenerated = "QRCodeGenerated"
        QRCodeExpired   = "QRCodeExpired"
        QRCodeScanned   = "QRCodeScanned"
        QRCodeDeclined  = "QRCodeDeclined"
        GotLoginInfo    = "GotLoginInfo"

# ==================== CẤU HÌNH =====================
PORT          = int(os.environ.get("PORT", 5000))
SESSION_TTL   = 3600
LOGIN_TIMEOUT = 180
CACHE_DIR     = "cache"
os.makedirs(CACHE_DIR, exist_ok=True)

app           = Flask(__name__)
sessions      = {}
sessions_lock = threading.Lock()

# ==================== TIỆN ÍCH =====================
def add_border_to_qr(data: bytes) -> bytes:
    try:
        img = Image.open(BytesIO(data))
        if img.mode == "RGBA":
            img = img.convert("RGB")
        bw, r = 40, 80
        w, h  = img.size
        nw, nh = w + bw*2, h + bw*2
        bg = Image.new("RGB", (nw, nh), (255,255,255))
        bg.paste(img, (bw, bw))
        mask = Image.new("L", (nw, nh), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0,0,nw,nh], r, fill=255)
        out = BytesIO(); bg.save(out, "PNG"); return out.getvalue()
    except Exception:
        return data

def _get(sid):
    with sessions_lock:
        return sessions.get(sid)

def _rm(p):
    try:
        if os.path.exists(p): os.remove(p)
    except Exception: pass

def _cleanup():
    while True:
        now = time.time()
        with sessions_lock:
            for k in [k for k,v in sessions.items() if now - v["created_at"] > SESSION_TTL]:
                sessions.pop(k, None)
        time.sleep(60)

threading.Thread(target=_cleanup, daemon=True).start()

# ==================== LOGIN WORKER =================
def worker(sid):
    s = _get(sid)
    if not s: return
    qr_path = os.path.join(CACHE_DIR, f"qr_{sid}.png")

    def cb(event):
        et      = event.get("type")
        data    = event.get("data") or {}
        actions = event.get("actions") or {}

        if et == LoginQRCallbackEventType.QRCodeGenerated:
            try:
                actions["saveToFile"](qr_path)
                with open(qr_path, "rb") as f:
                    s["qr"] = add_border_to_qr(f.read())
                s["status"] = "qr_ready"
                s["msg"]    = "Quét QR bằng ứng dụng Zalo."
            except Exception as e:
                s["status"] = "error"; s["msg"] = f"Lỗi lưu QR: {e}"

        elif et == LoginQRCallbackEventType.QRCodeExpired:
            s.update(status="expired", msg="QR đã hết hạn.", qr=None); _rm(qr_path)

        elif et == LoginQRCallbackEventType.QRCodeScanned:
            s["status"] = "scanned"
            s["msg"]    = f"Đã quét bởi {data.get('display_name','Người dùng')}. Xác nhận trên điện thoại."
            _rm(qr_path)

        elif et == LoginQRCallbackEventType.QRCodeDeclined:
            s.update(status="declined", msg="Đăng nhập bị từ chối.", qr=None)

        elif et == LoginQRCallbackEventType.GotLoginInfo:
            s["status"] = "processing"
            s["msg"]    = "Đăng nhập thành công. Đang lấy dữ liệu..."

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                asyncio.wait_for(login_qr_with_unique_imei(callback=cb), timeout=LOGIN_TIMEOUT)
            )
        finally:
            loop.close()

        if result:
            info = result.get("user_info") or {}
            s["result"] = {
                "cookies":      result.get("cookies", {}),
                "imei":         result.get("imei", "N/A"),
                "display_name": info.get("displayName", "N/A"),
                "phone":        info.get("phoneNumber", "N/A"),
                "timestamp":    datetime.utcnow().isoformat(),
            }
            s["status"] = "success"; s["msg"] = "Lấy Cookie & IMEI thành công."
        else:
            s["status"] = "error"; s["msg"] = "Không lấy được thông tin."
    except asyncio.TimeoutError:
        s["status"] = "expired"; s["msg"] = "Hết thời gian chờ."
    except Exception as e:
        s["status"] = "error"; s["msg"] = f"Lỗi: {e}"
    finally:
        s["qr"] = None; _rm(qr_path)

# ==================== ROUTES =======================
@app.route("/")
def index():
    return Response(HTML_PAGE.replace("__ZALO_OK__", "1" if ZALO_OK else "0")
                             .replace("__ZALO_ERR__", ZALO_ERR or ""),
                    mimetype="text/html")

@app.route("/api/start", methods=["POST"])
def api_start():
    if not ZALO_OK:
        return jsonify({"error": f"zalo_login không khả dụng: {ZALO_ERR}"}), 503
    sid = str(uuid.uuid4())
    with sessions_lock:
        sessions[sid] = {
            "status": "starting", "msg": "Đang khởi tạo QR...",
            "qr": None, "result": None, "created_at": time.time(),
        }
    threading.Thread(target=worker, args=(sid,), daemon=True).start()
    return jsonify({"session_id": sid})

@app.route("/api/status/<sid>")
def api_status(sid):
    s = _get(sid)
    if not s: abort(404)
    return jsonify({
        "status": s["status"], "message": s["msg"],
        "has_qr": s["qr"] is not None, "result": s["result"],
    })

@app.route("/api/qr/<sid>")
def api_qr(sid):
    s = _get(sid)
    if not s or not s["qr"]: abort(404)
    return send_file(BytesIO(s["qr"]), mimetype="image/png", max_age=0)

@app.route("/health")
def health():
    return jsonify({"ok": True, "zalo": ZALO_OK})

# ==================== FRONTEND (inline) ============
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zalo QR Login</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#0f172a;color:#e2e8f0;
     min-height:100vh;padding:20px;display:flex;justify-content:center;align-items:flex-start}
.box{width:100%;max-width:480px;background:#1e293b;border-radius:16px;padding:28px;
     margin-top:40px;box-shadow:0 4px 24px rgba(0,0,0,.4)}
h1{font-size:22px;text-align:center;margin-bottom:20px}
.btn{border:none;padding:12px 20px;border-radius:8px;cursor:pointer;
     font-size:15px;font-weight:600;transition:.2s}
.btn.primary{background:#2563eb;color:#fff;width:100%}
.btn.primary:hover{background:#1d4ed8}
.btn:not(.primary){background:#334155;color:#e2e8f0;margin:4px 6px 0 0}
.btn:hover{opacity:.85}
.btn:disabled{opacity:.4;cursor:not-allowed}
.hidden{display:none}
.status{margin-top:16px;padding:12px;background:#0f172a;border-radius:8px;
        text-align:center;font-size:14px}
.qr{margin-top:20px;text-align:center}
.qr img{max-width:100%;width:320px;border-radius:12px}
.result{margin-top:20px}
.result h2{font-size:18px;margin-bottom:14px;color:#22c55e}
.f{margin-bottom:10px;font-size:14px}
.f strong{color:#94a3b8;margin-right:4px}
pre{background:#0f172a;padding:12px;border-radius:8px;font-size:12px;
    overflow-x:auto;margin-bottom:12px;white-space:pre-wrap;word-break:break-all}
.alert{padding:12px;background:#7f1d1d;border-radius:8px;margin-bottom:16px;font-size:14px}
</style>
</head>
<body>
<div class="box">
  <h1>🔑 Zalo QR Login</h1>
  <div id="warn" class="alert hidden"></div>
  <button id="start" class="btn primary">Tạo mã QR</button>
  <div id="status" class="status hidden"><span id="status-txt">...</span></div>
  <div id="qr-box" class="qr hidden"><img id="qr-img" alt="QR"></div>
  <div id="result" class="result hidden">
    <h2>Kết quả</h2>
    <div class="f"><strong>Tên:</strong> <span id="r-name"></span></div>
    <div class="f"><strong>SĐT:</strong> <span id="r-phone"></span></div>
    <div class="f"><strong>IMEI:</strong> <span id="r-imei"></span></div>
    <div class="f"><strong>Cookie:</strong></div>
    <pre id="r-cookie"></pre>
    <button id="copy" class="btn">Copy Cookie</button>
    <button id="dl" class="btn">Tải .txt</button>
    <button id="reset" class="btn">Làm mới</button>
  </div>
</div>
<script>
const ZALO_OK = __ZALO_OK__ === 1;
const ZALO_ERR = "__ZALO_ERR__";
const $ = id => document.getElementById(id);
let sid=null, timer=null, last=null;

if(!ZALO_OK){
  $('warn').classList.remove('hidden');
  $('warn').textContent = 'Module zalo_login không khả dụng: ' + ZALO_ERR;
  $('start').disabled = true;
}

function resetUI(){
  sid=null; if(timer)clearInterval(timer);
  $('qr-box').classList.add('hidden');
  $('result').classList.add('hidden');
  $('status').classList.add('hidden');
  $('qr-img').src=''; last=null;
}

async function start(){
  resetUI();
  $('start').disabled=true;
  $('status').classList.remove('hidden');
  $('status-txt').textContent='Đang khởi tạo QR...';
  try{
    const r = await fetch('/api/start',{method:'POST'});
    if(!r.ok){ const e=await r.json().catch(()=>({}));
      $('status-txt').textContent=e.error||('HTTP '+r.status);
      $('start').disabled=false; return; }
    const d = await r.json();
    sid = d.session_id; poll();
  }catch(e){ $('status-txt').textContent='Lỗi mạng: '+e.message;
             $('start').disabled=false; }
}

function poll(){
  if(timer)clearInterval(timer);
  let fail=0;
  timer=setInterval(async()=>{
    if(!sid)return;
    try{
      const r = await fetch('/api/status/'+sid);
      if(!r.ok){ if(++fail>5){clearInterval(timer);$('start').disabled=false;} return; }
      fail=0; render(await r.json());
    }catch(e){console.error(e);}
  },1000);
}

function render(s){
  $('status').classList.remove('hidden');
  $('status-txt').textContent = s.message || s.status;

  if(s.has_qr){
    $('qr-box').classList.remove('hidden');
    $('qr-img').src = '/api/qr/'+sid+'?t='+Date.now();
  } else $('qr-box').classList.add('hidden');

  if(s.status==='success' && s.result){
    clearInterval(timer); $('start').disabled=false;
    $('result').classList.remove('hidden');
    $('r-name').textContent  = s.result.display_name||'N/A';
    $('r-phone').textContent = s.result.phone||'N/A';
    $('r-imei').textContent  = s.result.imei||'N/A';
    $('r-cookie').textContent= JSON.stringify(s.result.cookies||{},null,2);
    last = s.result;
  } else if(['expired','declined','error'].includes(s.status)){
    clearInterval(timer); $('start').disabled=false;
  } else $('start').disabled=true;
}

$('start').addEventListener('click', start);
$('reset').addEventListener('click', ()=>{resetUI();$('start').disabled=false;});
$('copy').addEventListener('click', async()=>{
  if(!last)return;
  await navigator.clipboard.writeText(JSON.stringify(last.cookies||{},null,2));
  $('copy').textContent='✓ Đã copy';
  setTimeout(()=>$('copy').textContent='Copy Cookie',1500);
});
$('dl').addEventListener('click', ()=>{
  if(!last)return;
  const txt = `Imei:\n${last.imei}\n\nCookie:\n${JSON.stringify(last.cookies,null,2)}\n`;
  const b = new Blob([txt],{type:'text/plain'});
  const u = URL.createObjectURL(b);
  const a = document.createElement('a');
  a.href=u; a.download=(last.display_name||'zalo').replace(/[^\w.-]/g,'_')+'_zalo.txt';
  a.click(); URL.revokeObjectURL(u);
});
</script>
</body>
</html>"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
