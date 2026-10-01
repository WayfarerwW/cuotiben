"""GitHub 自动同步（AGENTS.md 五）。

把工作区的改动定期 add + commit + push 到 GitHub。默认**关闭**，
只有 .env 里 GIT_SYNC_ENABLED=1 时才启动后台线程。

设计取舍（都有明确理由，改动前请先读）：

1. **只同步 git 跟踪的东西**。
   `data/`、`uploads/`、`backups/` 在 .gitignore 里是**刻意**忽略的
   （见 .gitignore「用户数据，不入库」），`git add -A` 不会暂存它们。
   本服务不会用 `-f` 去绕开 —— 那会把个人图片和数据库推到 GitHub。
   这三个目录仍然被**监控**：状态里能看到它们的指纹变化，变化时触发一次
   同步检查（代码可能也一起改了）。但只有代码/文档真的变了才有 commit。

2. **Token 不落盘**。
   不用 `https://{token}@github.com/...` 把凭据写进 .git/config ——
   那会以明文永久留在磁盘上。改用 GIT_ASKPASS 在 push 时临时提供：
   token 只存在于那一次子进程的环境变量里。
   启动时还会检查 remote URL，若已含凭据则**自动去掉**（防手滑）。
   自检脚本会直接断言 .git/config 里没有 token。

3. **不加锁会互相踩**。同步可能在后台线程、POST /sync/now、启动首次
   三处同时发生，用一个 Lock 串行化，并让后来者直接返回"已在同步中"。

4. **push 失败不吞掉**。错误记进状态并通过 /sync/status 暴露，
   否则"看起来在同步但一直失败"最难排查。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from ..database import PROJECT_ROOT

#: 仓库根目录。做成**可替换的模块变量**：自检脚本必须把它指向一个临时副本。
#: 同步会真的执行 `git add` / `commit`，在项目本体上跑测试会污染真实提交历史
#: （这一点是在实际踩坑后加的护栏，见 tools/verify_sync.py 的说明）。
#: 生产代码永远不该改它。
_repo_root: Path = PROJECT_ROOT


def project_root() -> Path:
    """当前生效的仓库根。每次都读模块变量，便于自检替换。"""
    return _repo_root


def set_project_root(path: Path | str) -> None:
    """仅供自检：把同步操作指向另一个目录。"""
    global _repo_root
    _repo_root = Path(path)


# --------------------------------------------------------------------------
# 监控目录（requirements：data/ uploads/ backups/）
# --------------------------------------------------------------------------

WATCHED_DIRS = ("data", "uploads", "backups")

#: GitPython 需要知道 git 在哪。本项目机器上 git 常常不在 PATH 上，
#: 不显式指定就会 `ImportError: Bad git executable`。
GIT_CANDIDATES = (
    r"C:\Program Files\Git\cmd\git.exe",
    r"C:\Program Files (x86)\Git\cmd\git.exe",
    "/usr/bin/git",
    "/usr/local/bin/git",
)


class SyncError(Exception):
    """同步相关错误的基类。"""


class NotConfiguredError(SyncError):
    """未启用/缺少配置，无法同步。"""


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------


@dataclass
class SyncConfig:
    """从环境变量（.env）读取的配置。"""

    enabled: bool = False
    repo_url: str = ""
    token: str = ""
    interval_minutes: int = 10
    branch: str = "main"
    author_name: str = "cuotiben-sync"
    author_email: str = "cuotiben-sync@localhost"

    @property
    def configured(self) -> bool:
        """是否具备真正推送的条件。"""
        return bool(self.repo_url)

    @property
    def ready(self) -> bool:
        return self.enabled and self.configured


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def load_config() -> SyncConfig:
    """每次调用都重新读环境变量，便于 /sync/status 反映最新配置。"""
    return SyncConfig(
        enabled=_env_bool("GIT_SYNC_ENABLED"),
        repo_url=(os.environ.get("GIT_SYNC_REPO_URL") or "").strip(),
        token=(os.environ.get("GIT_SYNC_TOKEN") or "").strip(),
        interval_minutes=_env_int("GIT_SYNC_INTERVAL_MINUTES", 10),
        branch=(os.environ.get("GIT_SYNC_BRANCH") or "main").strip() or "main",
        author_name=(os.environ.get("GIT_SYNC_AUTHOR_NAME") or "cuotiben-sync").strip(),
        author_email=(
            os.environ.get("GIT_SYNC_AUTHOR_EMAIL") or "cuotiben-sync@localhost"
        ).strip(),
    )


# --------------------------------------------------------------------------
# URL / 凭据处理
# --------------------------------------------------------------------------


def strip_credentials(url: str) -> str:
    """去掉 URL 里的用户名与口令，只留 scheme://host/path。

    用于把 `https://<token>@github.com/u/r.git` 还原成干净地址。
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.scheme or not parts.netloc:
        return url          # 本地路径或 scp 风格（git@host:path），原样返回
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def url_has_credentials(url: str) -> bool:
    """判断 URL 里是否带了用户名/口令。"""
    if not url:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return bool(parts.username or parts.password)


