#!/usr/bin/env python3
"""B站评论时间轴自动切片投稿。

用法:
  python main.py run                 处理一遍 config 里的全部任务
  python main.py run --loop          常驻后台，按「轮询间隔秒」循环处理
  python main.py login [账号 ...]    登录账号（密码登录失败时扫码），不写账号则登录 config 里所有账号
  python main.py check               检查 config 里所有账号的登录状态
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path

from bili_clipper.auth import AccountStore, LoginRequired, get_client, qrcode_login, safe_name
from bili_clipper.client import BiliClient
from bili_clipper.config import find_password, load_config
from bili_clipper.runner import Runner

log = logging.getLogger("bili_clipper")


def setup_logging(data_dir: Path, verbose: bool) -> None:
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    file = logging.handlers.RotatingFileHandler(
        data_dir / "logs" / "clipper.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    root.handlers[:] = [file]
    if sys.stderr:  # pythonw 无窗口运行时没有 stderr
        root.handlers.append(logging.StreamHandler())
    for h in root.handlers:
        h.setFormatter(fmt)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


class RunLock:
    """防止 cron/计划任务重叠执行。"""

    def __init__(self, path: Path):
        self.path = path
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            raise SystemExit("已有一个实例在运行，本次退出。")
        return self

    def __exit__(self, *exc):
        self.fh.close()


def cmd_run(args, config) -> int:
    with RunLock(config.settings.data_dir / "run.lock"):
        interactive = False if args.daemon else None
        while True:
            runner = Runner(load_config(config.path), interactive=interactive)
            runner.run_once()
            if not args.loop:
                return 0
            interval = max(60, runner.settings.poll_interval)
            log.info("本轮结束，%d 秒后再次检查", interval)
            time.sleep(interval)


def _accounts(config, names):
    if names:
        return names
    seen = []
    for t in config.tasks:
        for a in (t.watch_account, t.upload_account):
            if a not in seen:
                seen.append(a)
    return seen


def cmd_login(args, config) -> int:
    store = AccountStore(config.settings.data_dir)
    ok = True
    for account in _accounts(config, args.accounts):
        try:
            if args.qrcode:
                result = qrcode_login(account, store.dir / f"{safe_name(account)}_qrcode.png")
                client = BiliClient(result["cookies"])
                nav = client.nav()
                if not nav.get("isLogin"):
                    raise LoginRequired("扫码后校验失败")
                store.save(account, client.cookie_dict(), mid=nav.get("mid"), uname=nav.get("uname"),
                           **{k: v for k, v in result.items() if k != "cookies"})
                print(f"[成功] {account} -> {nav.get('uname')}")
            else:
                client = get_client(store, account, find_password(config, account), interactive=True)
                print(f"[成功] {account} -> {client.uname}（大会员: {'是' if client.vip else '否'}）")
        except LoginRequired as exc:
            ok = False
            print(f"[失败] {exc}")
    return 0 if ok else 1


def cmd_check(args, config) -> int:
    store = AccountStore(config.settings.data_dir)
    ok = True
    for account in _accounts(config, args.accounts):
        try:
            client = get_client(store, account, find_password(config, account), interactive=False)
            print(f"[正常] {account} -> {client.uname} mid={client.mid} 大会员={'是' if client.vip else '否'}")
        except LoginRequired as exc:
            ok = False
            print(f"[需登录] {exc}")
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="B站评论时间轴自动切片投稿")
    parser.add_argument("-c", "--config", default="config.json", help="配置文件路径（默认 config.json）")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="处理所有任务")
    p_run.add_argument("--loop", action="store_true", help="常驻循环运行")
    p_run.add_argument("--daemon", action="store_true", help="纯后台模式：从不弹出扫码登录")

    p_login = sub.add_parser("login", help="登录账号并缓存凭据")
    p_login.add_argument("accounts", nargs="*")
    p_login.add_argument("--qrcode", action="store_true", help="直接扫码登录，不尝试密码")

    p_check = sub.add_parser("check", help="检查账号登录状态")
    p_check.add_argument("accounts", nargs="*")

    args = parser.parse_args(argv)
    if not args.command:
        args = parser.parse_args(["-c", args.config] + (["-v"] if args.verbose else []) + ["run"])

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"找不到配置文件 {config_path}，请先复制 config.example.json 为 config.json 并填写。")
        return 2
    config = load_config(config_path)
    setup_logging(config.settings.data_dir, args.verbose)
    return {"run": cmd_run, "login": cmd_login, "check": cmd_check}[args.command](args, config)


if __name__ == "__main__":
    sys.exit(main())
