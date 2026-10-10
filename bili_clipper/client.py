"""B站 HTTP 会话封装：通用请求头、wbi 签名、常用接口。"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import urllib.parse
from typing import Any, Dict, Iterator, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


class BiliError(RuntimeError):
    def __init__(self, code: int, message: str, url: str = ""):
        super().__init__(f"B站接口错误 code={code} message={message} ({url})")
        self.code = code


def mixin_key(img_key: str, sub_key: str) -> str:
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def sign_wbi(params: Dict[str, Any], key: str, wts: Optional[int] = None) -> Dict[str, Any]:
    params = dict(params)
    params["wts"] = int(wts if wts is not None else time.time())
    cleaned = {
        k: "".join(ch for ch in str(v) if ch not in "!'()*")
        for k, v in sorted(params.items())
    }
    query = urllib.parse.urlencode(cleaned)
    cleaned["w_rid"] = hashlib.md5((query + key).encode()).hexdigest()
    return cleaned


def new_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=(500, 502, 503, 504),
                  allowed_methods=None)
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "User-Agent": UA,
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
    })
    return session


class BiliClient:
    def __init__(self, cookies: Optional[Dict[str, str]] = None, request_interval: float = 0.6):
        self.session = new_session()
        if cookies:
            self.session.cookies.update(cookies)
        self.request_interval = request_interval
        self.mid: int = 0
        self.uname: str = ""
        self.vip: bool = False
        self._wbi_key: Optional[str] = None
        self._wbi_ts = 0.0
        self._last_request = 0.0

    # ------------------------------------------------------------------ 基础
    @property
    def csrf(self) -> str:
        return self.session.cookies.get("bili_jct", "")

    def cookie_dict(self) -> Dict[str, str]:
        return {c.name: c.value for c in self.session.cookies}

    def _throttle(self) -> None:
        wait = self.request_interval - (time.time() - self._last_request)
        if wait > 0:
            time.sleep(wait + random.uniform(0, self.request_interval / 2))
        self._last_request = time.time()

    def request_json(self, method: str, url: str, *, check: bool = True, **kwargs) -> Dict[str, Any]:
        self._throttle()
        kwargs.setdefault("timeout", 20)
        resp = self.session.request(method, url, **kwargs)
        if resp.status_code == 412:
            raise BiliError(412, "请求被风控拦截(412)，请稍后再试或调大请求间隔", url)
        resp.raise_for_status()
        data = resp.json()
        if check and data.get("code", 0) != 0:
            raise BiliError(data.get("code"), data.get("message", ""), url)
        return data

    def get(self, url: str, **kwargs) -> Dict[str, Any]:
        return self.request_json("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> Dict[str, Any]:
        return self.request_json("POST", url, **kwargs)

    def ensure_buvid(self) -> None:
        if self.session.cookies.get("buvid3"):
            return
        try:
            data = self.get("https://api.bilibili.com/x/frontend/finger/spi")["data"]
            self.session.cookies.set("buvid3", data["b_3"], domain=".bilibili.com")
            self.session.cookies.set("buvid4", data["b_4"], domain=".bilibili.com")
        except Exception as exc:  # 非关键步骤
            log.debug("获取 buvid 失败: %s", exc)

    # ------------------------------------------------------------------ 账号
    def nav(self) -> Dict[str, Any]:
        """返回 nav 数据。未登录时 code=-101 但仍带 wbi_img。"""
        data = self.get("https://api.bilibili.com/x/web-interface/nav", check=False)
        nav = data.get("data") or {}
        img = (nav.get("wbi_img") or {})
        if img.get("img_url") and img.get("sub_url"):
            stem = lambda u: u.rsplit("/", 1)[-1].split(".", 1)[0]  # noqa: E731
            self._wbi_key = mixin_key(stem(img["img_url"]), stem(img["sub_url"]))
            self._wbi_ts = time.time()
        return nav

    def is_login(self) -> bool:
        try:
            return bool(self.nav().get("isLogin"))
        except Exception as exc:
            log.warning("检查登录状态失败: %s", exc)
            return False

    def wbi(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self._wbi_key or time.time() - self._wbi_ts > 3600:
            self.nav()
        if not self._wbi_key:
            raise BiliError(-1, "无法获取 wbi 签名密钥")
        return sign_wbi(params, self._wbi_key)

    # ------------------------------------------------------------------ 视频
    def video_info(self, bvid: str = "", aid: int = 0) -> Dict[str, Any]:
        params = {"bvid": bvid} if bvid else {"aid": aid}
        return self.get("https://api.bilibili.com/x/web-interface/view", params=params)["data"]

    def history(self, count: int) -> List[Dict[str, Any]]:
        """最近观看的稿件（需登录且未关闭历史记录）。"""
        items: List[Dict[str, Any]] = []
        cursor: Dict[str, Any] = {"max": 0, "view_at": 0, "business": ""}
        while len(items) < count:
            params = {"ps": min(30, count - len(items)), "type": "archive", **cursor}
            data = self.get("https://api.bilibili.com/x/web-interface/history/cursor",
                            params=params)["data"]
            batch = [i for i in data.get("list") or []
                     if (i.get("history") or {}).get("business") == "archive"]
            if not batch:
                break
            items.extend(batch)
            nxt = data.get("cursor") or {}
            if not nxt.get("max"):
                break
            cursor = {"max": nxt["max"], "view_at": nxt.get("view_at", 0),
                      "business": nxt.get("business", "")}
        return items[:count]

    def iter_replies(self, aid: int, max_pages: int) -> Iterator[Dict[str, Any]]:
        """按时间倒序遍历视频下的一级评论。"""
        try:
            yield from self._iter_replies_wbi(aid, max_pages)
        except BiliError as exc:
            log.debug("wbi 评论接口失败(%s)，改用旧接口", exc)
            yield from self._iter_replies_legacy(aid, max_pages)

    def _iter_replies_wbi(self, aid: int, max_pages: int) -> Iterator[Dict[str, Any]]:
        offset = ""
        for _ in range(max_pages):
            params = self.wbi({
                "oid": aid, "type": 1, "mode": 2, "plat": 1, "web_location": 1315875,
                "pagination_str": json.dumps({"offset": offset}, separators=(",", ":")),
            })
            data = self.get("https://api.bilibili.com/x/v2/reply/wbi/main", params=params)["data"]
            for reply in data.get("replies") or []:
                yield reply
            cursor = data.get("cursor") or {}
            offset = ((cursor.get("pagination_reply") or {}).get("next_offset")) or ""
            if cursor.get("is_end") or not offset:
                return

    def _iter_replies_legacy(self, aid: int, max_pages: int) -> Iterator[Dict[str, Any]]:
        for pn in range(1, max_pages + 1):
            data = self.get("https://api.bilibili.com/x/v2/reply",
                            params={"type": 1, "oid": aid, "sort": 0, "pn": pn, "ps": 20})["data"]
            replies = data.get("replies") or []
            yield from replies
            if len(replies) < 20:
                return
