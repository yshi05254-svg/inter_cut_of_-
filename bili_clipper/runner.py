"""逐条执行 config 中的任务。"""
from __future__ import annotations

import logging
import time
from typing import Dict, Optional

from .auth import AccountStore, LoginRequired, get_client, safe_name
from .client import BiliClient
from .config import Config, Task
from .downloader import download_clip
from .finder import ClipComment, collect_candidates, find_clip_comments
from .state import State
from .timeline import Clip, format_seconds
from .uploader import TITLE_MAX, Submission, upload_video

log = logging.getLogger(__name__)


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render_title(template: str, values: Dict[str, str]) -> str:
    """生成投稿标题；超长时优先截短原标题，保证时间轴完整。"""
    template = template.strip() or "{原标题} {时间轴}"
    original = values.get("原标题", "")
    for cut in range(len(original) + 1):
        vals = dict(values)
        vals["原标题"] = original[: len(original) - cut] + ("…" if cut else "")
        title = template.format_map(_SafeDict(vals)).strip()
        if len(title) <= TITLE_MAX:
            return title
    return title[:TITLE_MAX]


def _resolve_page(info: dict, comment: ClipComment) -> dict:
    pages = info.get("pages") or [{"page": 1, "cid": info.get("cid"),
                                   "duration": info.get("duration", 0), "part": ""}]
    wanted = comment.clip.page or comment.video.page or 1
    for p in pages:
        if int(p.get("page", 0)) == int(wanted):
            return p
    log.warning("视频 %s 没有 P%s，改用 P1", info.get("bvid"), wanted)
    return pages[0]


class Runner:
    def __init__(self, config: Config, interactive: Optional[bool] = None):
        self.config = config
        self.settings = config.settings
        self.interactive = interactive
        self.store = AccountStore(self.settings.data_dir)
        self.state = State(self.settings.data_dir / "state.json")
        self._clients: Dict[str, BiliClient] = {}

    def client(self, account: str, password: str) -> BiliClient:
        if account not in self._clients:
            self._clients[account] = get_client(
                self.store, account, password, interactive=self.interactive,
                request_interval=self.settings.request_interval)
        return self._clients[account]

    def run_once(self) -> None:
        self._clients.clear()   # 每轮重新校验登录状态
        tasks = [t for t in self.config.tasks if t.enabled]
        log.info("开始处理 %d 条任务", len(tasks))
        for task in tasks:
            try:
                self.run_task(task)
            except LoginRequired as exc:
                log.error("%s 跳过: %s", task.name, exc)
            except Exception:
                log.exception("%s 执行出错", task.name)

    def run_task(self, task: Task) -> None:
        s = self.settings
        log.info("==== %s ====", task.name)
        watcher = self.client(task.watch_account, task.watch_password)
        st = self.state.task(task.key)
        candidates = collect_candidates(watcher, task.videos, s.history_count)
        log.info("扫描 %d 个视频的评论（监听账号 %s, mid=%s）", len(candidates), watcher.uname, watcher.mid)
        comments = find_clip_comments(watcher, watcher.mid, candidates,
                                      since_ctime=int(st["last_ctime"]), reply_pages=s.reply_pages,
                                      processed=self.state.processed_ids(task.key))
        if not comments:
            log.info("%s 没有新的时间轴评论", task.name)
            return
        comment = comments[0]
        log.info("最新时间轴评论: %s《%s》%s", comment.video.bvid, comment.video.title,
                 comment.clip.label)
        try:
            self.process(task, watcher, comment)
        except LoginRequired:
            raise
        except Exception as exc:
            n = self.state.mark_failed(task.key, comment.rpid, comment.ctime, str(exc), s.max_attempts)
            log.error("处理评论 %s 失败（第 %d/%d 次）: %s", comment.rpid, n, s.max_attempts, exc)
            raise

    def process(self, task: Task, watcher: BiliClient, comment: ClipComment) -> None:
        s = self.settings
        info = watcher.video_info(bvid=comment.video.bvid) if comment.video.bvid \
            else watcher.video_info(aid=comment.video.aid)
        page = _resolve_page(info, comment)
        duration = int(page.get("duration") or 0)
        clip = comment.clip
        if duration and clip.start >= duration:
            raise ValueError(f"开始时间 {format_seconds(clip.start)} 超出视频长度 {format_seconds(duration)}")
        if duration and clip.end > duration:
            clip = Clip(clip.start, duration, clip.page)

        bvid, page_no = info["bvid"], int(page.get("page", 1))
        multi = len(info.get("pages") or []) > 1
        source = f"https://www.bilibili.com/video/{bvid}" + (f"?p={page_no}" if multi else "")
        values = {
            "原标题": info.get("title", ""),
            "时间轴": clip.label,
            "开始": format_seconds(clip.start),
            "结束": format_seconds(clip.end),
            "BV": bvid,
            "分P": str(page_no),
            "分P标题": page.get("part", ""),
            "UP主": (info.get("owner") or {}).get("name", ""),
        }
        title = render_title(task.title, values)
        desc = task.desc.format_map(_SafeDict(values)) if task.desc else \
            f"原视频：{source}\nUP主：{values['UP主']}\n时间轴：{clip.label}"

        uploader = self.client(task.upload_account, task.upload_password)
        work_dir = s.download_dir / safe_name(task.key)
        path = download_clip(bvid, page_no, clip, watcher.cookie_dict(), s, work_dir)

        result = upload_video(uploader, path, Submission(
            title=title, tags=task.tags, tid=task.tid or int(info.get("tid") or s.default_tid),
            desc=desc, source=source, original=task.original, cover_url=info.get("pic", ""),
        ), line=s.upload_line, threads=s.upload_threads)

        self.state.mark_done(task.key, comment.rpid, comment.ctime, {
            "source": bvid, "page": page_no, "clip": clip.label, "title": title,
            "aid": result.get("aid"), "bvid": result.get("bvid"), "time": int(time.time()),
        })
        if s.delete_after_upload:
            path.unlink(missing_ok=True)
