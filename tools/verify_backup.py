"""每日备份脚本的验证（requirements.md 2.17 / 7）。

覆盖：
- 文件名格式严格是 `cuotiben_{YYYY-MM-DD}.db`，用**本地**日期
- 备份出的文件是**可用的一致快照**（能打开、表与行数正确，
  即使源库开着 WAL / 有未提交事务）
- **图片也被备份**：`uploads_{日期}/` 快照与源逐字节一致，
  且能真的从快照还原回上传目录
- **快照用硬链接**：内容一致但**不占额外磁盘**（同 inode）
- 同一天重复运行不重做（除非 --force）
- 只保留最近 N 份，且**按文件名日期**清理，不按 mtime
- 清理不碰 backups/ 下的其它内容（手动备份子目录、无关文件）
- --list / --dry-run / --keep / --no-images 的 CLI 行为
- 库不存在时以非零退出并给出可读原因
- 不污染真实 backups/ 与真实 uploads/
  （全程用 CUOTIBEN_BACKUPS_DIR / CUOTIBEN_UPLOADS_DIR 指向临时目录）

不用 pytest，照本项目惯例直接跑、打印 [PASS]/[FAIL]、用退出码表示结果。
"""

from __future__ import annotations

import datetime as dt
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OK = FAIL = 0
FAILED: list[str] = []


