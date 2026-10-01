#!/usr/bin/env python3
"""每日备份脚本（requirements.md 2.17 / 7）。

把 `data/cuotiben.db` 复制成 `backups/cuotiben_{YYYY-MM-DD}.db`，
并只保留最近 N 天（默认 30 天）。

用法：

    python backup.py                 # 备份今天，然后清理超过 30 天的
    python backup.py --keep 7        # 只保留最近 7 天
    python backup.py --force         # 今天已备份过也重新做一份
    python backup.py --list          # 只列出已有备份，不备份
    python backup.py --dry-run       # 只显示将会做什么，不落盘

排期（每日本机执行一次）：

  - Linux/macOS：`crontab -e` 加一行
        0 3 * * *  cd /path/to/cuotiben && /usr/bin/python3 backup.py >> backups/backup.log 2>&1
  - Windows：计划任务里每天运行
        python.exe D:\\path\\to\\cuotiben\\backup.py
  - 或者让应用自己代管：在 .env 里设 `CUOTIBEN_AUTO_BACKUP=1`，
    应用每次启动时会补做当天这一次（同一天不会重复备）。

文件名用**本地日期**（不是 UTC）：备份是给人看的东西，
半夜 3 点跑的时候用户期望看到的是"今天"。

清理按**文件名里的日期**排序，不按 mtime —— mtime 会被复制、同步、
解压等操作改掉，按它删有可能误删刚备份的、留下很久以前的。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

#: 默认保留天数
DEFAULT_KEEP = 30

#: 每日备份文件名。只认这个格式，绝不碰 backups/ 下的其它东西
#: （手动备份是 backups/<时间戳>/ 子目录，两者互不干扰）。
BACKUP_NAME_RE = re.compile(r"^cuotiben_(\d{4}-\d{2}-\d{2})\.db$")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def backups_dir() -> Path:
    """备份目录。

    与 data_service 一样支持 `CUOTIBEN_BACKUPS_DIR` 覆盖 ——
    自检必须能在临时目录里跑，否则每跑一次就往真实 backups/ 里堆文件。
    """
    from app.services.data_service import BACKUPS_DIR

    return BACKUPS_DIR


def resolve_db_path() -> Path:
    """当前实际使用的数据库文件。

    用 data_service 的实现而不是常量 `data/cuotiben.db`：
    `CUOTIBEN_DATABASE_URL` 可以覆盖库位置，写死常量会备份一个空文件。
    """
    from app.services.data_service import resolve_db_path as _resolve

    return _resolve()


def list_backups(directory: Path) -> list[tuple[dt.date, Path]]:
    """列出每日备份，按日期升序。"""
    found: list[tuple[dt.date, Path]] = []
    if not directory.is_dir():
        return found
    for item in directory.iterdir():
        if not item.is_file():
            continue
        match = BACKUP_NAME_RE.match(item.name)
        if not match:
            continue
        try:
            day = dt.date.fromisoformat(match.group(1))
        except ValueError:
            continue          # 形如 cuotiben_2026-13-45.db，忽略
        found.append((day, item))
    found.sort(key=lambda pair: pair[0])
    return found


def prune(keep: int, *, directory: Path | None = None, dry_run: bool = False) -> list[Path]:
    """删除最旧的每日备份，只留最近 `keep` 份。返回被删除的文件。"""
    target_dir = directory or backups_dir()
    entries = list_backups(target_dir)
    excess = entries[:-keep] if keep > 0 else entries
    removed: list[Path] = []
    for _day, path in excess:
        if dry_run:
            removed.append(path)
            continue
        try:
            path.unlink()
            removed.append(path)
        except OSError as exc:                       # noqa: PERF203
            print(f"[warn] 删除失败 {path.name}: {exc}", file=sys.stderr)
    return removed


def run_backup(
    *,
    keep: int = DEFAULT_KEEP,
    directory: Path | None = None,
    now: dt.datetime | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """执行一次每日备份 + 清理。返回结果摘要。"""
    from app.services.data_service import sqlite_snapshot

    target_dir = directory or backups_dir()
    source = resolve_db_path()

    if not source.is_file():
        raise FileNotFoundError(f"数据库不存在：{source}")

    moment = now or dt.datetime.now()
    day = moment.date()
    dest = target_dir / f"cuotiben_{day.isoformat()}.db"

    result = {
        "source": str(source),
        "dest": str(dest),
        "created": False,
        "skipped": False,
        "pruned": [],
        "keep": keep,
        "dry_run": dry_run,
    }

    if dry_run:
        result["skipped"] = dest.exists() and not force
        result["pruned"] = [str(p) for p in prune(
            keep, directory=target_dir, dry_run=True)]
        return result

    target_dir.mkdir(parents=True, exist_ok=True)

    # 同一天重复运行不重做（cron + 应用启动兜底可能都触发；
    # 重做会覆盖掉当天更早的那份快照，没有好处）。
    if dest.exists() and not force:
        result["skipped"] = True
    else:
        # 先写临时文件再原子替换：中途失败不会留下半个 .db
        # 被误当成可用备份。
        tmp = dest.with_suffix(".db.tmp")
        try:
            sqlite_snapshot(source, tmp)
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
        result["created"] = True

    result["pruned"] = [str(p) for p in prune(keep, directory=target_dir)]
    return result


def maybe_run_daily_backup(*, keep: int = DEFAULT_KEEP) -> dict | None:
    """给应用启动时调用的**兜底**入口：今天还没备就补一次。

    为什么不只靠 cron / 计划任务：个人本地应用里没人愿意去配 cron，
    结果就是"备份脚本写了但从来没跑过"。启动时补一次成本极低
    （同一天只做一次），能让每日备份在没有排期的情况下也真正成立。
    排期仍然推荐（应用不是每天都开）。

    默认**关闭**，由 .env 的 `CUOTIBEN_AUTO_BACKUP=1` 开启。
    任何异常都吞掉并返回 None —— 备份失败不该拦住应用启动。
    """
    if os.environ.get("CUOTIBEN_AUTO_BACKUP", "").strip().lower() not in (
        "1", "true", "yes", "y", "on",
    ):
        return None

    try:
        directory = backups_dir()
        day = dt.datetime.now().date()
        if (directory / f"cuotiben_{day.isoformat()}.db").exists():
            return None                     # 今天已经备过
        return run_backup(keep=keep)
    except Exception as exc:  # noqa: BLE001 - 绝不因备份失败影响启动
        print(f"[backup] 启动兜底备份失败：{type(exc).__name__}: {exc}",
              file=sys.stderr)
        return None


def cmd_list(directory: Path) -> int:
    entries = list_backups(directory)
    if not entries:
        print(f"（{directory} 下没有每日备份）")
        return 0
    total = 0
    print(f"每日备份（{directory}）：")
    for day, path in entries:
        size = path.stat().st_size
        total += size
        print(f"  {day.isoformat()}  {size / 1024:9.1f} KB  {path.name}")
    print(f"共 {len(entries)} 份，合计 {total / 1024 / 1024:.1f} MB")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把 data/cuotiben.db 每日备份到 backups/，并只保留最近 N 天",
    )
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                        help=f"保留最近多少份（默认 {DEFAULT_KEEP}）")
    parser.add_argument("--dir", type=Path, default=None,
                        help="备份目录（默认项目的 backups/）")
    parser.add_argument("--force", action="store_true",
                        help="今天已备份过也重新做一份")
    parser.add_argument("--list", action="store_true",
                        help="只列出已有备份")
    parser.add_argument("--dry-run", action="store_true",
                        help="只显示会做什么，不落盘")
    parser.add_argument("--quiet", action="store_true",
                        help="成功时不输出")
    args = parser.parse_args(argv)

    if args.keep < 1:
        print("--keep 必须 >= 1", file=sys.stderr)
        return 2

    directory = args.dir or backups_dir()

    if args.list:
        return cmd_list(directory)

    try:
        result = run_backup(
            keep=args.keep, directory=directory,
            force=args.force, dry_run=args.dry_run,
        )
    except FileNotFoundError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - 脚本要给出可读的失败原因
        print(f"[error] 备份失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if not args.quiet:
        prefix = "[dry-run] " if result["dry_run"] else ""
        if result["skipped"]:
            print(f"{prefix}今天已备份过，跳过：{Path(result['dest']).name}")
        elif result["created"]:
            print(f"{prefix}已备份：{result['dest']}")
        if result["pruned"]:
            print(f"{prefix}已清理 {len(result['pruned'])} 份超出 {args.keep} 天的备份")
        print(f"{prefix}当前保留：{len(list_backups(directory))} 份（上限 {args.keep}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
