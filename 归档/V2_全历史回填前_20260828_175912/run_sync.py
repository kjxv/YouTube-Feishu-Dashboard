"""Run one sync mode while streaming output to both console and a UTF-8 log."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime

from app_config import LOGS_DIR, PROJECT_DIR


def write_line(log_file, text: str, quiet: bool) -> None:
    log_file.write(text)
    log_file.flush()
    if not quiet:
        print(text, end="", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="运行同步并同时记录日志")
    parser.add_argument("mode", choices=("current", "daily"))
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="仅写日志，不在窗口显示（供 Windows 定时任务使用）",
    )
    args = parser.parse_args()

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{args.mode}.log"
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"

    with log_path.open("a", encoding="utf-8", newline="") as log_file:
        start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        write_line(log_file, f"\n[{start}] START {args.mode}\n", args.quiet)

        try:
            process = subprocess.Popen(
                [sys.executable, str(PROJECT_DIR / "youtube_lark.py"), args.mode],
                cwd=str(PROJECT_DIR),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            write_line(log_file, f"无法启动同步：{exc}\n", args.quiet)
            return 1

        assert process.stdout is not None
        for line in process.stdout:
            write_line(log_file, line, args.quiet)

        exit_code = process.wait()
        end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        write_line(
            log_file,
            f"[{end}] END {args.mode} exit={exit_code}\n",
            args.quiet,
        )

    if not args.quiet:
        if exit_code == 0:
            print(f"\n同步成功。日志：{log_path}")
        else:
            print(f"\n同步失败，退出码：{exit_code}。请检查：{log_path}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

