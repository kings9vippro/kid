import aiohttp
import asyncio
import qrcode
from io import BytesIO

class LoginQRCallbackEventType:
    QRCodeGenerated = "QRCodeGenerated"
    QRCodeExpired   = "QRCodeExpired"
    QRCodeScanned   = "QRCodeScanned"
    QRCodeDeclined  = "QRCodeDeclined"
    GotLoginInfo    = "GotLoginInfo"

async def login_qr_with_unique_imei(callback=None):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://id.zalo.me",
        "Referer": "https://id.zalo.me/account/login",
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.get("https://id.zalo.me/account/login/qr") as resp:
                data = await resp.json()
                qr_data = data.get("data", {})
                qr_code = qr_data.get("qrCode") or qr_data.get("qr")
                qr_url = qr_data.get("qrUrl")
        except Exception as e:
            if callback:
                await callback({"type": "error", "data": {"message": f"Lỗi kết nối Zalo: {str(e)}"}})
            return None

        if not qr_code and not qr_url:
            if callback:
                await callback({"type": "error", "data": {"message": "Zalo từ chối cấp mã QR."}})
            return None

        qr_bytes = None
        if qr_url:
            try:
                async with session.get(qr_url) as resp:
                    qr_bytes = await resp.read()
            except:
                pass
        
        if not qr_bytes and qr_code:
            img = qrcode.make(qr_code)
            buf = BytesIO()
            img.save(buf, format="PNG")
            qr_bytes = buf.getvalue()

        if callback:
            await callback({
                "type": LoginQRCallbackEventType.QRCodeGenerated,
                "data": {"qrCode": qr_code},
                "actions": {
                    "saveToFile": lambda path: open(path, "wb").write(qr_bytes)
                }
            })

        for _ in range(60):
            await asyncio.sleep(2)
            try:
                async with session.get(f"https://id.zalo.me/account/login/qr/check?qrCode={qr_code}") as resp:
                    check_data = await resp.json()
            except:
                continue
            
            status = check_data.get("data", {}).get("status")
            if status == "scanned":
                if callback:
                    await callback({
                        "type": LoginQRCallbackEventType.QRCodeScanned,
                        "data": {"display_name": check_data.get("data", {}).get("displayName", "Người dùng")}
                    })
            elif status == "confirmed":
                cookies = {}
                for cookie in session.cookie_jar:
                    cookies[cookie.key] = cookie.value
                imei = check_data.get("data", {}).get("imei", "")
                user_info = check_data.get("data", {}).get("userInfo", {})
                if callback:
                    await callback({"type": LoginQRCallbackEventType.GotLoginInfo, "data": {}})
                return {
                    "cookies": cookies,
                    "imei": imei,
                    "user_info": user_info
                }
            elif status == "expired":
                if callback:
                    await callback({"type": LoginQRCallbackEventType.QRCodeExpired})
                return None
        
        return None