def expect(label: str, cond: bool, detail: object = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
    else:
        FAIL += 1
        FAILED.append(label)
    print(("[PASS] " if cond else "[FAIL] ") + label
          + (f"  ({detail})" if detail != "" else ""))


def section(title: str) -> None:
    print(f"\n-- {title} --")


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PROJECT / "backup.py"), *args],
        cwd=str(PROJECT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def main() -> int:  # noqa: C901 - 线性用例清单
    work = Path(tempfile.mkdtemp(prefix="verify_backup_"))
    backups = work / "backups"
    db_file = work / "source.db"
    uploads = work / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)

    # 造两张"图片"（内容随意，只要可逐字节比对）
    (uploads / "2026").mkdir(exist_ok=True)
    (uploads / "2026" / "a.jpg").write_bytes(b"JPEG-A" * 100)
    (uploads / "b.jpg").write_bytes(b"JPEG-B" * 50)
    (uploads / ".gitkeep").write_bytes(b"")

    # 临时库 + 一条数据，用来核对快照完整性
    con = sqlite3.connect(str(db_file))
    con.execute("CREATE TABLE questions (id INTEGER PRIMARY KEY, stem TEXT)")
    con.execute("INSERT INTO questions (stem) VALUES ('求极限 sin(x)/x')")
    con.execute("CREATE TABLE folders (id INTEGER PRIMARY KEY, name TEXT)")
    con.execute("INSERT INTO folders (name) VALUES ('高等数学')")
    con.commit()
    con.close()

    os.environ["CUOTIBEN_BACKUPS_DIR"] = str(backups)
    os.environ["CUOTIBEN_DATABASE_URL"] = f"sqlite:///{db_file.as_posix()}"
    # 隔离 uploads：备份会把图片快照过去，绝不能碰真实 uploads/
    os.environ["CUOTIBEN_UPLOADS_DIR"] = str(uploads)

    # 让 backup.py 里的 resolve_db_path() 走环境变量分支
    from app.services import data_service as DS
    from app.services import image_service as IS
    import backup as B

    expect("backups_dir() 读到了临时目录",
           B.backups_dir() == backups, str(B.backups_dir()))
    expect("uploads_dir() 也隔离到了临时目录",
           B.uploads_dir() == uploads, str(B.uploads_dir()))
    expect("与 image_service 是同一个 uploads 目录",
           B.uploads_dir() == IS.UPLOADS_DIR,
           f"backup={B.uploads_dir()} image={IS.UPLOADS_DIR}")

    # ---------------------------------------------------------------
    section("首次备份")
    r = B.run_backup(keep=30)
    expect("返回 created=True", r["created"] is True)
    expect("返回 skipped=False", r["skipped"] is False)
    dest = Path(r["dest"])
    expect("备份文件已生成", dest.is_file(), dest.name)
    today = dt.datetime.now().date().isoformat()
    expect("文件名严格是 cuotiben_{YYYY-MM-DD}.db",
           dest.name == f"cuotiben_{today}.db", dest.name)
    expect("落在 backups/ 顶层（不是子目录）", dest.parent == backups)

    # 快照可用性与内容
    snap = sqlite3.connect(str(dest))
    try:
        rows_q = snap.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        rows_f = snap.execute("SELECT COUNT(*) FROM folders").fetchone()[0]
        stem = snap.execute("SELECT stem FROM questions").fetchone()[0]
    finally:
        snap.close()
    expect("备份是可用数据库", rows_q == 1 and rows_f == 1, f"{rows_q}/{rows_f}")
    expect("备份内容与源一致", stem == "求极限 sin(x)/x", stem)
    expect("没有残留 .tmp 临时文件",
           not list(backups.glob("*.tmp")), [p.name for p in backups.glob("*.tmp")])

    # ---------------------------------------------------------------
    section("图片也被备份（这是一次真实事故的补救）")
    # 事故：自检隔离失败把 uploads/ 清空，数据库记录还在、文件没了，
    # 而当时的每日备份只有 .db —— 任何备份都救不回图片。
    snap_dir = backups / f"uploads_{today}"
    expect("生成了当天的图片快照目录", snap_dir.is_dir(), snap_dir.name)

    src_files = sorted(p.relative_to(uploads).as_posix()
                       for p in uploads.rglob("*") if p.is_file())
    snap_files = sorted(p.relative_to(snap_dir).as_posix()
                        for p in snap_dir.rglob("*") if p.is_file())
    expect("快照里的文件集合与源一致", snap_files == src_files,
           f"源={src_files} 快照={snap_files}")

    same = all((snap_dir / rel).read_bytes() == (uploads / rel).read_bytes()
               for rel in src_files)
    expect("**每个文件内容逐字节一致**", same)
    expect("快照统计报出了文件数",
           r["uploads"]["files"] == len(src_files),
           f"{r['uploads']['files']} vs {len(src_files)}")

    # 硬链接：内容一致但不占额外磁盘（同 inode）
    linked = 0
    for rel in src_files:
        try:
            if (snap_dir / rel).stat().st_ino == (uploads / rel).stat().st_ino:
                linked += 1
        except OSError:
            pass
    expect("**快照用硬链接，不占额外磁盘（inode 相同）**",
           linked == len(src_files), f"{linked}/{len(src_files)} 个同 inode")

    # 真还原一次：删掉源里的图，再从快照拷回，内容要一致
    victim = uploads / "2026" / "a.jpg"
    original = victim.read_bytes()
    victim.unlink()
    expect("（准备）源里的图已删除", not victim.exists())
    restored = uploads / "2026" / "a.jpg"
    restored.write_bytes((snap_dir / "2026" / "a.jpg").read_bytes())
    expect("**能从快照还原图片且内容一致**",
           restored.read_bytes() == original)
    # 还原动作会写新 inode，可能断开链接；重建快照让后续断言基于真实状态
    B.run_backup(keep=30, force=True)

    # ---------------------------------------------------------------
    section("图片快照也参与保留天数清理")
    up_snaps = B.list_uploads_snapshots(backups)
    expect("list_uploads_snapshots 认得当天快照",
           any(d == dt.date.fromisoformat(today) for d, _ in up_snaps),
           [p.name for _d, p in up_snaps])
    # 造 5 个更早的图片快照目录，配合 keep=3 应只留最新的
    base_up = dt.date(2026, 3, 1)
    for i in range(5):
        d = base_up + dt.timedelta(days=i)
        p = backups / f"uploads_{d.isoformat()}"
        p.mkdir(exist_ok=True)
        (p / "x.jpg").write_bytes(b"old")
    before_n = len(B.list_uploads_snapshots(backups))
    B.run_backup(keep=3)
    after = B.list_uploads_snapshots(backups)
    expect("**按 keep 裁掉了过期的图片快照**",
           len(after) <= 3, f"{before_n} -> {len(after)}")
    expect("最新的那份图片快照仍在",
           any(d == dt.date.fromisoformat(today) for d, _ in after),
           [p.name for _d, p in after])

    # ---------------------------------------------------------------
    section("同一天重复运行")
    r2 = B.run_backup(keep=30)
    expect("同一天不重做（skipped=True）", r2["skipped"] is True)
    expect("同一天不覆盖（created=False）", r2["created"] is False)
    mtime_before = dest.stat().st_mtime_ns
    B.run_backup(keep=30)
    expect("重复运行不改动已有文件", dest.stat().st_mtime_ns == mtime_before)

    r3 = B.run_backup(keep=30, force=True)
    expect("--force 时重新备份", r3["created"] is True and r3["skipped"] is False)

    # ---------------------------------------------------------------
    section("保留最近 N 天（按文件名日期清理）")
    # 造 35 个历史备份；故意把最旧的那份 mtime 设成最新，
    # 若实现按 mtime 清理就会误删它 —— 这里正是要抓这个错误。
    oldest = None
    base = dt.date(2026, 1, 1)
    for i in range(35):
        day = base + dt.timedelta(days=i)
        p = backups / f"cuotiben_{day.isoformat()}.db"
        p.write_bytes(b"SQLite format 3\x00")
        if oldest is None:
            oldest = p
    # 把最旧的名字配上最新的 mtime
    os.utime(oldest, None)

    r4 = B.run_backup(keep=30)
    remaining = sorted(p.name for p in backups.glob("cuotiben_*.db"))
    expect("清理后正好保留 30 份", len(remaining) == 30, len(remaining))
    expect("**最旧的那份被删掉（因为按文件名日期，不按 mtime）**",
           oldest.name not in remaining, oldest.name)
    # 注意：轮里还包含"今天"那一份（真实日期 2026-10-01），
    # 它比所有合成日期都新，所以保留窗口是 合成07..合成35 + 今天。
    # 第一版断言"最后一份是合成35"是错的 —— 今天是更新的那个。
    synth_last = f"cuotiben_{(base + dt.timedelta(days=34)).isoformat()}.db"
    expect("被删的正好是最早的 6 个合成日期",
           all(f"cuotiben_{(base + dt.timedelta(days=i)).isoformat()}.db"
               not in remaining for i in range(6)),
           f"剩余最早={remaining[0]}")
    expect("保留了最晚的合成日期",
           synth_last in remaining, synth_last)
    expect("今天那份也在（它最新，不该被清掉）",
           f"cuotiben_{today}.db" in remaining, f"cuotiben_{today}.db")
    expect("pruned 报告了删除数量", len(r4["pruned"]) == 6, len(r4["pruned"]))

    # ---------------------------------------------------------------
    section("清理不碰其它内容")
    manual = backups / "20260101-030000"
    manual.mkdir(exist_ok=True)
    (manual / "cuotiben.db").write_bytes(b"manual")
    (backups / "backup.log").write_text("log\n", encoding="utf-8")
    (backups / "notes.txt").write_text("keep me\n", encoding="utf-8")
    (backups / ".gitkeep").touch()

    B.run_backup(keep=3)
    expect("手动备份子目录未被删除", manual.is_dir())
    expect("backup.log 未被删除", (backups / "backup.log").is_file())
    expect("无关文件未被删除", (backups / "notes.txt").is_file())
    expect(".gitkeep 未被删除", (backups / ".gitkeep").is_file())
    expect("--keep 3 后只剩 3 份每日备份",
           len(list(backups.glob("cuotiben_*.db"))) == 3,
           len(list(backups.glob("cuotiben_*.db"))))

    # 非法文件名不该影响解析
    (backups / "cuotiben_not-a-date.db").write_bytes(b"x")
    (backups / "cuotiben_2026-13-45.db").write_bytes(b"x")
    entries = B.list_backups(backups)
    expect("list_backups 忽略非法日期文件名",
           all("not-a-date" not in p.name and "13-45" not in p.name
               for _d, p in entries), len(entries))

    # ---------------------------------------------------------------
    section("CLI 行为")
    out = run_cli("--list")
    expect("--list 退出码 0", out.returncode == 0, out.returncode)
    expect("--list 列出备份", "cuotiben_" in out.stdout, out.stdout.strip()[:60])

    out = run_cli("--dry-run")
    expect("--dry-run 退出码 0", out.returncode == 0, out.returncode)
    expect("--dry-run 输出带 [dry-run] 前缀", "[dry-run]" in out.stdout,
           out.stdout.strip()[:60])

    out = run_cli("--keep", "0")
    expect("--keep 0 被拒绝（退出码 2）", out.returncode == 2, out.returncode)

    out = run_cli("--quiet")
    expect("--quiet 成功时无输出", out.returncode == 0 and out.stdout.strip() == "",
           repr(out.stdout[:60]))

    # --no-images：只备数据库，不生成图片快照
    noimg_dir = work / "noimg"
    noimg_dir.mkdir(exist_ok=True)
    (noimg_dir / "cuotiben_2026-05-05.db").write_bytes(b"SQLite format 3\x00")
    out = run_cli("--dir", str(noimg_dir), "--force", "--no-images")
    expect("--no-images 退出码 0", out.returncode == 0, out.returncode)
    expect("--no-images 明确提示已跳过图片",
           "已跳过" in out.stdout or "--no-images" in out.stdout,
           out.stdout.strip()[:80])
    expect("**--no-images 时没有生成图片快照**",
           not B.list_uploads_snapshots(noimg_dir),
           [p.name for _d, p in B.list_uploads_snapshots(noimg_dir)])
    today2 = dt.datetime.now().date().isoformat()
    expect("--no-images 时数据库备份仍然生成",
           (noimg_dir / f"cuotiben_{today2}.db").is_file())

    # 默认（不带 --no-images）在同一目录下应当生成图片快照
    out = run_cli("--dir", str(noimg_dir), "--force")
    expect("默认会生成图片快照",
           bool(B.list_uploads_snapshots(noimg_dir)),
           [p.name for _d, p in B.list_uploads_snapshots(noimg_dir)])

    # ---------------------------------------------------------------
    section("库不存在时的行为")
    missing = work / "nope.db"
    original = B.resolve_db_path
    B.resolve_db_path = lambda: missing
    try:
        B.run_backup(keep=30)
        expect("库不存在时抛 FileNotFoundError", False, "没有抛异常")
    except FileNotFoundError as exc:
        expect("库不存在时抛 FileNotFoundError", True, str(exc)[:40])
    except Exception as exc:  # noqa: BLE001
        expect("库不存在时抛 FileNotFoundError", False, f"抛了 {type(exc).__name__}")
    finally:
        B.resolve_db_path = original

    # ---------------------------------------------------------------
    section("启动兜底（maybe_run_daily_backup）")
    # 清掉今天那份，才能观察"补做"行为
    for p in backups.glob("cuotiben_*.db"):
        p.unlink()
    os.environ.pop("CUOTIBEN_AUTO_BACKUP", None)
    expect("默认（未设 CUOTIBEN_AUTO_BACKUP）不备份",
           B.maybe_run_daily_backup() is None)
    expect("默认关闭时确实没生成文件",
           not list(backups.glob("cuotiben_*.db")))

    os.environ["CUOTIBEN_AUTO_BACKUP"] = "1"
    first = B.maybe_run_daily_backup()
    expect("开启后补做了今天这一次",
           bool(first) and first.get("created") is True, first)
    second = B.maybe_run_daily_backup()
    expect("同一天第二次调用不再备份（幂等）", second is None, second)
    expect("开启后文件已生成",
           (backups / f"cuotiben_{today}.db").is_file())

    # 配置写错时不能炸（备份不该拦住应用启动）
    os.environ["CUOTIBEN_AUTO_BACKUP"] = "yes-please"
    expect("无法识别的开关值视为关闭",
           B.maybe_run_daily_backup() is None)
    os.environ["CUOTIBEN_AUTO_BACKUP"] = "1"
    bad_dir = work / "readonly_parent"
    os.environ["CUOTIBEN_BACKUPS_DIR"] = str(bad_dir)
    try:
        # 用一个**文件**占住目录名，mkdir 必然失败
        bad_dir.write_text("not a directory", encoding="utf-8")
        expect("备份出错时不抛异常（只返回 None）",
               B.maybe_run_daily_backup() is None)
    finally:
        bad_dir.unlink(missing_ok=True)
        os.environ["CUOTIBEN_BACKUPS_DIR"] = str(backups)
        os.environ.pop("CUOTIBEN_AUTO_BACKUP", None)

    # ---------------------------------------------------------------
    section("不污染真实 backups/")
    # 这条守的是"自检绝不能往用户真实备份目录里写东西"。
    # 允许目录里本来就有的合法内容（.gitkeep，以及用户自己跑过的
    # 每日备份 cuotiben_*.db / uploads_* ），只拒绝**本自检产生的**其它文件。
    real = PROJECT / "backups"
    allowed = re.compile(r"^(\.gitkeep|cuotiben_\d{4}-\d{2}-\d{2}\.db"
                         r"|uploads_\d{4}-\d{2}-\d{2})$")
    real_entries = sorted(p.name for p in real.iterdir()) if real.is_dir() else []
    unexpected = [n for n in real_entries if not allowed.match(n)]
    expect("真实 backups/ 里没有自检产生的意外文件",
           not unexpected, unexpected)
    # 还要确认临时目录的备份没有误落进来
    expect("真实 backups/ 里没有 .tmp 残留",
           not [n for n in real_entries if n.endswith(".tmp")],
           [n for n in real_entries if n.endswith(".tmp")])

    # WAL 模式下的一致性快照
    section("WAL / 未提交事务下的一致性")
    wal_db = work / "wal.db"
    con = sqlite3.connect(str(wal_db))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    con.execute("INSERT INTO t (v) VALUES ('committed')")
    con.commit()
    # 故意留一个未提交事务（不 commit），快照不应包含它
    con.execute("INSERT INTO t (v) VALUES ('uncommitted')")
    wal_dest = work / "wal_snapshot.db"
    DS.sqlite_snapshot(wal_db, wal_dest)
    snap = sqlite3.connect(str(wal_dest))
    try:
        cnt = snap.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    finally:
        snap.close()
    con.rollback()
    con.close()
    expect("WAL 下快照只含已提交数据", cnt == 1, cnt)

    print("\n" + "-" * 74)
    print(f"合计 {OK + FAIL} 项，通过 {OK}，失败 {FAIL}")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
