"""POST /export/pdf 的 HTTP 级验证。

为什么除了 service 级还要这一层：
1. **run_in_threadpool 是否真的生效** —— 这是本项目对 WeasyPrint 的关键约束。
   验证方式：并发发起一次导出，同时轮询 /health。若渲染阻塞了事件循环，
   /health 会在渲染期间超时；用线程池则仍然秒回。
2. Content-Disposition / media type / 自定义头是否正确落到响应上。
3. 422 语义：scope 缺必填参数、空范围。

用真实 uvicorn（不是 TestClient）—— TestClient 走的是自己的传输层，
不一定能暴露事件循环被阻塞的问题。
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8981
BASE = f"http://127.0.0.1:{PORT}"
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


# 绕开系统代理：本机 127.0.0.1 请求被代理接管会连不上
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method: str, path: str, payload: dict | None = None, timeout: float = 120):
    """返回 (status, headers, body_bytes)。"""
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with _opener.open(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _upload_jpeg(name: str, color: tuple = (30, 60, 200)) -> dict:
    """走真实上传接口传一张图，返回上传响应。

    **必须走这个接口**，不能自己往 uploads/ 里写文件：真实上传会落到
    `uploads/YYYY/MM/DD/` 日期分片目录，而"平铺在 uploads/ 根下"这种
    形态恰好绕过了导出解析图片的那个 bug（见 _image_uri 的说明）。
    用户遇到的 500 正是分片路径触发的。
    """
    import io as _io

    from PIL import Image

    buf = _io.BytesIO()
    Image.new("RGB", (60, 40), color).save(buf, "JPEG", quality=88)
    boundary = "----http" + str(int(time.time() * 1000))
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode() + buf.getvalue() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(BASE + "/upload/image", data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with _opener.open(req, timeout=60) as resp:
        return json.loads(resp.read())


def wait_ready(proc: subprocess.Popen, seconds: float = 60) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if proc.poll() is not None:
            return False
        try:
            status, _h, _b = call("GET", "/health", timeout=3)
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="verify_export_http_"))
    db = tmp / "h.db"
    uploads = tmp / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["CUOTIBEN_DATABASE_URL"] = f"sqlite:///{db.as_posix()}"
    # 隔离 uploads：本自检会上传真图，绝不能写到项目真实目录
    env["CUOTIBEN_UPLOADS_DIR"] = str(uploads)
    env["CUOTIBEN_BACKUPS_DIR"] = str(tmp / "backups")
    env["GIT_SYNC_ENABLED"] = "0"

    runner = tmp / "serve.py"
    runner.write_text(
        "import os, sys, uvicorn\n"
        f"sys.path.insert(0, {str(Path.cwd())!r})\n"
        "from app.weasyprint_bootstrap import ensure_native_libs\n"
        "ensure_native_libs()\n"
        "from app.main import app\n"
        f"uvicorn.run(app, host='127.0.0.1', port={PORT}, log_level='error')\n",
        encoding="utf-8")

    proc = subprocess.Popen([sys.executable, str(runner)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        if not wait_ready(proc):
            print("服务未能启动。输出：")
            try:
                print(proc.stdout.read().decode("utf-8", "replace")[-2000:])
            except Exception:
                pass
            return 2

        # ---------------- 造数据 ----------------
        # 注意：题目只能挂在"二级大类"下，直接挂一级学科会被拒
        # （folder_service 的层级校验）。第一版把题挂在学科上，直接 400。
        st, _h, body = call("POST", "/folders", {"name": "数学", "parent_id": None})
        assert st in (200, 201), (st, body[:200])
        subject = json.loads(body)["id"]

        st, _h, body = call("POST", "/folders",
                            {"name": "极限与连续", "parent_id": subject})
        assert st in (200, 201), (st, body[:200])
        subj = json.loads(body)["id"]

        st, _h, body = call("POST", "/folders",
                            {"name": "空大类", "parent_id": subject})
        empty = json.loads(body)["id"]

        ids: list[int] = []
        for i, (stem, ans, tags) in enumerate([
            ("求极限 lim(x→0) sin(x)/x", "答案为 1，用重要极限。", ["重要极限"]),
            ("洛必达法则的适用条件", "0/0 或 ∞/∞ 型且可导。", ["重要极限", "可导条件"]),
            ("求 y=x^3 的导数", "y' = 3x²。", ["可导条件"]),
        ]):
            st, _h, body = call("POST", "/questions", {
                "folder_id": subj, "stem": stem, "answer": ans,
                "tags": tags, "is_starred": i == 1, "sort_order": i,
            })
            assert st in (200, 201), (st, body[:300])
            ids.append(json.loads(body)["id"])

        # ---------------- 各 scope ----------------
        section("HTTP 各 scope")
        st, h, body = call("POST", "/export/pdf",
                           {"scope": "folder", "folder_id": subj, "with_answer": True})
        expect("folder 导出 200", st == 200, st)
        expect("返回 application/pdf",
               h.get("content-type", "").startswith("application/pdf"),
               h.get("content-type"))
        expect("响应体是 PDF", body[:5] == b"%PDF-", body[:8])
        expect("Content-Length 与实体长度一致",
               int(h.get("content-length", -1)) == len(body),
               f"{h.get('content-length')} vs {len(body)}")
        expect("X-Question-Count 为 3", h.get("x-question-count") == "3",
               h.get("x-question-count"))
        cd = h.get("content-disposition", "")
        expect("Content-Disposition 含 filename*", "filename*=UTF-8''" in cd, cd[:70])
        expect("文件名含日期与范围", "%E6%8C%89%E6%96%87%E4%BB%B6%E5%A4%B9" in cd, "按文件夹")

        import _pdf_probe as P

        text = P.extract_text(body)
        expect("PDF 里中文可提取", "洛必达" in text)
        expect("答案区存在", "答案与解析" in text)

        st, _h, _b = call("POST", "/export/pdf", {"scope": "starred"})
        expect("starred 导出 200", st == 200, st)

        st, _h, b2 = call("POST", "/export/pdf",
                          {"scope": "tags", "tags": ["重要极限", "可导条件"]})
        expect("tags 导出 200", st == 200, st)
        expect("tags AND 命中 1 题，X-Question-Count=1",
               _h.get("x-question-count") == "1", _h.get("x-question-count"))

        st, _h, _b = call("POST", "/export/pdf",
                          {"scope": "manual", "question_ids": [ids[2], ids[0]]})
        expect("manual 导出 200", st == 200, st)
        expect("manual 命中 2 题", _h.get("x-question-count") == "2",
               _h.get("x-question-count"))

        st, _h, _b = call("POST", "/export/pdf",
                          {"scope": "review_queue", "with_answer": False})
        # 没有到期题时应当是 422（"该范围内没有题目"），而不是 500 或空 PDF
        expect("review_queue 无到期题时返回 422", st == 422, st)

        # ---------------- 422 语义 ----------------
        section("422 语义")
        st, _h, body = call("POST", "/export/pdf", {"scope": "manual"})
        expect("manual 缺 question_ids -> 422", st == 422, st)
        st, _h, body = call("POST", "/export/pdf", {"scope": "tags"})
        expect("tags 缺 tags -> 422", st == 422, st)
        st, _h, body = call("POST", "/export/pdf",
                            {"scope": "folder", "folder_id": empty})
        expect("空范围 -> 422（不是 200 空 PDF）", st == 422, st)
        st, _h, body = call("POST", "/export/pdf", {"scope": "不存在的范围"})
        expect("非法 scope -> 422", st == 422, st)
        st, _h, body = call("POST", "/export/pdf",
                            {"scope": "manual", "question_ids": [999999]})
        expect("不存在的题目 id -> 422", st == 422, st)

        # ---------------- 事件循环不被阻塞 ----------------
        section("run_in_threadpool（事件循环不被渲染阻塞）")
        # 用一道长题 + 多题制造一次耗时渲染，同时在渲染期间轮询 /health。
        big_ids = []
        for i in range(40):
            st, _h, body = call("POST", "/questions", {
                "folder_id": subj,
                "stem": f"第 {i} 道超长题干 " + "内容" * 120,
                "answer": "答案 " + "解释" * 40,
                "tags": ["重要极限"],
            })
            big_ids.append(json.loads(body)["id"])

        health_latencies: list[float] = []
        export_result: dict = {}

        def do_export():
            t0 = time.time()
            s, _hh, bb = call("POST", "/export/pdf",
                              {"scope": "manual", "question_ids": big_ids,
                               "with_answer": True}, timeout=300)
            export_result.update(status=s, seconds=time.time() - t0, size=len(bb))

        with cf.ThreadPoolExecutor(max_workers=2) as pool:
            fut = pool.submit(do_export)
            # 渲染进行中持续探活
            while not fut.done():
                t0 = time.time()
                try:
                    s, _hh, _bb = call("GET", "/health", timeout=10)
                    if s == 200:
                        health_latencies.append(time.time() - t0)
                except Exception as exc:  # 超时即视为被阻塞
                    health_latencies.append(float("inf"))
                    print(f"      /health 失败：{exc}")
                time.sleep(0.15)
            fut.result()

        expect("大文档导出成功", export_result.get("status") == 200,
               f"{export_result.get('status')} {export_result.get('size')} 字节 "
               f"耗时 {export_result.get('seconds'):.1f}s")
        expect("导出确实耗时（否则本项没测到东西）",
               export_result.get("seconds", 0) > 0.2,
               f"{export_result.get('seconds'):.2f}s")
        expect("渲染期间 /health 有被成功探活", len(health_latencies) >= 1,
               f"{len(health_latencies)} 次")
        worst = max(health_latencies) if health_latencies else float("inf")
        expect("渲染期间 /health 最长响应 < 2s（未被阻塞）", worst < 2.0,
               f"最长 {worst:.2f}s")
        expect("/health 没有超时（无 inf）",
               all(x != float("inf") for x in health_latencies))

        # ---------------- 真实上传的图片能导出（HTTP 层回归） ----------------
        section("真实上传图片后导出（用户报的 500 就在这里）")
        # 用户侧表现：任意范围点导出都 500。根因是 _image_uri 只按文件名在
        # uploads/ 根下平铺查找，而真实上传落在 uploads/YYYY/MM/DD/，
        # 于是落到兜底分支对**相对路径**调 as_uri() -> ValueError -> 500。
        up = _upload_jpeg("http-level.jpg")
        expect("上传接口返回了 file_path", bool(up.get("file_path")),
               up.get("file_path"))
        expect("**真实上传落在日期分片目录里**（不是 uploads/ 根下）",
               bool(re.fullmatch(r"uploads/\d{4}/\d{2}/\d{2}/[0-9a-f]+\.jpg",
                                 up.get("file_path") or "")),
               up.get("file_path"))
        expect("上传的图片确实写到了磁盘",
               (uploads / (up["file_path"]).replace("uploads/", "", 1)).is_file(),
               up.get("file_path"))

        st, _h, body = call("POST", "/questions", {
            "folder_id": subj, "stem": "带真实上传图的题", "answer": "答案",
            "tags": [], "sort_order": 99,
            "images": [{"url": up["file_path"], "kind": "stem"}],
        })
        with_img_id = json.loads(body)["id"] if st in (200, 201) else None
        expect("题目能挂上真实上传的图片", with_img_id is not None,
               f"{st} {body[:160]}")

        if with_img_id is not None:
            st, _h, body = call("POST", "/export/pdf",
                                {"scope": "manual", "question_ids": [with_img_id],
                                 "with_answer": True}, timeout=180)
            expect("**带真实上传图片的题目导出返回 200（不再 500）**",
                   st == 200, f"HTTP {st} {body[:160]}")
            expect("导出的是合法 PDF",
                   st == 200 and body[:5] == b"%PDF-", f"{len(body)} 字节")
            expect("**图片真的进了 PDF**",
                   st == 200 and (b"/Image" in body or b"/XObject" in body))

        # 图片文件不存在时也必须 200（缺图不该弄挂整次导出）
        st, _h, body = call("POST", "/questions", {
            "folder_id": subj, "stem": "图片缺失的题", "answer": "答案",
            "tags": [], "sort_order": 100,
            "images": [{"url": "uploads/2026/01/01/does-not-exist.jpg",
                        "kind": "stem"}],
        })
        missing_id = json.loads(body)["id"] if st in (200, 201) else None
        if missing_id is not None:
            st, _h, body = call("POST", "/export/pdf",
                                {"scope": "manual", "question_ids": [missing_id],
                                 "with_answer": False}, timeout=180)
            expect("**引用了不存在的图片时导出仍返回 200**",
                   st == 200, f"HTTP {st} {body[:160]}")

        print("\n" + "-" * 74)
        print(f"合计 {OK + FAIL} 项，通过 {OK}，失败 {FAIL}")
        for name in FAILED:
            print(f"  FAILED: {name}")
        return 1 if FAIL else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