def redact(text: str, token: str) -> str:
    """把 token 从任意文本里抹掉，避免它出现在日志或接口响应里。"""
    if not text:
        return text
    if token and token in text:
        text = text.replace(token, "***")
    return text


# --------------------------------------------------------------------------
# git 可执行文件
# --------------------------------------------------------------------------


def find_git_executable() -> str | None:
    """定位 git。优先环境变量，其次常见安装位置，最后 PATH。"""
    explicit = os.environ.get("GIT_PYTHON_GIT_EXECUTABLE")
    if explicit and Path(explicit).exists():
        return explicit
    found = shutil.which("git")
    if found:
        return found
    for candidate in GIT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def ensure_gitpython() -> str:
    """让 GitPython 找到 git，并把 git 路径返回给调用方。"""
    git_exe = find_git_executable()
    if not git_exe:
        raise SyncError(
            "找不到 git 可执行文件。请把 git 加入 PATH，"
            "或设置环境变量 GIT_PYTHON_GIT_EXECUTABLE 指向 git.exe。"
        )
    os.environ.setdefault("GIT_PYTHON_GIT_EXECUTABLE", git_exe)
    try:
        import git  # noqa: F401
    except ImportError as exc:  # pragma: no cover - 依赖缺失
        raise SyncError(f"GitPython 不可用：{exc}") from exc
    return git_exe


# --------------------------------------------------------------------------
# 变更指纹
# --------------------------------------------------------------------------


def dir_fingerprint(path: Path) -> str:
    """目录内容的指纹：相对路径 + 大小 + mtime。

    只用于**判断"有没有变"**，不读文件内容（图片可能上百兆）。
    """
    if not path.exists():
        return ""
    digest = hashlib.sha256()
    items: list[str] = []
    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue
        try:
            stat = item.stat()
        except OSError:
            continue
        items.append(
            f"{item.relative_to(path).as_posix()}|{stat.st_size}|{int(stat.st_mtime)}"
        )
    digest.update("\n".join(items).encode("utf-8"))
    return digest.hexdigest()


def watched_fingerprints() -> dict[str, str]:
    root = project_root()
    return {name: dir_fingerprint(root / name) for name in WATCHED_DIRS}


# --------------------------------------------------------------------------
# 状态
# --------------------------------------------------------------------------


@dataclass
class SyncState:
    """同步运行时状态，供 GET /sync/status 展示。"""

    running: bool = False
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    last_error_at: str | None = None
    last_error: str = ""
    last_action: str = ""
    last_commit: str = ""
    last_push: str = ""
    sync_count: int = 0
    failure_count: int = 0
    thread_alive: bool = False


_state = SyncState()
_lock = threading.Lock()          # 串行化同步（含"已在同步中"的判定）
_thread: threading.Thread | None = None
_stop_event = threading.Event()
#: 上次同步后各监控目录的指纹，用于判断"这三个目录变没变"
_last_fingerprint: dict[str, str] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_state() -> SyncState:
    with _lock:
        return SyncState(**asdict(_state))


