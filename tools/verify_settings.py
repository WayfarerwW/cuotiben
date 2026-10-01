"""设置自检（requirements.md 3.8 / 4.8）。

覆盖：
  - 首次启动（settings 表为空）写入默认值
  - 库中只有部分 key 时补齐缺失项，且**不覆盖**已存在的值
  - GET /settings 返回 intervals / backfill_limit / backfill_reset_days
  - PUT /settings 批量更新，只改传入字段
  - 校验：intervals 必须 4 项、正整数、严格递增；backfill_limit >= 0；
    backfill_reset_days >= 1；非法值 422
  - 配置改动真正影响业务：改 intervals 后新建题目的首个间隔随之变化
  - 坏值容错：库里的值被改坏时回退默认，不让接口 500

走临时数据库，不碰 data/cuotiben.db。

运行：python tools/verify_settings.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

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


def main() -> int:
    print("=" * 80)
    print("设置自检")
    print("=" * 80)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        os.environ["CUOTIBEN_DATABASE_URL"] = (
            f"sqlite:///{(Path(tmp) / 'settings.db').as_posix()}"
        )

        from app.database import SessionLocal, engine, init_db

        init_db()
        from app.models import (
            DEFAULT_BACKFILL_LIMIT,
            DEFAULT_BACKFILL_RESET_DAYS,
            DEFAULT_INTERVALS,
            Setting,
        )
        from app.services import settings_service as ssvc

        # ============ 1. 首次启动写入默认值 ============
        print("\n-- 首次启动（settings 表为空）--")
        with SessionLocal() as db:
            check("启动前 settings 表为空", db.query(Setting).count() == 0,
                  f"{db.query(Setting).count()} 行")

            created = ssvc.ensure_default_settings(db)
            check("首次启动写入 3 个默认配置", len(created) == 3, f"新增: {created}")
            check("默认 key 齐全",
                  {s.key for s in db.query(Setting).all()}
                  == {"intervals", "backfill_limit", "backfill_reset_days"},
                  str(sorted(s.key for s in db.query(Setting).all())))

            got = ssvc.get_all_settings(db)
            check("默认 intervals=[3,7,15,30]",
                  got["intervals"] == DEFAULT_INTERVALS, str(got["intervals"]))
            check("默认 backfill_limit=20",
                  got["backfill_limit"] == DEFAULT_BACKFILL_LIMIT,
                  str(got["backfill_limit"]))
            check("默认 backfill_reset_days=14",
                  got["backfill_reset_days"] == DEFAULT_BACKFILL_RESET_DAYS,
                  str(got["backfill_reset_days"]))

            # 幂等
            again = ssvc.ensure_default_settings(db)
            check("重复启动不新增", again == [], f"新增: {again}")

        # ============ 2. 部分缺失 / 被清空的情况 ============
        print("\n-- 库不完整时的恢复 --")
        with SessionLocal() as db:
            # 模拟"库被清空"与"只有部分 key"
            db.query(Setting).delete()
            db.commit()
            check("模拟库被清空", db.query(Setting).count() == 0)

            created2 = ssvc.ensure_default_settings(db)
            check("库被清空后启动能重新补齐", len(created2) == 3, f"新增: {created2}")

            # 只留一个 key，且值被用户改过
            db.query(Setting).filter(Setting.key != "backfill_limit").delete()
            db.commit()
            row = db.query(Setting).filter_by(key="backfill_limit").one()
            row.value = "5"
            db.commit()

            created3 = ssvc.ensure_default_settings(db)
            check("只补缺失的 key（不重复补 backfill_limit）",
                  set(created3) == {"intervals", "backfill_reset_days"},
                  f"新增: {created3}")
            after = ssvc.get_all_settings(db)
            check("已有配置不被默认值覆盖（backfill_limit 仍为 5）",
                  after["backfill_limit"] == 5, str(after["backfill_limit"]))
            check("补进来的默认值正确",
                  after["intervals"] == DEFAULT_INTERVALS
                  and after["backfill_reset_days"] == DEFAULT_BACKFILL_RESET_DAYS,
                  str(after))

        # ============ 3. 坏值容错 ============
        print("\n-- 坏值容错（不让接口 500）--")
        with SessionLocal() as db:
            def set_raw(key: str, value: str) -> None:
                row = db.query(Setting).filter_by(key=key).one_or_none()
                if row is None:
                    db.add(Setting(key=key, value=value))
                else:
                    row.value = value
                db.commit()

            set_raw("intervals", "{不是 JSON")
            check("intervals 非法 JSON -> 回退默认",
                  ssvc.get_intervals(db) == DEFAULT_INTERVALS,
                  str(ssvc.get_intervals(db)))

            set_raw("intervals", '["a","b"]')
            check("intervals 非数字 -> 回退默认",
                  ssvc.get_intervals(db) == DEFAULT_INTERVALS)

            set_raw("intervals", "[3,-1,15,30]")
            check("intervals 含非正数 -> 回退默认",
                  ssvc.get_intervals(db) == DEFAULT_INTERVALS)

            set_raw("backfill_limit", "abc")
            check("backfill_limit 非数字 -> 回退默认",
                  ssvc.get_backfill_limit(db) == DEFAULT_BACKFILL_LIMIT)

            set_raw("backfill_reset_days", "")
            check("backfill_reset_days 空串 -> 回退默认",
                  ssvc.get_backfill_reset_days(db) == DEFAULT_BACKFILL_RESET_DAYS)

            # 恢复干净状态
            ssvc.update_settings(db, intervals=DEFAULT_INTERVALS,
                                 backfill_limit=DEFAULT_BACKFILL_LIMIT,
                                 backfill_reset_days=DEFAULT_BACKFILL_RESET_DAYS)

        # ============ 4. service 层校验 ============
        print("\n-- 写入校验（service 层兜底）--")
        from app.services.settings_service import SettingsError

        with SessionLocal() as db:
            def expect_error(label: str, **kwargs) -> None:
                try:
                    ssvc.update_settings(db, **kwargs)
                    check(label, False, "没有抛异常")
                except SettingsError as e:
                    check(label, True, f"{type(e).__name__}: {str(e)[:40]}")

            expect_error("intervals 非递增被拒", intervals=[3, 7, 7, 30])
            expect_error("intervals 含 0 被拒", intervals=[0, 7, 15, 30])
            expect_error("backfill_limit 为负被拒", backfill_limit=-1)
            expect_error("backfill_reset_days 为 0 被拒", backfill_reset_days=0)

            check("合法 intervals 可写入",
                  ssvc.update_settings(db, intervals=[1, 2, 3, 4]) is None
                  and ssvc.get_intervals(db) == [1, 2, 3, 4])
            ssvc.update_settings(db, intervals=DEFAULT_INTERVALS)

        # ============ 5. 配置真正影响业务 ============
        print("\n-- 配置生效：改动影响建题的首个间隔 --")
        from app.services import folder_service, question_service, review_service

        with SessionLocal() as db:
            subj = folder_service.create_folder(db, "数学")
            cat = folder_service.create_folder(db, "极限", parent_id=subj.id)

            q_before = question_service.create_question(db, folder_id=cat.id, stem="默认间隔")
            rec_before = review_service.current_record(db, q_before.id)
            delta_before = (rec_before.next_review_at - datetime.now(UTC)).total_seconds() / 86400
            check("默认配置下首条记录约 3 天",
                  2.99 <= delta_before <= 3.01, f"{delta_before:.4f} 天")

            ssvc.update_settings(db, intervals=[10, 20, 30, 40])
            q_after = question_service.create_question(db, folder_id=cat.id, stem="新间隔")
            rec_after = review_service.current_record(db, q_after.id)
            delta_after = (rec_after.next_review_at - datetime.now(UTC)).total_seconds() / 86400
            check("改 intervals 后新题按新值（10 天）",
                  9.99 <= delta_after <= 10.01, f"{delta_after:.4f} 天")

            # 打勾也用新值
            tick = datetime.now(UTC)
            r = review_service.check(db, q_before.id, now=tick)
            d = (r.next_review_at - tick).total_seconds() / 86400
            check("打勾也用新的 INTERVALS[0]", abs(d - 10) < 0.01, f"{d:.4f} 天")

            ssvc.update_settings(db, intervals=DEFAULT_INTERVALS)

            # backfill_limit 生效
            ssvc.update_settings(db, backfill_limit=3)
            check("backfill_limit 改动生效",
                  ssvc.get_backfill_limit(db) == 3, str(ssvc.get_backfill_limit(db)))

            # backfill_reset_days 生效（用于分散重置的默认窗口）
            ssvc.update_settings(db, backfill_reset_days=7)
            check("backfill_reset_days 改动生效",
                  ssvc.get_backfill_reset_days(db) == 7)

            # 清理：造一道积压题验证分散窗口读取该配置
            q_bg = question_service.create_question(db, folder_id=cat.id, stem="积压")
            rec_bg = review_service.current_record(db, q_bg.id)
            rec_bg.next_review_at = datetime.now(UTC) - timedelta(days=30)
            db.commit()
            res = review_service.reset_backlog(db, spread=True)
            check("分散重置的默认窗口取自 settings.backfill_reset_days",
                  res.spread_days == 7, f"spread_days={res.spread_days}")

            ssvc.update_settings(db, backfill_reset_days=DEFAULT_BACKFILL_RESET_DAYS)

        # ============ 6. HTTP 层 ============
        print("\n-- HTTP 层 --")
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app) as client:
            r = client.get("/settings")
            check("GET /settings -> 200", r.status_code == 200,
                  f"HTTP {r.status_code} {r.text[:80]}")
            body = r.json()
            check("GET 返回三个配置项",
                  set(body) == {"intervals", "backfill_limit", "backfill_reset_days"},
                  str(sorted(body)))
            check("GET 类型正确（intervals 是数组而非 JSON 字符串）",
                  isinstance(body["intervals"], list)
                  and isinstance(body["backfill_limit"], int)
                  and isinstance(body["backfill_reset_days"], int),
                  str({k: type(v).__name__ for k, v in body.items()}))

            r = client.put("/settings", json={"backfill_limit": 33})
            check("PUT 只传一个字段 -> 200", r.status_code == 200)
            upd = r.json()
            check("该字段被更新", upd["backfill_limit"] == 33, str(upd["backfill_limit"]))
            check("未传字段保持不变", upd["intervals"] == DEFAULT_INTERVALS,
                  str(upd["intervals"]))

            r = client.put("/settings", json={"intervals": [2, 4, 8, 16]})
            check("PUT 更新 intervals", r.json()["intervals"] == [2, 4, 8, 16],
                  str(r.json()["intervals"]))
            r = client.get("/settings")
            check("PUT 结果已持久化（GET 复核）",
                  r.json()["intervals"] == [2, 4, 8, 16], str(r.json()["intervals"]))

            # 校验
            for payload, label in (
                ({"intervals": [3, 7, 7, 30]}, "非严格递增"),
                ({"intervals": [0, 7, 15, 30]}, "含 0"),
                ({"intervals": [3, 7]}, "长度不为 4"),
                ({"intervals": [3, 7, 15, 30, 60]}, "长度超过 4"),
                ({"backfill_limit": -1}, "limit 为负"),
                ({"backfill_reset_days": 0}, "reset_days 为 0"),
                ({"backfill_reset_days": 999}, "reset_days 过大"),
            ):
                r = client.put("/settings", json=payload)
                check(f"PUT 非法值被拒（{label}）-> 422", r.status_code == 422,
                      f"HTTP {r.status_code}")

            # 非法请求不应改动已有配置
            r = client.get("/settings")
            check("被拒的请求没有留下副作用",
                  r.json()["intervals"] == [2, 4, 8, 16], str(r.json()["intervals"]))

            # 空 body：什么都不改
            r = client.put("/settings", json={})
            check("PUT 空 body -> 200 且不改动",
                  r.status_code == 200 and r.json()["intervals"] == [2, 4, 8, 16],
                  str(r.json()["intervals"]))

            # ---- 传 null：删除该配置项，回退默认值 ----
            print("\n-- 传 null 回退默认值 --")
            r = client.put("/settings", json={"intervals": None})
            check("PUT intervals=null -> 200", r.status_code == 200,
                  f"HTTP {r.status_code} {r.text[:90]}")
            check("intervals 回退默认 [3,7,15,30]",
                  r.json()["intervals"] == DEFAULT_INTERVALS,
                  str(r.json()["intervals"]))
            check("其它项不受影响（backfill_limit 仍是 33）",
                  r.json()["backfill_limit"] == 33, str(r.json()["backfill_limit"]))

            with SessionLocal() as db:
                row = db.query(Setting).filter_by(key="intervals").one_or_none()
                check("回退默认是删除该行（而不是写入默认字符串）", row is None,
                      "行已删除" if row is None else f"仍存在: {row.value}")

            r = client.put("/settings", json={"backfill_limit": None,
                                              "backfill_reset_days": None})
            check("backfill_limit=null 回退默认",
                  r.json()["backfill_limit"] == DEFAULT_BACKFILL_LIMIT,
                  str(r.json()["backfill_limit"]))
            check("backfill_reset_days=null 回退默认",
                  r.json()["backfill_reset_days"] == DEFAULT_BACKFILL_RESET_DAYS,
                  str(r.json()["backfill_reset_days"]))

            # 回退后再设置仍然可用
            r = client.put("/settings", json={"intervals": [5, 10, 20, 40]})
            check("回退后可重新设置", r.json()["intervals"] == [5, 10, 20, 40],
                  str(r.json()["intervals"]))
            r = client.put("/settings", json={"intervals": None})
            check("可再次回退", r.json()["intervals"] == DEFAULT_INTERVALS)

            # 全部置 null -> 全部回默认，且此时库应为空
            client.put("/settings", json={"backfill_limit": 9})
            r = client.put("/settings", json={"intervals": None,
                                              "backfill_limit": None,
                                              "backfill_reset_days": None})
            check("三项同时置 null -> 全部回默认",
                  r.json() == {"intervals": DEFAULT_INTERVALS,
                               "backfill_limit": DEFAULT_BACKFILL_LIMIT,
                               "backfill_reset_days": DEFAULT_BACKFILL_RESET_DAYS},
                  str(r.json()))
            with SessionLocal() as db:
                check("全部回退后 settings 表为空（下次启动会重新写入默认值）",
                      db.query(Setting).count() == 0,
                      f"{db.query(Setting).count()} 行")
                # 重新播种，避免影响后续断言
                ssvc.ensure_default_settings(db)

        engine.dispose()

    print("-" * 80)
    failed = [r for r in results if not r[1]]
    print(f"合计 {len(results)} 项，通过 {len(results) - len(failed)}，失败 {len(failed)}")
    for n, _, d in failed:
        print(f"  FAILED: {n} {d}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
