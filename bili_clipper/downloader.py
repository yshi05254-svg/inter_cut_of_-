"""用 yt-dlp 下载视频的指定时间段，清晰度取决于传入账号的 cookie（大会员可下 4K/1080P+）。"""
from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List

from .config import GlobalSettings
from .timeline import Clip

log = logging.getLogger(__name__)


def write_netscape_cookies(cookies: Dict[str, str], path: Path) -> Path:
    expires = int(time.time()) + 30 * 86400
    lines = ["# Netscape HTTP Cookie File"]
    for name, value in cookies.items():
        lines.append("\t".join([".bilibili.com", "TRUE", "/", "FALSE", str(expires), name, value]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def build_command(url: str, clip: Clip, out_base: Path, cookie_file: Path,
                  result_file: Path, settings: GlobalSettings) -> List[str]:
    cmd = [
        sys.executable, "-m", "yt_dlp", url,
        "--cookies", str(cookie_file),
        "--no-playlist",
        "-f", settings.download_format,
        "--download-sections", f"*{clip.start}-{clip.end}",
        "--merge-output-format", "mp4",
        "-o", f"{out_base}.%(ext)s",
        "--print-to-file", "after_move:filepath", str(result_file),
        "--newline",
        "--retries", "10",
        "--fragment-retries", "10",
    ]
    if settings.precise_cut:
        cmd.append("--force-keyframes-at-cuts")
    if settings.ffmpeg:
        cmd += ["--ffmpeg-location", settings.ffmpeg]
    cmd += list(settings.ytdlp_args)
    return cmd


def download_clip(bvid: str, page: int, clip: Clip, cookies: Dict[str, str],
                  settings: GlobalSettings, work_dir: Path) -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{bvid}_p{page}_{clip.start}-{clip.end}"
    out_base = work_dir / stem
    cookie_file = write_netscape_cookies(cookies, work_dir / f".{stem}.cookies.txt")
    result_file = work_dir / f".{stem}.path"
    result_file.unlink(missing_ok=True)
    url = f"https://www.bilibili.com/video/{bvid}?p={page}"
    cmd = build_command(url, clip, out_base, cookie_file, result_file, settings)
    log.info("开始下载 %s P%d %s", bvid, page, clip.label)
    try:
        proc = subprocess.run(cmd)
    finally:
        cookie_file.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp 下载失败，退出码 {proc.returncode}")

    path = None
    if result_file.exists():
        lines = [ln.strip() for ln in result_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
        result_file.unlink(missing_ok=True)
        if lines:
            path = Path(lines[-1])
    if not path or not path.exists():
        matches = sorted(work_dir.glob(f"{stem}.*"), key=lambda p: p.stat().st_mtime)
        matches = [m for m in matches if m.suffix.lower() in (".mp4", ".mkv", ".flv", ".webm")]
        if not matches:
            raise RuntimeError("yt-dlp 运行完成但找不到输出文件")
        path = matches[-1]
    if path.stat().st_size < 1024:
        # ffmpeg 读流失败时 yt-dlp 仍可能返回 0 并留下空文件
        path.unlink(missing_ok=True)
        raise RuntimeError("下载得到的文件为空，可能是视频流读取失败或 cookie 权限不足")
    log.info("下载完成: %s (%.1f MB)", path, path.stat().st_size / 1024 / 1024)
    return path
