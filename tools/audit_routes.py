"""接口审计：命名一致性 + 字段级差异。

两部分：
  A. 命名审计（原有）：资源名复数、路径参数命名、单复数混用
  B. 字段级比对（新增）：把 docs/requirements.md 第 4 节里描述的响应字段
     与代码实际注册的 OpenAPI 契约对照，只报两类差异：
       - 文档有字段但实现无  -> 【严重】
       - 实现有字段但文档无  -> 【提示】
     不比对类型和值，只比对字段名是否存在。

字段来源：文档中反引号包裹的形如 `folder_name` 的标识符。
因此写文档时，响应字段名请用反引号标注（本仓库已在 4.2/4.3/4.5 采用该写法）。

退出码：只要存在【严重】差异即返回 1，便于接入检查流程。

运行：python tools/audit_routes.py
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(REPO_ROOT)

# ---------------------------------------------------------------- 解析文档

doc = Path("docs/requirements.md").read_text(encoding="utf-8")
section = doc[doc.index("4. 接口清单"):doc.index("5. 关键逻辑")]

# 4.1 文件夹 / 4.2 题目 ... 用于归属判断
SECTION_HEADER = re.compile(r"^4\.(\d+)\s+(\S+)", re.M)

rows: list[tuple[str, str, str]] = []
for line in section.splitlines():
    parts = [p for p in line.split("\t") if p.strip()]
    if len(parts) == 3 and parts[0].strip() in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        rows.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))

# ---------------------------------------------------------------- 加载代码契约

tmp = tempfile.mkdtemp()
os.environ["CUOTIBEN_DATABASE_URL"] = f"sqlite:///{(Path(tmp) / 'audit.db').as_posix()}"
sys.path.insert(0, str(REPO_ROOT))

from app.main import app  # noqa: E402

spec = app.openapi()
schemas = spec.get("components", {}).get("schemas", {})


def normalize_path(path: str) -> str:
    """把 {folder_id}/{question_id}/{id} 统一成 {} 以便对照。

    同时忽略查询串：文档里 `GET /tags/search?q=` 与代码注册的 `/tags/search`
    是同一个接口。
    """
    path = path.split("?")[0]
    return re.sub(r"\{[^}]*\}", "{}", path)


def response_fields(op: dict) -> list[str]:
    """取该操作 200/201 响应体里的字段名。"""
    for code in ("200", "201"):
        resp = op.get("responses", {}).get(code)
        if not resp:
            continue
        for cval in resp.get("content", {}).values():
            schema = cval.get("schema", {})
            if "$ref" in schema:
                name = schema["$ref"].split("/")[-1]
                return list(schemas.get(name, {}).get("properties", {}).keys())
            if schema.get("type") == "array" and "$ref" in schema.get("items", {}):
                name = schema["items"]["$ref"].split("/")[-1]
                return list(schemas.get(name, {}).get("properties", {}).keys())
            if schema.get("type") == "object":
                return list(schema.get("properties", {}).keys())
    return []


code_ops: dict[tuple[str, str], dict] = {}
for path, ops in spec["paths"].items():
    for method, op in ops.items():
        code_ops[(method.upper(), normalize_path(path))] = op

# ================================================================ A. 命名审计

print("=" * 84)
print("A. 命名审计")
print("=" * 84)

ACTION_ROOTS = {"review", "upload", "export", "sync", "health", "docs",
                "openapi", "redoc"}
RESOURCE_SINGULARS = {"question", "folder", "tag", "note", "setting"}
problems: list[str] = []

for m, p, _ in rows:
    segments = [s for s in p.strip("/").split("/") if s and not s.startswith("{")]
    if segments and segments[0] in ACTION_ROOTS:
        continue
    for seg in segments:
        if seg in RESOURCE_SINGULARS:
            problems.append(f"资源名单数: {m} {p}（'{seg}' 应为复数）")

params: dict[str, set[str]] = defaultdict(set)
for m, p, _ in rows:
    for ph in re.findall(r"\{(\w+)\}", p):
        segments = [s for s in p.strip("/").split("/") if s and not s.startswith("{")]
        owner = segments[0] if segments else "?"
        params[owner].add(ph)
print("\n-- 路径参数命名 --")
for owner, names in sorted(params.items()):
    flag = "" if names == {"id"} else "   <-- 与 {id} 不一致（实现里按资源具体命名，属约定）"
    print(f"  {owner:<12} {sorted(names)}{flag}")

by_resource: dict[str, set[str]] = defaultdict(set)
for m, p, _ in rows:
    first = [s for s in p.strip("/").split("/") if s and not s.startswith("{")]
    if first:
        by_resource[first[0].rstrip("s")].add(first[0])
for root, forms in sorted(by_resource.items()):
    if len(forms) > 1:
        problems.append(f"同一资源出现多种写法: {sorted(forms)}")

print()
if problems:
    print(f"命名问题 {len(problems)} 处：")
    for x in problems:
        print(f"  - {x}")
else:
    print("未发现命名不一致。")

# ================================================================ B. 字段比对

print()
print("=" * 84)
print("B. 字段级比对（文档 vs 代码实际契约）")
print("=" * 84)

# 归属规则（显式约定，避免模糊猜测）：
#
#   响应字段列表以「响应字段」标记行开始，并归属到**最近一个在它之前的 GET 接口行**。
#   例：
#       GET	/questions	列表
#       GET	/questions/{id}	详情
#       ...
#       `GET /questions` 列表项响应字段：
#       `id`、`folder_id`、...
#
#   即：多个接口时可以共用一张接口表，但每个字段列表都要带「响应字段」标记，
#   工具按出现顺序分配给在它之前的最近一个 GET 接口。
#
# 为什么不靠位置模糊推断：小节级汇总会让同节不同接口的字段互相串味
# （4.5 的 /review/today 与 /review/backfill/stats 就吃过这个亏），
# 而误报比漏报更耗时间。
FIELD_TOKEN = re.compile(r"`([a-z][a-z0-9_]*)`")

# 触发一个"响应字段列表"的标记
FIELD_MARKER = re.compile(r"响应字段")

# 文档里用反引号标注、但表示表名/查询参数/动作前缀/示例值的名字，不是响应字段
EXCLUDED_NAMES = {
    "tag", "tag_mode", "keyword", "starred", "mastery",
    "q", "limit", "force", "days", "spread", "scope", "with_answer",
    "include_tags", "question_ids",
    "question_tags", "review_records", "question_images",
    "export", "review", "upload",
    "abc",
}

ENUM_GROUP = re.compile(r"\[([^\]]*`[^\]]*)\]")


def _enum_like_names(lines: list[str]) -> set[str]:
    """找出"取值集合"里的反引号名，避免把枚举值当字段。"""
    found: set[str] = set()
    for group in ENUM_GROUP.findall("\n".join(lines)):
        found |= set(FIELD_TOKEN.findall(group))
    for line in lines:
        if "|" in line:
            continue
        for seg in re.split(r"[、,，]", line):
            names = FIELD_TOKEN.findall(seg)
            if len(names) >= 2 and "/" in seg:
                found |= set(names)
    return found


def _split_field_segments(lines: list[str]) -> list[list[str]]:
    """把行序列按「响应字段」标记切成若干字段列表段。

    每段从标记行开始，到空行为止（标记行本身与其后的字段续行都算在内）。
    """
    segments: list[list[str]] = []
    i = 0
    while i < len(lines):
        if FIELD_MARKER.search(lines[i]):
            seg: list[str] = []
            j = i
            while j < len(lines) and lines[j].strip():
                seg.append(lines[j])
                j += 1
            segments.append(seg)
            i = j
        else:
            i += 1
    return segments


def _tokens_from_segment(seg: list[str]) -> tuple[set[str], set[str]]:
    enum_values = _enum_like_names(seg)
    tokens: set[str] = set()
    for line in seg:
        if "|" in line:
            continue
        tokens |= set(FIELD_TOKEN.findall(line))
    tokens -= enum_values
    unknown = {t for t in tokens if t in EXCLUDED_NAMES}
    return tokens - unknown, unknown


# 归属策略：字段列表带「响应字段」标记，归属到**在它之前的最近一个 GET 接口**。
# 详见本段上方 FIELD_MARKER 处的说明。
blocks: list[tuple[str, str, list[str]]] = []


ENDPOINT_LINE = re.compile(r"^(GET|POST|PUT|DELETE|PATCH)\t")


def split_into_blocks(lines_list: list[str]) -> None:
    cur: tuple[str, str] | None = None
    buf: list[str] = []
    for line in lines_list:
        # 只认"方法名后紧跟制表符"的接口表行。
        # 不能只用 split("\t") 判断：字段标记行 `GET /questions` 列表项响应字段：
        # 也会被切成 3 段，从而被误判成新接口行，把字段段整段吃掉。
        m = ENDPOINT_LINE.match(line)
        if SECTION_HEADER.match(line):
            if cur is not None:
                blocks.append((cur[0], cur[1], buf))
            cur, buf = None, []
        elif m:
            if cur is not None:
                blocks.append((cur[0], cur[1], buf))
            cur = (m.group(1), line.split("\t")[1].strip())
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        blocks.append((cur[0], cur[1], buf))


split_into_blocks(section.splitlines())

doc_fields: dict[tuple[str, str], set[str]] = {}
unverifiable: set[str] = set()

# 逐块处理，维护"上一个块"（不限方法）。
# 一个块 = 一个接口行 + 紧随其后的说明；因此块内出现的字段列表段就属于该块。
# 只有当该块确实是 GET、且该路径真的在代码里注册了 GET 时，才做字段比对 ——
# 否则说明这段文字写在了错误的接口下面，直接跳过而不是错配。
skipped: list[str] = []
for method, path, body_lines in blocks:
    segs = _split_field_segments(body_lines)
    if not segs:
        continue
    endpoint = (method, normalize_path(path))
    if method != "GET":
        skipped.append(f"{method} {path}")
        continue
    if endpoint not in code_ops:
        skipped.append(f"{method} {path}（代码中无此 GET）")
        continue
    for seg in segs:
        tokens, unknown = _tokens_from_segment(seg)
        unverifiable |= unknown
        if tokens:
            doc_fields[endpoint] = doc_fields.get(endpoint, set()) | tokens

only_doc: list[tuple[str, str, list[str]]] = []
only_code: list[tuple[str, str, list[str]]] = []

for key, impl_fields in sorted(code_ops.items(), key=lambda kv: (kv[0][1], kv[0][0])):
    method, path = key
    if method != "GET":
        continue
    impl = response_fields(code_ops[key])
    if not impl:
        continue
    documented = doc_fields.get(key, set())
    if not documented:
        only_code.append((method, path, impl))
        continue
    missing = sorted(documented - set(impl))
    extra = sorted(set(impl) - documented)
    if missing:
        only_doc.append((method, path, missing))
    if extra:
        only_code.append((method, path, extra))

print()
print("-- 【严重】文档有字段但实现无 --")
if only_doc:
    for method, path, fields in only_doc:
        print(f"  {method} {path}")
        for f in fields:
            print(f"      - {f}")
else:
    print("  无")

print()
print("-- 【提示】实现有字段但文档无 --")
if only_code:
    for method, path, fields in only_code:
        print(f"  {method} {path}")
        for f in fields:
            print(f"      + {f}")
else:
    print("  无")

if unverifiable:
    print()
    print("-- 【不计入】文档中被反引号标注、但不属于响应字段的名称 --")
    print("   （表名/查询参数/枚举值等，人工确认即可）")
    for name in sorted(unverifiable):
        print(f"      · {name}")

if skipped:
    print()
    print("-- 【未参与比对】字段列表写在非 GET 接口下的段落 --")
    for item in skipped:
        print(f"      ! {item}")

print()
print("=" * 84)
print(f"严重 {len(only_doc)} 项，提示 {len(only_code)} 项，命名问题 {len(problems)} 项")
print("说明：只比对字段名是否存在，不比对类型与值。")
print("      字段来源 = 小节内行内反引号标识符，已排除表格行、枚举值、参数名与表名；")
print("      因此文档里描述响应字段时请用反引号标注（如 `folder_name`）。")
print("=" * 84)

sys.exit(1 if (only_doc or problems) else 0)
