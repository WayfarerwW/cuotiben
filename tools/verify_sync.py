"""GitHub 自动同步的验证（AGENTS.md 五）。

**真的推**：把项目复制一份到临时目录，在那里建一个本地 bare 仓库当远端，
真跑 add + commit + push，再从 bare 仓库核对内容。不 mock git ——
mock 掉的正是最容易错的部分（凭据传递、push 引用、忽略规则）。

**为什么必须复制到临时目录**：同步会真的 `git add` / `commit`。
第一版直接在本仓库上跑，结果往真实历史里塞了两个
`chore(sync): 自动同步` 提交、还把 origin 改成了临时 bare 仓库，
需要手工 reset 才恢复。sync_service 为此提供了 set_project_root()，
本脚本第一件事就是切过去。

覆盖：
- 配置读取（含非法值回退）
- 凭据处理：strip_credentials / url_has_credentials / redact
- 未启用 / 未配置时拒绝同步，且是 NotConfiguredError（router 会翻 409）
- 首次同步真的产生提交并出现在远端
- 忽略规则：data/ uploads/ backups/ 的内容**不会**进入提交
- **token 不落盘**：跑完同步后 .git/config 里搜不到 token
- remote URL 里的凭据会被清理
- 无改动时不产生空提交
- 目录指纹能察觉变化
- 状态报告字段完整、不回显 token

不用 pytest，照本项目惯例直接跑、打印 [PASS]/[FAIL]、用退出码表示结果。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OK = FAIL = 0
FAILED: list[str] = []

FAKE_TOKEN = "ghp_FAKEtokenForTestOnly1234567890"

#: 复制到临时工作区时需要哪些顶层目录/文件。
#: 不复制 data/uploads/backups（脚本自己造干净的），也不复制 .env。
COPY_ITEMS = (
    ".git", "app", "tools", "docs", "AGENTS.md", "README.md",
    "requirements.txt", "run.py", "start.bat", "start.sh",
    ".gitignore", ".gitattributes",
)


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


GIT = None


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [GIT, *args], cwd=str(cwd) if cwd else None,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def main() -> int:  # noqa: C901 - 线性用例清单
    global GIT

    from app.services import sync_service as S

    GIT = S.find_git_executable()
    if not GIT:
        print("找不到 git，无法验证")
        return 2
    print(f"[env] git = {GIT}")

    # ---------------------------------------------------------------
    # 先切到隔离副本：绝不能在本仓库上跑同步
    # ---------------------------------------------------------------
    work = Path(tempfile.mkdtemp(prefix="verify_sync_"))
    sandbox = work / "repo"
    sandbox.mkdir()
    for name in COPY_ITEMS:
        src = PROJECT / name
        if not src.exists():
            continue
        dst = sandbox / name
        if src.is_dir():
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)
    for name in ("data", "uploads", "backups"):
        (sandbox / name).mkdir(exist_ok=True)
        (sandbox / name / ".gitkeep").touch()

    S.set_project_root(sandbox)
    expect("自检已切到隔离副本（不在项目本体上提交）",
           S.project_root() == sandbox, S.project_root().name)
    expect("副本是有效的 git 仓库", (sandbox / ".git").is_dir())

    bare = work / "remote.git"
    git("init", "--bare", "--initial-branch=main", str(bare))
    expect("bare 远端已建好", (bare / "HEAD").is_file(), bare.name)

    try:
        return run_cases(S, sandbox, bare)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_cases(S, sandbox: Path, bare: Path) -> int:  # noqa: C901
    # ---------- 单元：纯函数 ----------
    section("凭据与配置处理")
    expect("strip_credentials 去掉 token",
           S.strip_credentials(f"https://{FAKE_TOKEN}@github.com/u/r.git")
           == "https://github.com/u/r.git")
    expect("strip_credentials 保留端口",
           S.strip_credentials("https://user:pw@github.com:443/u/r.git")
           == "https://github.com:443/u/r.git")
    expect("strip_credentials 不动本地路径",
           S.strip_credentials("D:/tmp/bare.git") == "D:/tmp/bare.git")
    expect("strip_credentials 不动 scp 风格",
           S.strip_credentials("git@github.com:u/r.git") == "git@github.com:u/r.git")
    expect("url_has_credentials 识别凭据",
           S.url_has_credentials(f"https://{FAKE_TOKEN}@github.com/u/r.git") is True)
    expect("url_has_credentials 干净 URL 为 False",
           S.url_has_credentials("https://github.com/u/r.git") is False)
    expect("redact 抹掉 token",
           FAKE_TOKEN not in S.redact(f"fatal: auth {FAKE_TOKEN} failed", FAKE_TOKEN))
    expect("redact 无 token 时原样返回", S.redact("plain", "") == "plain")

    os.environ["GIT_SYNC_ENABLED"] = "1"
    os.environ["GIT_SYNC_INTERVAL_MINUTES"] = "7"
    cfg = S.load_config()
    expect("读到 enabled", cfg.enabled is True)
    expect("读到 interval_minutes", cfg.interval_minutes == 7, cfg.interval_minutes)
    os.environ["GIT_SYNC_INTERVAL_MINUTES"] = "abc"
    expect("非法间隔回退默认 10", S.load_config().interval_minutes == 10)
    os.environ["GIT_SYNC_INTERVAL_MINUTES"] = "0"
    expect("0 回退默认 10（不能每 0 分钟转一次）",
           S.load_config().interval_minutes == 10)
    os.environ["GIT_SYNC_INTERVAL_MINUTES"] = "7"

    # ---------- 未配置就拒绝 ----------
    section("未启用 / 未配置时拒绝同步")
    os.environ["GIT_SYNC_ENABLED"] = "0"
    os.environ["GIT_SYNC_REPO_URL"] = "https://github.com/u/r.git"
    try:
        S.sync_now(reason="test")
        expect("未启用时抛 NotConfiguredError", False, "没有抛异常")
    except S.NotConfiguredError:
        expect("未启用时抛 NotConfiguredError", True)
    except Exception as exc:  # noqa: BLE001
        expect("未启用时抛 NotConfiguredError", False, f"抛了 {type(exc).__name__}")

    os.environ["GIT_SYNC_ENABLED"] = "1"
    os.environ["GIT_SYNC_REPO_URL"] = ""
    try:
        S.sync_now(reason="test")
        expect("未配 repo_url 时抛 NotConfiguredError", False, "没有抛异常")
    except S.NotConfiguredError:
        expect("未配 repo_url 时抛 NotConfiguredError", True)
    except Exception as exc:  # noqa: BLE001
        expect("未配 repo_url 时抛 NotConfiguredError", False,
               f"抛了 {type(exc).__name__}")

    # ---------- 真推送 ----------
    section("真同步到本地 bare 仓库")
    os.environ["GIT_SYNC_TOKEN"] = FAKE_TOKEN
    os.environ["GIT_SYNC_REPO_URL"] = bare.as_uri()
    os.environ["GIT_SYNC_BRANCH"] = "main"
    os.environ["GIT_SYNC_AUTHOR_NAME"] = "sync-test"
    os.environ["GIT_SYNC_AUTHOR_EMAIL"] = "sync-test@localhost"
    S.reset_state_for_tests()

    # 造"被忽略的用户数据"，确认它不会进提交
    secret_db = sandbox / "data" / "cuotiben.db"
    secret_img = sandbox / "uploads" / "2026" / "probe.jpg"
    secret_bak = sandbox / "backups" / "20260101-000000" / "cuotiben.db"
    secret_db.write_bytes(b"SQLite format 3\x00")
    secret_img.parent.mkdir(parents=True, exist_ok=True)
    secret_img.write_bytes(b"\xff\xd8\xff\xe0fake")
    secret_bak.parent.mkdir(parents=True, exist_ok=True)
    secret_bak.write_bytes(b"backup-snapshot")

    # 造一个"代码"改动，让同步有东西可提交
    code_probe = sandbox / "tools" / "_verify_sync_probe.txt"
    code_probe.write_text("sync probe\n", encoding="utf-8")

    try:
        result = S.sync_now(reason="test")
    except S.SyncError as exc:
        expect("同步执行成功", False, f"SyncError: {exc}")
        result = {}
    expect("同步返回 ok", result.get("status") == "ok", result.get("status"))
    expect("产生了提交", result.get("action") in ("committed", "pushed"),
           result.get("action"))
    expect("确实推送了", result.get("pushed") is True)
    expect("返回了 commit sha", len(result.get("commit") or "") == 40,
           (result.get("commit") or "")[:12])

    log = git("log", "--oneline", "--all", cwd=bare)
    expect("远端有提交记录", log.returncode == 0 and log.stdout.strip() != "",
           log.stdout.strip()[:60])
    names = git("ls-tree", "-r", "--name-only", "main", cwd=bare).stdout.split()
    expect("提交里有我们造的代码文件", "tools/_verify_sync_probe.txt" in names,
           names[:5])
    expect("**被忽略的数据库没有进提交**",
           not any(n.endswith("cuotiben.db") for n in names))
    expect("**上传的图片没有进提交**",
           not any(n.endswith("probe.jpg") for n in names))
    expect("**备份产物没有进提交**",
           not any("backups/20260101" in n for n in names))
    expect("提交作者用的是配置里的身份",
           "sync-test" in git("log", "-1", "--format=%an <%ae>", cwd=bare).stdout)

    # ---------- token 不落盘 ----------
    section("token 不落盘（关键安全断言）")
    config_file = sandbox / ".git" / "config"
    config_text = config_file.read_text(encoding="utf-8", errors="replace")
    expect("** .git/config 里没有 token **", FAKE_TOKEN not in config_text)

    git("remote", "set-url", "origin",
        f"https://{FAKE_TOKEN}@github.com/u/r.git", cwd=sandbox)
    config_text = config_file.read_text(encoding="utf-8", errors="replace")
    expect("（铺垫）手工写入 token 后 config 里确实有", FAKE_TOKEN in config_text,
           "用于确认下面的清理断言不是空断言")
    cleaned = S.sanitize_remote_credentials()
    config_text = config_file.read_text(encoding="utf-8", errors="replace")
    expect("sanitize_remote_credentials 报告已清理", cleaned is True)
    expect("**清理后 .git/config 里没有 token **", FAKE_TOKEN not in config_text)

    os.environ["GIT_SYNC_REPO_URL"] = bare.as_uri()
    report = S.status_report()
    expect("** /sync/status 不回显 token **", FAKE_TOKEN not in repr(report))
    expect("但如实报告已配置 token", report["token_present"] is True)

    # ---------- 忽略状态 ----------
    section("忽略规则与状态报告")
    ignored = report["watched_dirs_gitignored"]
    expect("状态里 data 标注为被忽略", ignored["data"] is True, ignored)
    expect("状态里 uploads 标注为被忽略", ignored["uploads"] is True)
    expect("状态里 backups 标注为被忽略", ignored["backups"] is True)
    expect("状态里有说明文案（避免误解数据也同步了）",
           "不会提交" in report["note"] or "不入库" in report["note"])
    for key in ("enabled", "configured", "ready", "repo_url", "interval_minutes",
                "branch", "thread_alive", "dirty", "watched", "state", "note"):
        expect(f"状态含字段 {key}", key in report)

    # ---------- 无改动不产生空提交 ----------
    section("无改动时的行为")
    before = git("rev-parse", "main", cwd=bare).stdout.strip()
    S.reset_state_for_tests()
    try:
        again = S.sync_now(reason="test-nochange")
        expect("无改动时仍返回 ok", again.get("status") == "ok", again.get("status"))
        expect("无改动时不产生新提交",
               again.get("action") in ("nothing-to-commit", "pushed-existing"),
               again.get("action"))
    except S.SyncError as exc:
        expect("无改动时仍返回 ok", False, f"SyncError: {exc}")
    after = git("rev-parse", "main", cwd=bare).stdout.strip()
    expect("远端 HEAD 未变（没有空提交）", before == after,
           f"{before[:8]} -> {after[:8]}")

    # ---------- 目录指纹 ----------
    section("监控目录指纹")
    fp1 = S.watched_fingerprints()
    expect("三个目录都有指纹", set(fp1) == {"data", "uploads", "backups"}, list(fp1))
    expect("指纹非空", all(bool(v) for v in fp1.values()))
    secret_img.write_bytes(b"\xff\xd8\xff\xe0changed-content")
    fp2 = S.watched_fingerprints()
    expect("内容变化后 uploads 指纹改变", fp2["uploads"] != fp1["uploads"])
    expect("未改动的 data 指纹不变", fp2["data"] == fp1["data"])

    # ---------- 后台线程 ----------
    section("后台线程启动")
    S.reset_state_for_tests()
    boot = S.start_background_sync(run_first=True)
    expect("后台同步已启动", boot.get("started") is True, boot.get("reason", ""))
    expect("报告了间隔分钟数", boot.get("interval_minutes") == 7,
           boot.get("interval_minutes"))
    time.sleep(4)
    state = S.get_state()
    expect("线程存活", S.status_report()["thread_alive"] is True)
    expect("首次同步已执行", state.sync_count >= 1, state.sync_count)
    expect("首次同步未失败", state.failure_count == 0, state.last_error[:60])
    S.stop_background_sync()
    time.sleep(0.5)
    boot2 = S.start_background_sync(run_first=False)
    expect("停止后可以再次启动", boot2.get("started") is True)
    S.stop_background_sync()

    print("\n" + "-" * 74)
    print(f"合计 {OK + FAIL} 项，通过 {OK}，失败 {FAIL}")
    for name in FAILED:
        print(f"  FAILED: {name}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
