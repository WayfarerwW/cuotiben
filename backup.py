#!/usr/bin/env python3
"""每日备份脚本（requirements.md 2.17 / 7）。

每天产出**两份并列**的备份：

    backups/cuotiben_{YYYY-MM-DD}.db     数据库（SQLite backup API 的一致性快照）
    backups/uploads_{YYYY-MM-DD}/        图片（uploads/ 的当日快照）

两者按**同一个日期**各自保留最近 N 天（默认 30 天），一起清理。

用法：

    python backup.py                 # 备份今天（库 + 图片），然后清理超期备份
    python backup.py --keep 7        # 只保留最近 7 天
    python backup.py --force         # 今天已备份过也重新做一份
    python backup.py --list          # 只列出已有备份，不备份
    python backup.py --dry-run       # 只显示将会做什么，不落盘
    python backup.py --no-images     # 只备份数据库，不备 uploads/

排期（每日本机执行一次）：

  - Linux/macOS：`crontab -e` 加一行
        0 3 * * *  cd /path/to/cuotiben && /usr/bin/python3 backup.py >> backups/backup.log 2>&1
  - Windows：计划任务里每天运行
        python.exe D:\\path\\to\\cuotiben\\backup.py
  - 或者让应用自己代管：在 .env 里设 `CUOTIBEN_AUTO_BACKUP=1`，
    应用每次启动时会补做当天这一次（同一天不会重复备）。

## 为什么要把 uploads/ 也备上

原来每日备份**只有 .db**。图片在 uploads/ 里，一旦误删（真实发生过：
一次自检隔离失败把 uploads/ 清空），数据库记录还在、文件没了，
`GET /uploads/...` 全变 404，而且**任何备份都救不回来** ——
"有备份"却在最需要的时候没有图片，这种备份是假的安全感。

## 为什么图片单独一份，而不是塞进 .db 里

`backups/cuotiben_{日期}.db` 是**单文件**，一直是"每日快照"的名字。
把 uploads 塞进同名目录会改变这个既有布局与其清理语义，没必要。
并列成 `uploads_{日期}/` 之后：

  - 数据库那份布局**完全不变**（老备份、老脚本、老自检都还认）
  - 还原很简单：`cuotiben_{日期}.db` 拷回 `data/`，
    `uploads_{日期}/` 里的内容拷回 `uploads/`

## 为什么用硬链接而不是复制

图片是**不可变**的（上传后写一次，文件名是 UUID，永不原地修改），
所以硬链接出的备份与源文件内容必然一致。好处：

  - **零额外磁盘**：30 天的图片备份不会把同一张图复制 30 份
  - **秒级完成**：不复制字节
  - **每天都自包含**：任何一天单独拿出来都能完整还原
    （"增量备份 + 链式依赖"做不到这点 —— 最老的那份一删，整条链都废了）

硬链接不支持的场景（跨卷、文件系统不支持）自动退回**复制**，
目录结构完全一样，只是多占磁盘；链接数与复制数会分别报出来。

文件名用**本地日期**（不是 UTC）：备份是给人看的东西，
半夜 3 点跑的时候用户期望看到的是"今天"。

清理按**日期**排序，不按 mtime —— mtime 会被复制、同步、
解压等操作改掉，按它删有可能误删刚备份的、留下很久以前的。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

#: 默认保留天数
DEFAULT_KEEP = 30

#: 每日数据库备份文件名。只认这个格式，绝不碰 backups/ 下的其它东西
#: （手动备份是 backups/<时间戳>/ 子目录，两者互不干扰）。
BACKUP_NAME_RE = re.compile(r"^cuotiben_(\d{4}-\d{2}-\d{2})\.db$")

#: 每日图片快照目录名，与数据库那份同一天。
UPLOADS_SNAPSHOT_RE = re.compile(r"^uploads_(\d{4}-\d{2}-\d{2})$")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def backups_dir() -> Path:
    """备份目录。

    与 data_service 一样支持 `CUOTIBEN_BACKUPS_DIR` 覆盖 ——
    自检必须能在临时目录里跑，否则每跑一次就往真实 backups/ 里堆文件。
    """
    from app.services.data_service import BACKUPS_DIR

    return BACKUPS_DIR


def uploads_dir() -> Path:
    """图片目录（与上传/导出/清理统一取自 image_service）。"""
    from app.services.image_service import UPLOADS_DIR

    return UPLOADS_DIR


def resolve_db_path() -> Path:
    """当前实际使用的数据库文件。

    用 data_service 的实现而不是常量 `data/cuotiben.db`：
    `CUOTIBEN_DATABASE_URL` 可以覆盖库位置，写死常量会备份一个空文件。
    """
    from app.services.data_service import resolve_db_path as _resolve

    return _resolve()


def list_backups(directory: Path) -> list[tuple[dt.date, Path]]:
    """列出每日**数据库**备份，按日期升序。"""
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


def list_uploads_snapshots(directory: Path) -> list[tuple[dt.date, Path]]:
    """列出每日**图片**快照目录，按日期升序。"""
    found: list[tuple[dt.date, Path]] = []
    if not directory.is_dir():
        return found
    for item in directory.iterdir():
        if not item.is_dir():
            continue
        match = UPLOADS_SNAPSHOT_RE.match(item.name)
        if not match:
            continue
        try:
            day = dt.date.fromisoformat(match.group(1))
        except ValueError:
            continue
        found.append((day, item))
    found.sort(key=lambda pair: pair[0])
    return found


def prune(keep: int, *, directory: Path | None = None, dry_run: bool = False) -> list[Path]:
    """删除最旧的每日备份（.db 与图片快照各自裁剪），只留最近 `keep` 天。

    返回被删除的路径（含 .db 与 uploads 目录）。
    """
    target_dir = directory or backups_dir()
    removed: list[Path] = []

    # 数据库那份
    db_entries = list_backups(target_dir)
    for _day, path in (db_entries[:-keep] if keep > 0 else db_entries):
        if dry_run:
            removed.append(path)
            continue
        try:
            path.unlink()
            removed.append(path)
        except OSError as exc:                       # noqa: PERF203
            print(f"[warn] 删除失败 {path.name}: {exc}", file=sys.stderr)

    # 图片快照：按同一个 keep **独立**裁剪。
    # 故意不与 .db 的日期求交集 —— 若某天只备成功了一半，那份仍应按自己的
    # 年龄被清掉，否则会永远留着。
    up_entries = list_uploads_snapshots(target_dir)
    for _day, path in (up_entries[:-keep] if keep > 0 else up_entries):
        if dry_run:
            removed.append(path)
            continue
        try:
            shutil.rmtree(path)
            removed.append(path)
        except OSError as exc:                       # noqa: PERF203
            print(f"[warn] 删除失败 {path.name}: {exc}", file=sys.stderr)

    return removed


def link_or_copy_uploads(source: Path, dest: Path) -> dict:
    """把 uploads/ 快照到 dest；优先硬链接，失败则复制。

    图片是**不可变**的（UUID 文件名、写一次就不动），所以硬链接安全：
    链接出的文件与源内容必然一致，而且**不占额外磁盘**。
    跨卷/不支持时退回复制，布局不变。

    返回 {"files", "linked", "copied", "bytes"}。
    """
    stats = {"files": 0, "linked": 0, "copied": 0, "bytes": 0}
    if not source.is_dir():
        return stats
    for item in source.rglob("*"):
        rel = item.relative_to(source)
        target = dest / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not item.is_file():
            continue          # 跳过 fifo/软链等特殊文件
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(item, target)
            stats["linked"] += 1
        except OSError:
            # 跨卷、文件系统不支持硬链接 —— 退回复制
            shutil.copy2(item, target)
            stats["copied"] += 1
        stats["files"] += 1
        try:
            stats["bytes"] += target.stat().st_size
        except OSError:
            pass
    return stats


def _refresh_uploads_snapshot(source: Path, dest: Path) -> dict:
    """生成/刷新当天的图片快照。

    先写临时目录再原子改名：中途失败不会留下一个"缺了一半图片"的快照
    被当成可用备份。`--force` 重做时先删旧快照再改名。
    """
    staging = dest.with_name(dest.name + ".tmp")
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    try:
        staging.mkdir(parents=True, exist_ok=False)
        stats = link_or_copy_uploads(source, staging)
        if dest.exists():
            shutil.rmtree(dest)
        staging.replace(dest)
        return stats
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def run_backup(
    *,
    keep: int = DEFAULT_KEEP,
    directory: Path | None = None,
    now: dt.datetime | None = None,
    force: bool = False,
    dry_run: bool = False,
    with_uploads: bool = True,
) -> dict:
    """执行一次每日备份（数据库 + uploads/）+ 清理。返回结果摘要。"""
    from app.services.data_service import sqlite_snapshot

    target_dir = directory or backups_dir()
    source = resolve_db_path()

    if not source.is_file():
        raise FileNotFoundError(f"数据库不存在：{source}")

    moment = now or dt.datetime.now()
    day = moment.date()
    dest = target_dir / f"cuotiben_{day.isoformat()}.db"
    uploads_dest = target_dir / f"uploads_{day.isoformat()}"
    images_source = uploads_dir()

    result = {
        "source": str(source),
        "dest": str(dest),
        "uploads_dest": str(uploads_dest),
        "created": False,
        "skipped": False,
        "pruned": [],
        "keep": keep,
        "dry_run": dry_run,
        "uploads_source": str(images_source),
        "uploads": {"files": 0, "linked": 0, "copied": 0, "bytes": 0},
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

        if with_uploads:
            result["uploads"] = _refresh_uploads_snapshot(images_source,
                                                          uploads_dest)

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
    snapshots = {day: path for day, path in list_uploads_snapshots(directory)}
    if not entries and not snapshots:
        print(f"（{directory} 下没有每日备份）")
        return 0
    total = 0
    print(f"每日备份（{directory}）：")
    for day, path in entries:
        size = path.stat().st_size
        total += size
        snap = snapshots.pop(day, None)
        if snap is None:
            print(f"  {day.isoformat()}  {size / 1024:9.1f} KB  {path.name}"
                  "   （无图片快照）")
        else:
            n_img = sum(1 for p in snap.rglob("*") if p.is_file())
            img_bytes = sum(p.stat().st_size for p in snap.rglob("*") if p.is_file())
            total += img_bytes
            print(f"  {day.isoformat()}  {size / 1024:9.1f} KB  {path.name}"
                  f"   + {n_img:4} 张图（{img_bytes / 1024:.1f} KB）  {snap.name}/")
    for day, snap in sorted(snapshots.items()):
        n_img = sum(1 for p in snap.rglob("*") if p.is_file())
        img_bytes = sum(p.stat().st_size for p in snap.rglob("*") if p.is_file())
        total += img_bytes
        print(f"  {day.isoformat()}  {'—':>9}      （只有图片快照）  {snap.name}/"
              f"   {n_img} 张图")
    print(f"共 {len(entries)} 份数据库备份，"
          f"{len(list_uploads_snapshots(directory))} 份图片快照，"
          f"内容合计 {total / 1024 / 1024:.1f} MB（硬链接不占额外磁盘）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把数据库与 uploads/ 每日备份到 backups/，并只保留最近 N 天",
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
    parser.add_argument("--no-images", action="store_true",
                        help="只备份数据库，不备 uploads/")
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
            with_uploads=not args.no_images,
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
            up = result["uploads"]
            print(f"{prefix}已备份：{result['dest']}")
            if args.no_images:
                print(f"{prefix}  图片    已跳过（--no-images）")
            else:
                print(f"{prefix}  图片    {up['files']} 张 -> "
                      f"{Path(result['uploads_dest']).name}/"
                      f"（硬链接 {up['linked']}，复制 {up['copied']}，"
                      f"内容 {up['bytes'] / 1024:.1f} KB）")
        if result["pruned"]:
            print(f"{prefix}已清理 {len(result['pruned'])} 项超出 {args.keep} 天的备份")
        print(f"{prefix}当前保留：{len(list_backups(directory))} 份"
              f"（上限 {args.keep}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
