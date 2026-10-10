"""读取 config.json。键名支持中文（与示例一致）和英文别名。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


def _pick(data: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def _as_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [v.strip() for v in re.split(r"[,，;；\s]+", value) if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


@dataclass
class GlobalSettings:
    data_dir: Path = Path("data")
    download_dir: Path = Path("downloads")
    poll_interval: int = 600
    history_count: int = 20
    reply_pages: int = 3
    precise_cut: bool = False
    delete_after_upload: bool = False
    default_tid: int = 21
    upload_line: str = "bda2"
    upload_threads: int = 3
    ffmpeg: str = ""
    download_format: str = "bv*+ba/b"
    ytdlp_args: List[str] = field(default_factory=list)
    max_attempts: int = 3
    request_interval: float = 0.6


@dataclass
class Task:
    index: int
    upload_account: str
    upload_password: str
    watch_account: str
    watch_password: str
    title: str = ""
    tags: List[str] = field(default_factory=list)
    desc: str = ""
    tid: Optional[int] = None
    original: bool = False
    videos: List[str] = field(default_factory=list)
    task_id: str = ""
    enabled: bool = True

    @property
    def key(self) -> str:
        return self.task_id or f"{self.watch_account}->{self.upload_account}"

    @property
    def name(self) -> str:
        return f"任务#{self.index + 1}({self.key})"


@dataclass
class Config:
    settings: GlobalSettings
    tasks: List[Task]
    path: Path


def _parse_settings(raw: Dict[str, Any], base: Path) -> GlobalSettings:
    s = GlobalSettings()
    s.data_dir = base / _pick(raw, "数据目录", "data_dir", default="data")
    s.download_dir = base / _pick(raw, "下载目录", "download_dir", default="downloads")
    s.poll_interval = int(_pick(raw, "轮询间隔秒", "poll_interval", default=s.poll_interval))
    s.history_count = int(_pick(raw, "扫描历史视频数", "history_count", default=s.history_count))
    s.reply_pages = int(_pick(raw, "每视频扫描评论页数", "reply_pages", default=s.reply_pages))
    s.precise_cut = bool(_pick(raw, "精确剪切", "precise_cut", default=s.precise_cut))
    s.delete_after_upload = bool(_pick(raw, "上传后删除本地文件", "delete_after_upload",
                                       default=s.delete_after_upload))
    s.default_tid = int(_pick(raw, "默认分区tid", "default_tid", default=s.default_tid))
    s.upload_line = str(raw.get("上传线路", raw.get("upload_line", s.upload_line)) or "")  # 空=自动分配
    s.upload_threads = int(_pick(raw, "上传线程数", "upload_threads", default=s.upload_threads))
    s.ffmpeg = str(_pick(raw, "ffmpeg路径", "ffmpeg", default=""))
    s.download_format = str(_pick(raw, "下载格式", "download_format", default=s.download_format))
    s.ytdlp_args = list(_pick(raw, "yt-dlp额外参数", "ytdlp_args", default=[]))
    s.max_attempts = int(_pick(raw, "最大重试次数", "max_attempts", default=s.max_attempts))
    s.request_interval = float(_pick(raw, "请求间隔秒", "request_interval",
                                     default=s.request_interval))
    return s


def _parse_task(index: int, raw: Dict[str, Any]) -> Task:
    upload_account = str(_pick(raw, "投稿账号", "upload_account", default="")).strip()
    if not upload_account:
        raise ValueError(f"第 {index + 1} 条任务缺少「投稿账号」")
    upload_password = str(_pick(raw, "投稿账号密码", "upload_password", default=""))
    watch_account = str(_pick(raw, "监听账号", "watch_account", default=upload_account)).strip()
    if watch_account == upload_account:
        watch_password = str(_pick(raw, "监听账号密码", "watch_password", default=upload_password))
    else:
        watch_password = str(_pick(raw, "监听账号密码", "watch_password", default=""))
    tid = _pick(raw, "分区tid", "tid")
    return Task(
        index=index,
        upload_account=upload_account,
        upload_password=upload_password,
        watch_account=watch_account,
        watch_password=watch_password,
        title=str(_pick(raw, "投稿视频名", "title", default="")),
        tags=_as_list(_pick(raw, "tag", "标签", "tags")),
        desc=str(_pick(raw, "简介", "desc", default="")),
        tid=int(tid) if tid is not None else None,
        original=bool(_pick(raw, "自制", "original", default=False)),
        videos=_as_list(_pick(raw, "指定视频", "videos")),
        task_id=str(_pick(raw, "任务ID", "task_id", default="")),
        enabled=bool(raw.get("启用", raw.get("enabled", True))),
    )


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    with open(path, encoding="utf-8-sig") as f:
        raw = json.load(f)
    if isinstance(raw, list):
        raw = {"任务列表": raw}
    settings = _parse_settings(_pick(raw, "全局设置", "settings", default={}) or {}, path.parent)
    raw_tasks = _pick(raw, "任务列表", "tasks", default=[]) or []
    tasks = [_parse_task(i, t) for i, t in enumerate(raw_tasks)]
    return Config(settings=settings, tasks=tasks, path=path)


def find_password(config: Config, account: str) -> str:
    for task in config.tasks:
        if task.upload_account == account and task.upload_password:
            return task.upload_password
        if task.watch_account == account and task.watch_password:
            return task.watch_password
    return ""