# --------------------------------------------------------------------------
# 仓库准备
# --------------------------------------------------------------------------


def _open_repo(create: bool = False):
    """打开仓库；create=True 时不存在就 git init。"""
    ensure_gitpython()
    import git

    try:
        return git.Repo(project_root())
    except git.InvalidGitRepositoryError:
        if not create:
            raise SyncError(f"{project_root()} 不是 git 仓库") from None
        return git.Repo.init(project_root())
    except git.NoSuchPathError as exc:  # pragma: no cover
        raise SyncError(f"路径不存在：{exc}") from exc


def _ensure_remote(repo, config: SyncConfig) -> str:
    """把 origin 设成配置里的地址（**不带凭据**），返回最终 URL。

    若磁盘上残留了带 token 的 remote，这里会顺手清掉。
    """
    if not config.repo_url:
        return ""
    clean = strip_credentials(config.repo_url)
    origin = None
    for remote in repo.remotes:
        if remote.name == "origin":
            origin = remote
            break
    if origin is None:
        repo.create_remote("origin", clean)
        return clean
    current = next(iter(origin.urls), "") if origin.urls else ""
    # 已有凭据 -> 无条件清掉（即使地址与配置一致也要清）
    if url_has_credentials(current) or current != clean:
        origin.set_url(clean)
    return clean


def sanitize_remote_credentials() -> bool:
    """去掉 origin URL 里的凭据。返回是否做过修改。

    这是**防御性**的：正常情况下我们从没把 token 写进 URL，
    但用户可能手改过 .git/config，或早先的版本写过。启动时清一次。
    """
    try:
        repo = _open_repo()
    except (SyncError, NotConfiguredError):
        return False
    changed = False
    for remote in repo.remotes:
        for url in list(remote.urls):
            if url_has_credentials(url):
                remote.set_url(strip_credentials(url))
                changed = True
    return changed


# --------------------------------------------------------------------------
# 无落盘 push
# --------------------------------------------------------------------------

#: GIT_ASKPASS 指向的解释器参数：按 git 的协议应答用户名/口令。
#: git 第一次问 username、第二次问 password。
_ASKPASS_CODE = (
    "import os,sys;"
    "p=' '.join(sys.argv[1:]).lower();"
    "t=os.environ.get('CUOTIBEN_SYNC_TOKEN','');"
    "u=os.environ.get('CUOTIBEN_SYNC_USER','x-access-token');"
    "sys.stdout.write(t if 'password' in p else u)"
)


def _auth_env(token: str) -> dict[str, str]:
    """构造带临时凭据的环境变量（只在这一条子进程里存在）。"""
    env = dict(os.environ)
    env["CUOTIBEN_SYNC_TOKEN"] = token
    env["CUOTIBEN_SYNC_USER"] = "x-access-token"
    env["GIT_ASKPASS"] = sys.executable
    env["GIT_ASKPASS_CODE"] = _ASKPASS_CODE
    env["GIT_TERMINAL_PROMPT"] = "0"      # 绝不在终端里卡着等输入
    env["GCM_INTERACTIVE"] = "never"
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "credential.helper"
    env["GIT_CONFIG_VALUE_0"] = ""        # 禁用系统凭据助手，避免弹出窗口
    return env


