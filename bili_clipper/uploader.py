"""网页端投稿：upos 分片上传 + 提交稿件。"""
from __future__ import annotations

import base64
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .client import BiliClient, BiliError

log = logging.getLogger(__name__)

TITLE_MAX = 80
TAG_MAX_LEN = 20
TAG_MAX_COUNT = 10


@dataclass
class Submission:
    title: str
    tags: List[str]
    tid: int
    desc: str = ""
    source: str = ""       # 转载来源
    original: bool = False  # True=自制, False=转载
    cover_url: str = ""    # 原视频封面（会重新上传到投稿账号）
    extra: dict = field(default_factory=dict)


def clean_tags(tags: List[str]) -> List[str]:
    out: List[str] = []
    for t in tags:
        t = t.strip().replace(",", "")[:TAG_MAX_LEN]
        if t and t not in out:
            out.append(t)
    return out[:TAG_MAX_COUNT] or ["切片"]


def _upload_file(client: BiliClient, path: Path, line: str, threads: int) -> str:
    size = path.stat().st_size
    params = {
        "name": path.name, "size": size, "r": "upos", "profile": "ugcupos/bup",
        "ssl": 0, "version": "2.14.0", "build": 2140000,
    }
    if line:
        params.update({"upcdn": line, "probe_version": "20221109"})
    pre = client.request_json("GET", "https://member.bilibili.com/preupload",
                              params=params, check=False)
    if pre.get("OK") != 1:
        raise BiliError(pre.get("code", -1), f"preupload 失败: {pre}")
    url = f"https:{pre['endpoint']}/{pre['upos_uri'].replace('upos://', '')}"
    headers = {"X-Upos-Auth": pre["auth"]}
    chunk_size = int(pre.get("chunk_size") or 10 * 1024 * 1024)
    chunks = math.ceil(size / chunk_size)

    upload_id = client.session.post(f"{url}?uploads&output=json", headers=headers,
                                    timeout=30).json()["upload_id"]
    log.info("上传 %s：%.1f MB，%d 个分片", path.name, size / 1024 / 1024, chunks)

    lock = threading.Lock()
    done = [0]
    start_time = time.time()

    def put_chunk(index: int) -> None:
        start = index * chunk_size
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(chunk_size)
        q = {"partNumber": index + 1, "uploadId": upload_id, "chunk": index, "chunks": chunks,
             "size": len(data), "start": start, "end": start + len(data), "total": size}
        for attempt in range(10):
            try:
                r = client.session.put(url, params=q, data=data, headers=headers, timeout=120)
                r.raise_for_status()
                break
            except Exception as exc:
                if attempt == 9:
                    raise
                log.warning("分片 %d 上传失败(%s)，重试 %d", index, exc, attempt + 1)
                time.sleep(min(30, 2 ** attempt))
        with lock:
            done[0] += 1
            speed = done[0] * chunk_size / max(time.time() - start_time, 1) / 1024 / 1024
            log.info("上传进度 %d/%d (%.1f MB/s)", done[0], chunks, speed)

    with ThreadPoolExecutor(max_workers=max(1, threads)) as pool:
        list(pool.map(put_chunk, range(chunks)))

    parts = [{"partNumber": i + 1, "eTag": "etag"} for i in range(chunks)]
    finish_params = {"output": "json", "name": path.name, "profile": "ugcupos/bup",
                     "uploadId": upload_id, "biz_id": pre["biz_id"]}
    for attempt in range(6):
        try:
            r = client.session.post(url, params=finish_params, json={"parts": parts},
                                    headers=headers, timeout=60).json()
            if r.get("OK") == 1:
                break
            raise RuntimeError(r)
        except Exception as exc:
            if attempt == 5:
                raise RuntimeError(f"合并分片失败: {exc}")
            log.warning("合并分片失败(%s)，15 秒后重试", exc)
            time.sleep(15)
    return os.path.splitext(os.path.basename(pre["upos_uri"]))[0]


def _upload_cover(client: BiliClient, cover_url: str) -> str:
    if not cover_url:
        return ""
    try:
        if cover_url.startswith("//"):
            cover_url = "https:" + cover_url
        img = client.session.get(cover_url, timeout=30)
        img.raise_for_status()
        mime = img.headers.get("Content-Type", "image/jpeg").split(";")[0]
        b64 = base64.b64encode(img.content).decode()
        data = client.post("https://member.bilibili.com/x/vu/web/cover/up",
                           data={"cover": f"data:{mime};base64,{b64}", "csrf": client.csrf})
        return data["data"]["url"]
    except Exception as exc:
        log.warning("封面上传失败，将使用B站自动截取的封面: %s", exc)
        return ""


def upload_video(client: BiliClient, path: Path, sub: Submission, *,
                 line: str = "bda2", threads: int = 3) -> dict:
    if not client.csrf:
        raise RuntimeError("投稿账号缺少 bili_jct，请重新登录")
    filename = _upload_file(client, path, line, threads)
    cover = _upload_cover(client, sub.cover_url)
    payload = {
        "copyright": 1 if sub.original else 2,
        "source": "" if sub.original else sub.source,
        "tid": sub.tid,
        "cover": cover,
        "title": sub.title[:TITLE_MAX],
        "desc_format_id": 0,
        "desc": sub.desc,
        "dynamic": "",
        "subtitle": {"open": 0, "lan": ""},
        "tag": ",".join(clean_tags(sub.tags)),
        "videos": [{"filename": filename, "title": sub.title[:TITLE_MAX], "desc": ""}],
        **sub.extra,
    }
    try:
        client.session.get("https://member.bilibili.com/x/geetest/pre/add", timeout=15)
    except Exception:
        pass
    resp = client.post("https://member.bilibili.com/x/vu/web/add",
                       params={"csrf": client.csrf}, json=payload)
    data = resp.get("data") or {}
    log.info("投稿成功: aid=%s bvid=%s 《%s》", data.get("aid"), data.get("bvid"), payload["title"])
    return data
