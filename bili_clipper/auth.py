"""账号登录与凭据缓存。

登录顺序：
1. 读取缓存的 cookie，仍有效则直接使用（纯后台）。
2. 使用配置中的账号密码走客户端密码登录（纯后台，无需验证码；触发风控时会失败）。
3. 以上都失败时需要扫码登录：交互终端里会自动弹出二维码，
   后台运行时会跳过该任务并提示运行 scripts/login 脚本。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional

from .client import BiliClient, new_session

log = logging.getLogger(__name__)

APP_KEY = "ae57252b0c09105d"
APP_SEC = "c75875c596a69eb55bd119e74b07cfe3"


class LoginRequired(RuntimeError):
    pass


def safe_name(account: str) -> str:
    return re.sub(r"[^\w.@-]", "_", account) or "account"


class AccountStore:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir) / "accounts"
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, account: str) -> Path:
        return self.dir / f"{safe_name(account)}.json"

    def load(self, account: str) -> Dict[str, Any]:
        p = self.path(account)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("读取账号缓存 %s 失败: %s", p, exc)
            return {}

    def save(self, account: str, cookies: Dict[str, str], **extra: Any) -> None:
        payload = {"account": account, "cookies": cookies, "saved_at": int(time.time()), **extra}
        p = self.path(account)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            p.chmod(0o600)
        except OSError:
            pass


# ---------------------------------------------------------------------- 密码登录
def _app_sign(params: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(sorted(params.items()))
    params["sign"] = hashlib.md5((urllib.parse.urlencode(params) + APP_SEC).encode()).hexdigest()
    return params


def _encrypt_password(password: str, key_hash: str, pem: str) -> str:
    from Crypto.Cipher import PKCS1_v1_5
    from Crypto.PublicKey import RSA

    cipher = PKCS1_v1_5.new(RSA.import_key(pem))
    return base64.b64encode(cipher.encrypt((key_hash + password).encode())).decode()


def password_login(account: str, password: str) -> Dict[str, Any]:
    """客户端密码登录，成功返回 {"cookies":..., "access_token":..., ...}。"""
    session = new_session()
    key = session.get("https://passport.bilibili.com/x/passport-login/web/key",
                      timeout=15).json()
    if key.get("code") != 0:
        raise LoginRequired(f"获取登录公钥失败: {key.get('message')}")
    payload = _app_sign({
        "actionKey": "appkey",
        "appkey": APP_KEY,
        "build": 6270200,
        "captcha": "",
        "challenge": "",
        "channel": "bili",
        "device": "phone",
        "mobi_app": "android",
        "password": _encrypt_password(password, key["data"]["hash"], key["data"]["key"]),
        "permission": "ALL",
        "platform": "android",
        "seccode": "",
        "subid": 1,
        "ts": int(time.time()),
        "username": account,
        "validate": "",
    })
    resp = session.post("https://passport.bilibili.com/x/passport-login/oauth2/login",
                        data=payload, timeout=15).json()
    data = resp.get("data") or {}
    cookie_info = data.get("cookie_info")
    if resp.get("code") != 0 or not cookie_info:
        hint = data.get("message") or resp.get("message") or ""
        if data.get("url"):
            hint += f"（需要安全验证: {data['url']}）"
        raise LoginRequired(f"密码登录失败 code={resp.get('code')} {hint}".strip())
    cookies = {c["name"]: c["value"] for c in cookie_info.get("cookies", [])}
    token = data.get("token_info") or {}
    return {
        "cookies": cookies,
        "access_token": token.get("access_token", ""),
        "refresh_token": token.get("refresh_token", ""),
        "login_type": "password",
    }


# ---------------------------------------------------------------------- 扫码登录
def _print_qrcode(url: str, png_path: Optional[Path]) -> None:
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
        if png_path:
            try:
                qrcode.make(url).save(str(png_path))
                print(f"二维码图片已保存到: {png_path}")
            except Exception:  # 没装 pillow/pypng 时忽略
                pass
    except ImportError:
        print("未安装 qrcode 库，无法在终端显示二维码。")
    print(f"也可以把这个链接转成二维码后扫描: {url}")


def qrcode_login(account: str, png_path: Optional[Path] = None, timeout: int = 180) -> Dict[str, Any]:
    session = new_session()
    gen = session.get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
                      timeout=15).json()
    if gen.get("code") != 0:
        raise LoginRequired(f"生成二维码失败: {gen.get('message')}")
    url, key = gen["data"]["url"], gen["data"]["qrcode_key"]
    print(f"\n请使用账号「{account}」对应的 B站 App 扫码登录：")
    _print_qrcode(url, png_path)

    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        time.sleep(2)
        poll = session.get("https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
                           params={"qrcode_key": key}, timeout=15).json()
        data = poll.get("data") or {}
        code = data.get("code")
        if code == 0:
            cookies = {c.name: c.value for c in session.cookies}
            if "SESSDATA" not in cookies and data.get("url"):
                query = urllib.parse.parse_qs(urllib.parse.urlparse(data["url"]).query)
                cookies.update({k: v[0] for k, v in query.items()})
            print("扫码登录成功。")
            return {"cookies": cookies, "refresh_token": data.get("refresh_token", ""),
                    "login_type": "qrcode"}
        if code == 86038:
            raise LoginRequired("二维码已过期")
        if code != last and code == 86090:
            print("已扫码，请在手机上确认登录…")
        last = code
    raise LoginRequired("扫码登录超时")


# ---------------------------------------------------------------------- 对外入口
def _verify(cookies: Dict[str, str], request_interval: float) -> Optional[BiliClient]:
    client = BiliClient(cookies, request_interval=request_interval)
    nav = client.nav()
    if nav.get("isLogin"):
        client.mid = nav.get("mid")
        client.uname = nav.get("uname", "")
        client.vip = (nav.get("vipStatus") == 1)
        client.ensure_buvid()
        return client
    return None


def get_client(store: AccountStore, account: str, password: str, *,
               interactive: Optional[bool] = None, request_interval: float = 0.6) -> BiliClient:
    """返回已登录的 BiliClient，无法登录时抛出 LoginRequired。"""
    if interactive is None:
        interactive = bool(sys.stdin and sys.stdin.isatty())

    cached = store.load(account)
    if cached.get("cookies"):
        client = _verify(cached["cookies"], request_interval)
        if client:
            log.info("账号 %s 使用缓存凭据登录成功（%s）", account, client.uname)
            return client
        log.info("账号 %s 的缓存凭据已失效", account)

    attempts = []
    if password:
        attempts.append(("密码", lambda: password_login(account, password)))
    if interactive:
        attempts.append(("扫码", lambda: qrcode_login(
            account, store.dir / f"{safe_name(account)}_qrcode.png")))

    errors = []
    for name, fn in attempts:
        try:
            result = fn()
        except Exception as exc:
            log.warning("账号 %s %s登录失败: %s", account, name, exc)
            errors.append(f"{name}: {exc}")
            continue
        client = _verify(result["cookies"], request_interval)
        if client:
            extra = {k: v for k, v in result.items() if k != "cookies"}
            store.save(account, client.cookie_dict(), mid=client.mid, uname=client.uname, **extra)
            log.info("账号 %s %s登录成功（%s）", account, name, client.uname)
            return client
        errors.append(f"{name}: 登录后校验失败")

    hint = "；".join(errors) or "没有可用的登录方式"
    if not interactive:
        hint += f"。请在终端运行 `python main.py login {account}`（或 scripts/login 脚本）扫码登录"
    raise LoginRequired(f"账号 {account} 无法登录: {hint}")
