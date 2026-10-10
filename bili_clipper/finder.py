"""找到监听账号最新一条带时间轴的评论。

B站没有公开的「某用户的全部评论」接口，因此这里的做法是：
在监听账号最近观看过的视频（历史记录）以及配置里「指定视频」中，
按时间倒序翻评论，找出该账号发的、含时间轴的最新一条。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from .client import BiliClient
from .timeline import Clip, parse_clip

log = logging.getLogger(__name__)

_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_AV_RE = re.compile(r"(?:^|[^0-9A-Za-z])av(\d+)", re.I)


@dataclass
class Candidate:
    aid: int
    bvid: str
    title: str
    page: Optional[int] = None   # 历史记录里看的是哪一P


@dataclass
class ClipComment:
    rpid: int
    ctime: int
    message: str
    clip: Clip
    video: Candidate


def _candidate_from_ref(client: BiliClient, ref: str) -> Optional[Candidate]:
    ref = ref.strip()
    bv = _BV_RE.search(ref)
    av = _AV_RE.search(ref)
    try:
        if bv:
            info = client.video_info(bvid=bv.group(0))
        elif av or ref.isdigit():
            info = client.video_info(aid=int(av.group(1) if av else ref))
        else:
            log.warning("无法识别的视频: %s", ref)
            return None
    except Exception as exc:
        log.warning("获取视频 %s 信息失败: %s", ref, exc)
        return None
    return Candidate(aid=info["aid"], bvid=info["bvid"], title=info["title"])


def collect_candidates(client: BiliClient, videos: List[str], history_count: int) -> List[Candidate]:
    seen: Dict[int, Candidate] = {}
    for ref in videos:
        c = _candidate_from_ref(client, ref)
        if c and c.aid not in seen:
            seen[c.aid] = c
    if history_count > 0:
        try:
            for item in client.history(history_count):
                h = item.get("history") or {}
                aid = h.get("oid")
                if not aid or aid in seen:
                    continue
                seen[aid] = Candidate(aid=aid, bvid=h.get("bvid", ""), title=item.get("title", ""),
                                      page=h.get("page") or None)
        except Exception as exc:
            log.warning("获取观看历史失败（请确认历史记录未关闭）: %s", exc)
    return list(seen.values())


def find_clip_comments(client: BiliClient, mid: int, candidates: List[Candidate], *,
                       since_ctime: int, reply_pages: int,
                       processed: set) -> List[ClipComment]:
    """返回所有比 since_ctime 新、未处理过的时间轴评论，按时间从新到旧排序。"""
    found: Dict[int, ClipComment] = {}
    for cand in candidates:
        try:
            for reply in client.iter_replies(cand.aid, reply_pages):
                ctime = int(reply.get("ctime") or 0)
                if ctime and ctime <= since_ctime:
                    break   # 时间倒序，后面更旧
                if int(reply.get("mid") or 0) != int(mid):
                    continue
                rpid = int(reply.get("rpid") or 0)
                if rpid in processed or rpid in found:
                    continue
                message = (reply.get("content") or {}).get("message", "")
                clip = parse_clip(message)
                if clip:
                    found[rpid] = ClipComment(rpid=rpid, ctime=ctime, message=message,
                                              clip=clip, video=cand)
                    log.info("在 %s《%s》找到评论: %s", cand.bvid, cand.title, message.strip())
        except Exception as exc:
            log.warning("读取 %s 的评论失败: %s", cand.bvid or cand.aid, exc)
    return sorted(found.values(), key=lambda c: c.ctime, reverse=True)
