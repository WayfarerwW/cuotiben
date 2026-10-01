"""复习业务逻辑自检（requirements.md 2.10 / 2.11 / 2.12 / 5.1 / 5.2 / 5.3）。

覆盖 AGENTS.md 4.1 的四条硬规则与 4.2 的记忆曲线：
  - 创建题目自动生成首条记录（interval_index=0，next_review_at=now()+3天）
  - 间隔序列来自 settings，可配
  - 打勾不校验待复习状态，任何题任何时间都能打
  - 打勾后 interval_index 重置为 0，next_review_at = now() + INTERVALS[0]
  - 允许重复打勾，不抛 409
  - 撤销 = 软删除最近一条记录，上一轮自动生效
  - "今日"以本地时区 0 点为边界
  - 补卡：今日队列 = 今日到期 + 最多 N 道逾期（重点优先 + 逾期最久）

走临时数据库，不碰 data/cuotiben.db。

运行：python tools/verify_review_service.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def expect_raises(label: str, exc_types: tuple[type, ...], fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
        check(label, False, "没有抛异常")
    except exc_types as e:
        check(label, True, f"{type(e).__name__}: {str(e)[:56]}")
    except Exception as e:  # noqa: BLE001
        check(label, False, f"抛了意外异常 {type(e).__name__}: {str(e)[:56]}")


def main() -> int:
    print("=" * 80)
    print("review service 自检")
    print("=" * 80)

    # 固定时区，保证"今日"边界断言可复现
    os.environ["CUOTIBEN_TIMEZONE"] = "Asia/Shanghai"
    tz = ZoneInfo("Asia/Shanghai")

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CUOTIBEN_DATABASE_URL"] = (
            f"sqlite:///{(Path(tmp) / 'review.db').as_posix()}"
        )

        from app.database import SessionLocal, engine, init_db
        from app.models import Question, ReviewRecord
        from app.services import folder_service as fsvc
        from app.services import question_service as qsvc
        from app.services import review_service as rsvc
        from app.services import settings_service
        from app.services.review_service import (
            NothingToUndoError,
            ReviewQuestionNotFoundError,
        )

        init_db()

        with SessionLocal() as db:
            settings_service.ensure_default_settings(db)
            subj = fsvc.create_folder(db, "高等数学")
            cat = fsvc.create_folder(db, "极限与连续", parent_id=subj.id)

            # ============ 1. 创建题目自动生成首条记录 ============
            print("\n-- 首条 review_record（需求 2.10）--")
            before = datetime.now(UTC)
            q1 = qsvc.create_question(db, folder_id=cat.id, stem="求 lim sinx/x",
                                      answer="1", tags=["极限"], is_starred=True)

            rec = rsvc.current_record(db, q1.id)
            check("创建题目自动生成首条记录", rec is not None)
            check("interval_index=0", rec.interval_index == 0, str(rec.interval_index))
            check("review_count=0", rec.review_count == 0, str(rec.review_count))
            check("last_review_at 为空（还未复习过）", rec.last_review_at is None)
            check("next_review_at 带时区", rec.next_review_at.tzinfo is not None)
            delta = (rec.next_review_at - before).total_seconds() / 86400
            check("next_review_at = now() + 3 天", 2.99 <= delta <= 3.01,
                  f"{delta:.4f} 天，next={rec.next_review_at.isoformat()}")
            check("起点是 3 天而非 1 天（AGENTS.md 4.2）", 2.9 < delta < 3.1)

            # ============ 2. 间隔序列来自 settings ============
            print("\n-- 间隔序列可配（需求 2.10）--")
            check("默认 intervals=[3,7,15,30]",
                  settings_service.get_intervals(db) == [3, 7, 15, 30],
                  str(settings_service.get_intervals(db)))

            settings_service.update_settings(db, intervals=[5, 10, 20, 40])
            q2 = qsvc.create_question(db, folder_id=cat.id, stem="可配间隔验证")
            rec2 = rsvc.current_record(db, q2.id)
            delta2 = (rec2.next_review_at - datetime.now(UTC)).total_seconds() / 86400
            check("改 settings 后新题按新间隔（5 天）", 4.99 <= delta2 <= 5.01,
                  f"{delta2:.4f} 天")

            # 打勾也用新的 INTERVALS[0]
            tick = datetime.now(UTC)
            r_check = rsvc.check(db, q2.id, now=tick)
            d3 = (r_check.next_review_at - tick).total_seconds() / 86400
            check("打勾用 INTERVALS[0]=5 天", abs(d3 - 5) < 0.01, f"{d3:.4f} 天")

            settings_service.update_settings(db, intervals=[3, 7, 15, 30])
            check("恢复默认 intervals", settings_service.get_intervals(db) == [3, 7, 15, 30])

            # ============ 3. 打勾：任何时间都能打，重置为 0 ============
            print("\n-- 打勾（需求 2.11 / AGENTS.md 4.1）--")
            q3 = qsvc.create_question(db, folder_id=cat.id, stem="打勾目标")
            # 手动把当前记录改成"未到期"，验证打勾不校验待复习状态
            cur = rsvc.current_record(db, q3.id)
            cur.next_review_at = datetime.now(UTC) + timedelta(days=99)
            cur.interval_index = 3
            db.commit()

            future = rsvc.current_record(db, q3.id)
            check("前置条件：该题处于未到期状态",
                  future.next_review_at > datetime.now(UTC) and future.interval_index == 3,
                  f"interval_index={future.interval_index}")

            t0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
            r1 = rsvc.check(db, q3.id, now=t0)
            check("未到期的题也能打勾（不校验待复习状态）", r1 is not None, f"record id={r1.id}")
            check("打勾后 interval_index 重置为 0", r1.interval_index == 0,
                  str(r1.interval_index))
            check("打勾后 next_review_at = now + 3 天",
                  abs((r1.next_review_at - t0).total_seconds() / 86400 - 3) < 0.01,
                  r1.next_review_at.isoformat())
            check("打勾写入 last_review_at", r1.last_review_at is not None,
                  r1.last_review_at.isoformat() if r1.last_review_at else "None")
            check("review_count 递增到 1", r1.review_count == 1, str(r1.review_count))

            # 允许重复打勾：连打 3 次
            t1 = t0 + timedelta(days=1)
            t2 = t0 + timedelta(days=2)
            r2 = rsvc.check(db, q3.id, now=t1)
            r3 = rsvc.check(db, q3.id, now=t2)
            check("允许重复打勾（连续 3 次均成功，不抛 409）",
                  r1.id != r2.id != r3.id and r2.interval_index == 0 and r3.interval_index == 0,
                  f"ids={[r1.id, r2.id, r3.id]}")
            check("重复打勾仍是新增记录（不是改旧记录）",
                  len(db.query(ReviewRecord).filter_by(question_id=q3.id).all()) == 4,
                  "1 首条 + 3 次打勾 = 4 条")
            check("review_count 累加到 3", r3.review_count == 3, str(r3.review_count))
            check("current_record 取最新一条", rsvc.current_record(db, q3.id).id == r3.id)

            # mastery 传入时写入 mastery_level
            r4 = rsvc.check(db, q3.id, mastery=2, now=t2 + timedelta(days=1))
            check("打勾记录 mastery_level", r4.mastery_level == 2, str(r4.mastery_level))
            check("mastery=2 仍重置 interval_index=0（取 5.2 口径，非 5.1 推进）",
                  r4.interval_index == 0, str(r4.interval_index))
            r5 = rsvc.check(db, q3.id, now=t2 + timedelta(days=2))
            check("不传 mastery 时 mastery_level=0", r5.mastery_level == 0)

            # ============ 4. 撤销 ============
            print("\n-- 撤销（需求 5.3）--")
            before_uncheck = rsvc.current_record(db, q3.id)
            undone = rsvc.uncheck(db, q3.id)
            check("撤销软删除最近一条", undone.id == before_uncheck.id
                  and undone.deleted_at is not None,
                  f"deleted_at={undone.deleted_at.isoformat() if undone.deleted_at else None}")
            check("撤销后当前记录回退到上一条",
                  rsvc.current_record(db, q3.id).id == r4.id,
                  f"现在是 record {rsvc.current_record(db, q3.id).id}，期望 {r4.id}")
            check("被撤销的记录仍在库中（软删除而非物理删除）",
                  db.query(ReviewRecord).filter_by(id=undone.id).count() == 1)

            # 连续撤销到底：该题共 1 条首记录 + 5 次打勾 = 6 条，需撤销 6 次
            # （上面已调用 1 次，还剩 5 次）
            for _ in range(5):
                rsvc.uncheck(db, q3.id)
            rest = rsvc.current_record(db, q3.id)
            check("一路撤销后无任何未删除记录（首条也被撤掉）",
                  rest is None, f"current_record={rest}")
            expect_raises("撤销到无记录可撤 -> NothingToUndoError",
                          (NothingToUndoError,), rsvc.uncheck, db, q3.id)
            check("全部撤销后该题共 6 条记录，均被软删除而非物理删除",
                  db.query(ReviewRecord).filter_by(question_id=q3.id).count() == 6
                  and db.query(ReviewRecord).filter(
                      ReviewRecord.question_id == q3.id,
                      ReviewRecord.deleted_at.is_(None)).count() == 0,
                  f"总数={db.query(ReviewRecord).filter_by(question_id=q3.id).count()} "
                  f"未删除=0")

            expect_raises("对不存在的题打勾 -> ReviewQuestionNotFoundError",
                          (ReviewQuestionNotFoundError,), rsvc.check, db, 99999)
            expect_raises("对不存在的题撤销 -> ReviewQuestionNotFoundError",
                          (ReviewQuestionNotFoundError,), rsvc.uncheck, db, 99999)

            # ============ 5. 今日边界 ============
            print("\n-- 今日边界（本地时区 0 点）--")
            # 2026-06-10 12:00 UTC = 2026-06-10 20:00 Asia/Shanghai
            now_utc = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
            start, end = rsvc.today_bounds(now_utc)
            start_local = start.astimezone(tz)
            end_local = end.astimezone(tz)
            check("今日起点 = 本地 00:00", start_local.hour == 0
                  and start_local.minute == 0 and start_local.second == 0,
                  f"本地起点 {start_local.isoformat()}")
            check("今日终点 = 次日本地 00:00", end_local.hour == 0
                  and end_local.date() == start_local.date() + timedelta(days=1),
                  f"本地终点 {end_local.isoformat()}")
            check("本地起点转 UTC 正确（上海 UTC+8）",
                  start == datetime(2026, 6, 9, 16, 0, tzinfo=UTC),
                  f"UTC 起点 {start.isoformat()}")

            # UTC 与本地日期不同的时刻：2026-06-09 20:00 UTC = 06-10 04:00 上海
            cross = datetime(2026, 6, 9, 20, 0, tzinfo=UTC)
            cs, _ce = rsvc.today_bounds(cross)
            check("跨日时刻按本地日期划分（UTC 06-09 20:00 -> 本地 06-10）",
                  cs.astimezone(tz).date() == datetime(2026, 6, 10).date(),
                  f"本地日期 {cs.astimezone(tz).date()}")

            # ============ 6. 今日队列与补卡 ============
            print("\n-- 今日队列与补卡（需求 2.12）--")
            base = datetime(2026, 6, 10, 4, 0, tzinfo=UTC)  # 本地 12:00
            day_start, _ = rsvc.today_bounds(base)

            def make_due(stem: str, next_at: datetime, *, starred: bool = False) -> Question:
                q = qsvc.create_question(db, folder_id=cat.id, stem=stem, is_starred=starred)
                rec = rsvc.current_record(db, q.id)
                rec.next_review_at = next_at
                db.commit()
                return q

            # 今日到期：本地今天 18:00 = UTC 10:00
            q_due = make_due("今日到期", datetime(2026, 6, 10, 10, 0, tzinfo=UTC))
            # 逾期 1 天（本地昨天）
            q_ov1 = make_due("逾期1天", day_start - timedelta(days=1))
            # 逾期 20 天，且是重点 -> 应进补卡且优先
            q_ov20_star = make_due("逾期20天重点", day_start - timedelta(days=20), starred=True)
            # 逾期 30 天，非重点
            q_ov30 = make_due("逾期30天", day_start - timedelta(days=30))
            # 未来到期（本地明天）-> 不应出现
            q_future = make_due("明日到期", day_start + timedelta(days=1, hours=2))

            items, meta = rsvc.list_today(db, now=base)
            got_ids = [i.question_id for i in items]
            check("未来到期的题不在今日队列", q_future.id not in got_ids,
                  f"队列={[i.stem for i in items]}")
            check("今日到期题在队列中", q_due.id in got_ids)
            check("meta.due_count 含今日到期", meta["due_count"] == 1,
                  f"due_count={meta['due_count']}")
            check("默认补卡上限 = 20", meta["backfill_limit"] == 20,
                  str(meta["backfill_limit"]))
            check("补卡数与逾期总数分离",
                  meta["overdue_total"] == 3 and meta["backfill_count"] == 3,
                  f"overdue_total={meta['overdue_total']} backfill={meta['backfill_count']}")

            # 排序：重点优先，其次逾期最久
            overdue_ids = [i.question_id for i in items if i.is_overdue]
            check("逾期题排序：重点优先", overdue_ids[0] == q_ov20_star.id,
                  f"顺序={[(i.stem, i.overdue_days, i.is_starred) for i in items if i.is_overdue]}")
            check("非重点按逾期最久在前",
                  overdue_ids[1] == q_ov30.id, f"第二是 {overdue_ids[1]}（期望 {q_ov30.id}）")

            # 积压标记：逾期 >=14 天
            by_id = {i.question_id: i for i in items}
            check("逾期 20 天标记为积压", by_id[q_ov20_star.id].is_backlog)
            check("逾期 30 天标记为积压", by_id[q_ov30.id].is_backlog)
            check("逾期 1 天不算积压", not by_id[q_ov1.id].is_backlog)
            check("逾期天数计算正确",
                  by_id[q_ov20_star.id].overdue_days == 20
                  and by_id[q_ov30.id].overdue_days == 30,
                  f"20天={by_id[q_ov20_star.id].overdue_days} "
                  f"30天={by_id[q_ov30.id].overdue_days}")
            check("未逾期题 overdue_days=0", by_id[q_due.id].overdue_days == 0)

            # 补卡上限生效
            settings_service.update_settings(db, backfill_limit=1)
            items2, meta2 = rsvc.list_today(db, now=base)
            check("补卡上限=1 时只带 1 道逾期题",
                  meta2["backfill_count"] == 1 and meta2["total"] == 2,
                  f"backfill={meta2['backfill_count']} total={meta2['total']}")
            check("留下的那道是重点题", any(
                  i.question_id == q_ov20_star.id for i in items2 if i.is_overdue))
            settings_service.update_settings(db, backfill_limit=20)

            # count 与 list_today 同口径
            check("count_due 与 list_today 长度一致",
                  rsvc.count_due(db, now=base) == len(rsvc.list_today(db, now=base)[0]),
                  f"count={rsvc.count_due(db, now=base)}")

            # 打勾后该题离开队列
            rsvc.check(db, q_due.id, now=base)
            items3, _ = rsvc.list_today(db, now=base)
            check("打勾后题目离开今日队列", q_due.id not in [i.question_id for i in items3],
                  f"队列={[i.stem for i in items3]}")
            check("打勾后 count 下降", rsvc.count_due(db, now=base) == len(items3))

            # 撤销后回到队列
            rsvc.uncheck(db, q_due.id)
            items4, _ = rsvc.list_today(db, now=base)
            check("撤销后题目回到队列（上一轮自动生效）",
                  q_due.id in [i.question_id for i in items4])

            # 队列项字段完整性（前端契约）
            sample = next(i for i in items4 if i.question_id == q_due.id)
            for field in ("question_id", "stem", "answer", "images", "tags",
                          "folder_name", "is_starred", "interval_index", "next_review_at"):
                check(f"队列项含 {field}",
                      hasattr(sample, field), f"{getattr(sample, field, None)!r}")
            check("队列项带 folder_name", sample.folder_name == "极限与连续",
                  str(sample.folder_name))

            # ============ 7. 软删除的题不进队列 ============
            print("\n-- 软删除与队列 --")
            qsvc.delete_question(db, q_ov30.id)
            items5, _ = rsvc.list_today(db, now=base)
            check("软删除的题不在今日队列",
                  q_ov30.id not in [i.question_id for i in items5])

            # ============ 8. 补卡统计 ============
            print("\n-- 补卡统计 --")
            st = rsvc.stats(db, now=base)
            check("stats 含今日补卡数 / 连续天数 / 积压数",
                  {"today_backfill_count", "consecutive_days", "backlog_count"} <= set(st),
                  str(st))
            check("stats.backlog_count 与 backlog_question_ids 一致",
                  st["backlog_count"] == len(
                      rsvc.backlog_question_ids(db, now=base)),
                  str(st["backlog_count"]))

        engine.dispose()

    # ============ 9. 补卡：排序、重置、统计 ============
    # 独立一段干净数据，避免上面已软删除/已打勾的题干扰。
    # 不重新 import/reload 模块，而是显式拿一个指向新库的 engine 与会话，
    # 这样各 service 仍用同一套模块级函数，行为与生产一致。
    # ignore_cleanup_errors：Windows 下连接池可能仍持有 db 文件句柄，
    # 临时目录删除会 PermissionError；不影响测试结论。
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp2:
        from sqlalchemy.orm import Session as _Session

        from app.database import Base
        from app.database import create_db_engine as _create_engine
        from app.services import folder_service as fsvc2
        from app.services import question_service as qsvc2
        from app.services import review_service as rsvc2
        from app.services import settings_service as ss2

        engine2 = _create_engine(f"sqlite:///{(Path(tmp2) / 'backfill.db').as_posix()}")
        Base.metadata.create_all(bind=engine2)
        Sess = lambda: _Session(bind=engine2)  # noqa: E731

        with Sess() as db:
            ss2.ensure_default_settings(db)
            subj = fsvc2.create_folder(db, "数学")
            cat = fsvc2.create_folder(db, "极限", parent_id=subj.id)
            base2 = datetime(2026, 6, 10, 4, 0, tzinfo=UTC)
            day_start2, _ = rsvc2.today_bounds(base2)

            def mk2(stem: str, days_overdue: int, starred: bool) -> int:
                q = qsvc2.create_question(db, folder_id=cat.id, stem=stem,
                                          is_starred=starred)
                rec = rsvc2.current_record(db, q.id)
                assert rec is not None
                rec.next_review_at = day_start2 - timedelta(days=days_overdue)
                db.commit()
                return q.id

            # ---------- 9.1 逾期排序：is_starred DESC, 逾期天数 DESC ----------
            print("\n-- 逾期题排序（重点优先 + 逾期最久）--")
            # 故意让 id 升序与逾期降序错开，以暴露"靠 id 兜底"的错误实现
            a = mk2("A 逾期30 非重点", 30, False)
            b = mk2("B 逾期20 重点", 20, True)
            c = mk2("C 逾期10 非重点", 10, False)
            d = mk2("D 逾期5 重点", 5, True)

            items, meta = rsvc2.list_today(db, now=base2)
            order = [(i.stem, i.overdue_days, i.is_starred) for i in items]
            check("逾期排序 = 重点优先，其次逾期最久",
                  [i.question_id for i in items] == [b, d, a, c],
                  f"{order}")
            check("重点题内部的相对顺序也按逾期降序",
                  order[0][1] == 20 and order[1][1] == 5, f"{order[:2]}")
            check("非重点内部按逾期降序",
                  order[2][1] == 30 and order[3][1] == 10, f"{order[2:]}")
            check("backfill_limit 从 settings 读取，默认 20",
                  meta["backfill_limit"] == 20, str(meta["backfill_limit"]))

            # ---------- 9.2 补卡标记 ----------
            print("\n-- 补卡标记 is_backfill --")
            rec_b = rsvc2.current_record(db, b)
            check("逾期题尚未打勾前不算补卡",
                  rec_b is not None and rec_b.is_backfill is False)
            rsvc2.check(db, b, now=base2)
            check("对逾期题打勾记为补卡",
                  rsvc2.current_record(db, b).is_backfill is True)

            # 未逾期的题打勾不算补卡
            fresh = qsvc2.create_question(db, folder_id=cat.id, stem="新题")
            rsvc2.check(db, fresh.id, now=base2)
            check("未逾期题打勾不算补卡",
                  rsvc2.current_record(db, fresh.id).is_backfill is False)
            check("今日补卡数量只算补卡",
                  rsvc2.stats(db, now=base2)["today_backfill_count"] == 1,
                  str(rsvc2.stats(db, now=base2)["today_backfill_count"]))

            # ---------- 9.3 一键重置：全部重置 ----------
            print("\n-- 一键重置积压（全部重置）--")
            before_backlog = rsvc2.backlog_question_ids(db, now=base2)
            # 此刻只有 A 仍是积压题：B 已在 9.2 被打勾，next_review_at 推到 now+3 天，
            # 自然脱离积压区。这正好顺带验证"打勾后不再算积压"。
            check("积压题 = 逾期 >=14 天（A30；B 已打勾脱离积压）",
                  before_backlog == [a], f"{before_backlog}")

            res = rsvc2.reset_backlog(db, now=base2)
            check("affected_count 等于积压题数",
                  res.affected_count == len(before_backlog) == 1,
                  f"affected={res.affected_count} 积压题={before_backlog}")
            check("mode=all", res.mode == "all", res.mode)
            for qid in before_backlog:
                rec = rsvc2.current_record(db, qid)
                check(f"题 {qid} interval_index 归零", rec.interval_index == 0,
                      str(rec.interval_index))
                delta = (rec.next_review_at - base2).total_seconds() / 86400
                check(f"题 {qid} next_review_at = now()+3 天",
                      abs(delta - 3) < 0.01, f"{delta:.4f} 天")
                check(f"题 {qid} 重置记为补卡", rec.is_backfill is True)
            check("重置后不再有积压题",
                  rsvc2.backlog_question_ids(db, now=base2) == [],
                  str(rsvc2.backlog_question_ids(db, now=base2)))
            check("重置不新增记录（是重新排期而非打勾）",
                  db.query(ReviewRecord).filter_by(question_id=a).count() == 1,
                  "A 仍是 1 条记录")

            # ---------- 9.4 一键重置：分散到未来 N 天 ----------
            print("\n-- 一键重置积压（分散到未来 N 天）--")
            # 重新造 5 道积压题（逾期 15~19 天）
            spread_ids = [mk2(f"积压{i}", 15 + i, False) for i in range(5)]
            check("5 道题都进入积压区",
                  set(rsvc2.backlog_question_ids(db, now=base2)) == set(spread_ids),
                  str(len(rsvc2.backlog_question_ids(db, now=base2))))

            res2 = rsvc2.reset_backlog(db, spread=True, spread_days=4, now=base2)
            check("mode=spread 且 spread_days 回显",
                  res2.mode == "spread" and res2.spread_days == 4,
                  f"{res2.mode}/{res2.spread_days}")
            check("affected_count=5", res2.affected_count == 5, str(res2.affected_count))

            dues = []
            for qid in spread_ids:
                rec = rsvc2.current_record(db, qid)
                dues.append((rec.next_review_at - base2).total_seconds() / 86400)
            dues_sorted = sorted(dues)
            check("分散后到期时间互不相同（错开而非同一天）",
                  len(set(round(x, 4) for x in dues)) == len(dues),
                  f"{[round(x, 2) for x in dues_sorted]}")
            check("分散窗口落在 [3, 3+4] 天内（base 3 天 + 0~4 天错开）",
                  abs(dues_sorted[0] - 3) < 0.01 and abs(dues_sorted[-1] - 7) < 0.01,
                  f"最早 {dues_sorted[0]:.2f} 天，最晚 {dues_sorted[-1]:.2f} 天")
            check("所有分散题 interval_index 均为 0",
                  all(rsvc2.current_record(db, q).interval_index == 0
                      for q in spread_ids))
            check("分散后不再有积压题",
                  rsvc2.backlog_question_ids(db, now=base2) == [])

            # 默认窗口取 settings.backfill_reset_days
            ss2.update_settings(db, backfill_reset_days=6)
            more = [mk2(f"再积压{i}", 20 + i, False) for i in range(3)]
            res3 = rsvc2.reset_backlog(db, spread=True, now=base2)
            check("spread 未传 days 时取 settings.backfill_reset_days=6",
                  res3.spread_days == 6, str(res3.spread_days))
            d3 = sorted(
                (rsvc2.current_record(db, q).next_review_at - base2).total_seconds() / 86400
                for q in more
            )
            check("默认窗口下最晚落在 3+6=9 天",
                  abs(d3[-1] - 9) < 0.01, f"{[round(x, 2) for x in d3]}")

            # ---------- 9.5 无积压时的重置 ----------
            print("\n-- 无积压时的重置 --")
            res_empty = rsvc2.reset_backlog(db, now=base2)
            check("无积压题时 affected_count=0 且不报错",
                  res_empty.affected_count == 0 and res_empty.affected_question_ids == [],
                  f"affected={res_empty.affected_count}")
            # 曾经因为"无积压"的提前返回跳过了 days 计算，导致 spread_days 恒为 None，
            # 调用方无法判断本次请求实际用的是多大窗口。这里锁住该回归。
            res_empty_spread = rsvc2.reset_backlog(db, spread=True, spread_days=7,
                                                   now=base2)
            check("无积压题时 spread_days 仍回显生效窗口",
                  res_empty_spread.mode == "spread"
                  and res_empty_spread.spread_days == 7,
                  f"{res_empty_spread.mode}/{res_empty_spread.spread_days}")
            res_empty_default = rsvc2.reset_backlog(db, spread=True, now=base2)
            # 期望值取自当前配置，而不是写死 14 —— 本脚本前面已把
            # backfill_reset_days 改成过别的值
            want_days = settings_service.get_backfill_reset_days(db)
            check("无积压题且未传 days 时回退 settings.backfill_reset_days",
                  res_empty_default.spread_days == want_days,
                  f"{res_empty_default.spread_days}（配置值 {want_days}）")
            res_empty_all = rsvc2.reset_backlog(db, now=base2)
            check("非分散模式无积压时 spread_days 为 None",
                  res_empty_all.spread_days is None,
                  str(res_empty_all.spread_days))

            # ---------- 9.6 连续补卡天数（用独立库，数据完全可控）----------
            # 上面的库里已有大量"今天"产生的补卡记录，无法精确构造断档场景，
            # 所以单独开一个干净库，只插入我指定的补卡记录。
            print("\n-- 连续补卡天数 --")

        engine2.dispose()

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp3:
        from sqlalchemy.orm import Session as _S3

        from app.database import Base as _Base3
        from app.database import create_db_engine as _create3
        from app.services import review_service as rsvc3
        from app.services import settings_service as ss3

        engine3 = _create3(f"sqlite:///{(Path(tmp3) / 'streak.db').as_posix()}")
        _Base3.metadata.create_all(bind=engine3)
        Sess3 = lambda: _S3(bind=engine3)  # noqa: E731

        base3 = datetime(2026, 6, 10, 4, 0, tzinfo=UTC)  # 本地 12:00

        with Sess3() as db:
            ss3.ensure_default_settings(db)
            from app.models import Question
            from app.services import folder_service as fsvc3

            # questions.folder_id 有外键约束，必须先建真实的文件夹
            subj3 = fsvc3.create_folder(db, "数学")
            cat3 = fsvc3.create_folder(db, "极限", parent_id=subj3.id)

            q = Question(folder_id=cat3.id, stem="连续天数测试")
            db.add(q)
            db.commit()

            def backfill_at(days_before: int) -> None:
                """插入一条 is_backfill 记录，last_review_at 落在 N 天前。"""
                moment = base3 - timedelta(days=days_before)
                rec = rsvc3.current_record(db, q.id)
                if rec is None:
                    rsvc3.create_initial_record(db, q.id)
                    db.commit()
                    rec = rsvc3.current_record(db, q.id)
                rec.is_backfill = True
                rec.last_review_at = moment
                db.commit()
                # 下一次插入需要新记录，否则会覆盖同一条
                rsvc3.create_initial_record(db, q.id)
                db.commit()

            # 没有任何补卡
            st = rsvc3.stats(db, now=base3)
            check("无补卡记录 -> 今日补卡数 0、连续 0",
                  st["today_backfill_count"] == 0 and st["consecutive_days"] == 0,
                  str(st))

            # 只有今天补卡
            backfill_at(0)
            st = rsvc3.stats(db, now=base3)
            check("仅今天补卡 -> 今日 1、连续 1",
                  st["today_backfill_count"] == 1 and st["consecutive_days"] == 1,
                  str(st))

            # 昨天也补卡 -> 连续 2
            backfill_at(1)
            st = rsvc3.stats(db, now=base3)
            check("昨天也补卡 -> 连续 2",
                  st["consecutive_days"] == 2, str(st["consecutive_days"]))

            # 前天也补卡 -> 连续 3
            backfill_at(2)
            st = rsvc3.stats(db, now=base3)
            check("前天也补卡 -> 连续 3",
                  st["consecutive_days"] == 3, str(st["consecutive_days"]))

            # 大前天断档（不插），再往前插一条 -> 连续仍应为 3
            backfill_at(4)
            st = rsvc3.stats(db, now=base3)
            check("中间断档则停止累计（仍为 3）",
                  st["consecutive_days"] == 3, str(st["consecutive_days"]))

            # 今天没补卡（把今天那条挪到 10 天前）-> 从昨天起算，连续 2
            today_rec = db.query(type(rec)).filter(
                type(rec).question_id == q.id,
                type(rec).is_backfill.is_(True),
            ).order_by(type(rec).id).first()
            today_rec.last_review_at = base3 - timedelta(days=10)
            db.commit()
            st = rsvc3.stats(db, now=base3)
            check("今天无补卡时从昨天起算（连得上昨天/前天 -> 2）",
                  st["today_backfill_count"] == 0 and st["consecutive_days"] == 2,
                  f"today={st['today_backfill_count']} streak={st['consecutive_days']}")

        engine3.dispose()

    print("-" * 80)
    failed = [r for r in results if not r[1]]
    print(f"合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for n, _, d in failed:
        print(f"  FAILED: {n} {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