def _run_git_with_token(args: list[str], token: str, timeout: int = 120):
    """执行一条 git 命令，用 GIT_ASKPASS 提供 token，**不写入 .git/config**。

    为什么不用 GitPython 的 `remote.push()`：那种方式下要传凭据就得
    改 remote URL（会落盘），要么写 credential helper（也落盘）。
    直接在子进程里走 askpass，凭据只活在环境变量里。

    askpass 用 `python -c`（不带代码参数）配合环境变量 GIT_ASKPASS_CODE：
    Python 在没有 -c 参数时会从该环境变量读代码并执行，
    因此磁盘上**不会**产生任何临时脚本文件。
    """
    git_exe = ensure_gitpython()
    env = _auth_env(token)
    return subprocess.run(
        [git_exe, *args],
        cwd=str(project_root()),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _git_config_pairs(config: SyncConfig) -> list[str]:
    """提交身份等一次性配置，通过 -c 传入，不写进 .git/config。

    理由同 token：本项目不希望同步功能往磁盘上留配置。
    """
    return [
        "-c", f"user.name={config.author_name}",
        "-c", f"user.email={config.author_email}",
        "-c", "commit.gpgsign=false",
    ]


# --------------------------------------------------------------------------
# 同步主流程
# --------------------------------------------------------------------------


def _staged_changes(repo) -> bool:
    """暂存区里是否有相对 HEAD 的改动（含新增/删除）。"""
    try:
        return bool(repo.git.diff("--cached", "--name-only").strip())
    except Exception:  # noqa: BLE001 - 没有 HEAD 等异常按"有改动"处理
        return True


def _head_sha(repo) -> str:
    try:
        return repo.head.commit.hexsha
    except Exception:  # noqa: BLE001
        return ""


def _has_upstream_commits(repo, branch: str) -> bool:
    """本地分支是否有远端没有的提交。"""
    try:
        text = repo.git.rev_list("--count", f"origin/{branch}..{branch}")
        return int(text.strip() or "0") > 0
    except Exception:  # noqa: BLE001 - 远端分支还不存在
        return bool(_head_sha(repo))


def sync_now(*, reason: str = "manual", config: SyncConfig | None = None) -> dict:
    """执行一次同步：add -A → 有改动则 commit → push。

    返回本次结果摘要。异常在内部转成状态记录并抛出 SyncError，
    调用方（router）决定 HTTP 状态码。
    """
    cfg = config or load_config()

    if not cfg.enabled:
        raise NotConfiguredError("同步未启用（GIT_SYNC_ENABLED 未开启）")
    if not cfg.configured:
        raise NotConfiguredError("未配置 GIT_SYNC_REPO_URL")

    # 串行化：已经在同步就直接返回，不排队（避免堆积）
    if not _lock.acquire(blocking=False):
        return {"status": "busy", "message": "已有同步正在进行中"}

    try:
        _state.running = True
        _state.last_attempt_at = _now()
        repo = _open_repo(create=True)
        remote_url = _ensure_remote(repo, cfg)

        # 1) 暂存全部改动。被 .gitignore 忽略的 data/uploads/backups
        #    不会被加进来 —— 这是刻意的，见模块文档第 1 条。
        repo.git.add("-A")
        has_changes = _staged_changes(repo)

        commit_sha = ""
        action = "nothing-to-commit"

        if has_changes:
            # 用 -c 传身份，避免写 .git/config（首次使用可能没有全局身份）
            message = (
                f"chore(sync): 自动同步 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            args = [*_git_config_pairs(cfg), "commit", "-m", message]
            result = _run_git_with_token(args, cfg.token, timeout=120)
            if result.returncode != 0:
                combined = (result.stdout or "") + (result.stderr or "")
                # 没有内容可提交不算失败（可能与上一步的判定有竞态）
                if "nothing to commit" not in combined.lower():
                    raise SyncError(
                        "commit 失败：" + redact(combined.strip()[:400], cfg.token)
                    )
            else:
                action = "committed"
                commit_sha = repo.head.commit.hexsha
                _state.last_commit = commit_sha

        # 2) push。即使本次没有新提交也要 push ——
        #    上次可能提交成功但推送失败，不补推就会一直落后。
        need_push = has_changes or _has_upstream_commits(repo, cfg.branch)
        push_out = ""
        if need_push and remote_url:
            push_args = [
                *_git_config_pairs(cfg),
                "push", "origin", f"HEAD:refs/heads/{cfg.branch}",
            ]
            result = _run_git_with_token(push_args, cfg.token, timeout=180)
            combined = ((result.stdout or "") + (result.stderr or "")).strip()
            push_out = redact(combined, cfg.token)
            if result.returncode != 0:
                raise SyncError("push 失败：" + push_out[:400])
            action = "pushed" if has_changes else "pushed-existing"
            _state.last_push = _now()

        _last_fingerprint.clear()
        _last_fingerprint.update(watched_fingerprints())

        _state.last_action = action
        _state.last_success_at = _now()
        _state.last_error = ""
        _state.sync_count += 1
        return {
            "status": "ok",
            "action": action,
            "commit": commit_sha,
            "pushed": bool(need_push and remote_url),
            "message": push_out[:300] if push_out else "无改动，已是最新",
            "reason": reason,
        }

    except SyncError as exc:
        _state.last_error = redact(str(exc), cfg.token)
        _state.last_error_at = _now()
        _state.last_action = "failed"
        _state.failure_count += 1
        raise
    except Exception as exc:  # noqa: BLE001 - 兜底：任何异常都要记进状态
        message = redact(f"{type(exc).__name__}: {exc}", cfg.token)
        _state.last_error = message
        _state.last_error_at = _now()
        _state.last_action = "failed"
        _state.failure_count += 1
        raise SyncError(message) from exc
    finally:
        _state.running = False
        _lock.release()


# --------------------------------------------------------------------------
# 后台线程
# --------------------------------------------------------------------------


def _repo_is_dirty() -> bool:
    """工作区是否有未提交改动（只看 git 跟踪的东西）。"""
    try:
        repo = _open_repo()
    except (SyncError, NotConfiguredError):
        return False
    try:
        return repo.is_dirty(untracked_files=True)
    except Exception:  # noqa: BLE001
        return False


def _loop(interval_seconds: float) -> None:
    """后台轮询：每 interval 检测一次，有改动就同步。"""
    while not _stop_event.wait(interval_seconds):
        try:
            cfg = load_config()
            if not cfg.ready:
                continue
            current = watched_fingerprints()
            changed = {
                name: (current.get(name) != _last_fingerprint.get(name))
                for name in WATCHED_DIRS
            }
            # 监控目录有变化，或代码/文档有未提交改动，都尝试同步
            if any(changed.values()) or _repo_is_dirty():
                sync_now(reason="interval")
        except SyncError:
            # 失败已记进状态，线程继续跑（下次重试），不要因一次失败就退出
            pass
        except Exception as exc:  # noqa: BLE001 - 线程不能死
            _state.last_error = redact(
                f"轮询异常：{exc}", os.environ.get("GIT_SYNC_TOKEN", "")
            )
            _state.last_error_at = _now()
            _state.last_action = "failed"


def start_background_sync(*, run_first: bool = True) -> dict:
    """启动后台同步线程（幂等）。

    在 lifespan 里调用。run_first=True 时**立即**做一次同步
    （"启动时自动初始化并首次同步"），放在线程里跑，避免拖慢应用启动。
    """
    global _thread

    cfg = load_config()
    if not cfg.ready:
        return {
            "started": False,
            "reason": "未启用或未配置" if not cfg.enabled else "缺少 GIT_SYNC_REPO_URL",
        }
    if _thread is not None and _thread.is_alive():
        return {"started": False, "reason": "后台线程已在运行"}

    # 启动前先做一次安全检查：把可能存在的凭据从 .git/config 里清掉
    if sanitize_remote_credentials():
        print("[sync] 已从 .git/config 的 remote URL 中移除凭据")

    _stop_event.clear()
    interval = max(1, cfg.interval_minutes) * 60

    def _bootstrap() -> None:
        """初始化仓库 + 首次同步。"""
        try:
            repo = _open_repo(create=True)
            _ensure_remote(repo, cfg)
        except SyncError as exc:
            _state.last_error = redact(str(exc), cfg.token)
            _state.last_error_at = _now()
            _state.last_action = "failed"
            return
        _last_fingerprint.update(watched_fingerprints())
        if run_first:
            try:
                result = sync_now(reason="startup", config=cfg)
                print(f"[sync] 首次同步：{result.get('action')}")
            except SyncError as exc:
                print(f"[sync] 首次同步失败：{exc}")
        _loop(interval)

    _thread = threading.Thread(target=_bootstrap, name="cuotiben-sync", daemon=True)
    _thread.start()
    _state.thread_alive = True
    print(
        f"[sync] 后台同步已启动：每 {cfg.interval_minutes} 分钟检查一次，"
        f"分支 {cfg.branch}"
    )
    return {
        "started": True,
        "interval_minutes": cfg.interval_minutes,
        "branch": cfg.branch,
        "repo_url": strip_credentials(cfg.repo_url),
    }


def stop_background_sync() -> None:
    """停止后台线程（lifespan 关闭时调用）。"""
    _stop_event.set()


# --------------------------------------------------------------------------
# 状态汇总
# --------------------------------------------------------------------------

#: 每个监控目录用一个**代表性文件路径**判断是否被忽略。
#: 不能拿目录本身探测：`.gitignore` 里写的是 `data/*.db`、`uploads/*`、
#: `backups/*`，而 `data/.gitkeep` 是**有意入库**的占位文件，
#: 所以 `data/` 作为目录并不被整体忽略。必须问具体的文件。
_GITIGNORE_PROBES = {
    "data": "data/cuotiben.db",
    "uploads": "uploads/probe.jpg",
    "backups": "backups/probe/backup.db",
}


def gitignore_status() -> dict[str, bool]:
    """三个监控目录里的实际数据文件是否被 .gitignore 忽略。

    状态里必须如实说明 —— 否则用户会以为"数据也同步上去了"。
    """
    try:
        repo = _open_repo()
    except (SyncError, NotConfiguredError):
        return {name: False for name in WATCHED_DIRS}
    result: dict[str, bool] = {}
    for name in WATCHED_DIRS:
        try:
            repo.git.check_ignore(_GITIGNORE_PROBES[name])
            result[name] = True
        except Exception:  # noqa: BLE001 - check_ignore 未命中会非零退出
            result[name] = False
    return result


def status_report() -> dict:
    """GET /sync/status 的内容。"""
    cfg = load_config()
    with _lock:
        state = SyncState(**asdict(_state))
    root = project_root()
    current = watched_fingerprints()
    watched = {
        name: {
            "path": str(root / name),
            "exists": (root / name).exists(),
            "fingerprint": current.get(name, ""),
            "changed_since_last_sync": (
                current.get(name, "") != _last_fingerprint.get(name)
            ),
        }
        for name in WATCHED_DIRS
    }
    return {
        "enabled": cfg.enabled,
        "configured": cfg.configured,
        "ready": cfg.ready,
        "repo_url": strip_credentials(cfg.repo_url),
        "token_present": bool(cfg.token),      # 只报有没有，绝不回显内容
        "interval_minutes": cfg.interval_minutes,
        "branch": cfg.branch,
        "thread_alive": bool(_thread and _thread.is_alive()),
        "dirty": _repo_is_dirty(),
        "watched": watched,
        "watched_dirs_gitignored": gitignore_status(),
        "note": (
            "data/ uploads/ backups/ 被 .gitignore 忽略，只用于触发同步检查，"
            "不会提交到仓库（个人数据不入库）；实际同步的是 git 跟踪的代码与文档。"
        ),
        "state": asdict(state),
    }


def reset_state_for_tests() -> None:
    """仅供自检：清空运行时状态与指纹（不碰仓库）。"""
    global _thread
    for key, value in asdict(SyncState()).items():
        setattr(_state, key, value)
    _last_fingerprint.clear()
    _thread = None
    _stop_event.clear()


__all__ = [
    "WATCHED_DIRS",
    "NotConfiguredError",
    "SyncConfig",
    "SyncError",
    "ensure_gitpython",
    "find_git_executable",
    "get_state",
    "gitignore_status",
    "load_config",
    "project_root",
    "redact",
    "reset_state_for_tests",
    "sanitize_remote_credentials",
    "set_project_root",
    "start_background_sync",
    "status_report",
    "stop_background_sync",
    "strip_credentials",
    "sync_now",
    "url_has_credentials",
    "watched_fingerprints",
]
