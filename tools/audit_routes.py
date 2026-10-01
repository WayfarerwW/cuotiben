"""接口命名一致性审计。

检查 docs/requirements.md 第 4 节列出的所有路径：
  1. 资源名是否统一用复数
  2. 路径参数命名是否一致（{id} vs {question_id} ...）
  3. 同一资源是否出现单复数混用
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

doc = Path("docs/requirements.md").read_text(encoding="utf-8")

# 抓取第 4 节区间
start = doc.index("4. 接口清单")
end = doc.index("5. 关键逻辑")
section = doc[start:end]

rows = []
for line in section.splitlines():
    parts = [p for p in line.split("\t") if p.strip()]
    if len(parts) == 3 and parts[0].strip() in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        rows.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))

print("=" * 78)
print("第 4 节接口清单")
print("=" * 78)
for m, p, d in rows:
    print(f"  {m:<7} {p:<34} {d}")

print()
print("=" * 78)
print("审计结果")
print("=" * 78)

# 1) 资源名复数检查
# 真正的"资源名"单数形态。注意 review / upload / export 是**动作/功能命名空间**
# （/review/today、/upload/image、/export/pdf），不是资源集合，不适用复数规则。
SINGULARS = {"question", "folder", "tag", "note", "setting"}
problems = []

for m, p, _ in rows:
    segments = [s for s in p.strip("/").split("/") if s and not s.startswith("{")]
    for seg in segments:
        if seg in SINGULARS:
            problems.append(f"资源名单数: {m} {p}（'{seg}' 应为复数）")

# 2) 路径参数命名统计
params = defaultdict(set)
for m, p, _ in rows:
    for ph in re.findall(r"\{(\w+)\}", p):
        # 取该路径最后一个静态段作为归属资源
        segments = [s for s in p.strip("/").split("/") if s and not s.startswith("{")]
        owner = segments[0] if segments else "?"
        params[owner].add(ph)

print("\n-- 路径参数命名 --")
for owner, names in sorted(params.items()):
    flag = "" if names == {"id"} else "   <-- 与 {id} 不一致"
    print(f"  {owner:<12} {sorted(names)}{flag}")

mixed = {o: n for o, n in params.items() if n != {"id"}}
if mixed:
    for owner, names in mixed.items():
        problems.append(f"路径参数命名不统一: /{owner} 用了 {sorted(names)}，其余用 {{id}}")

# 3) 单复数混用
by_resource = defaultdict(set)
for m, p, _ in rows:
    first = [s for s in p.strip("/").split("/") if s and not s.startswith("{")]
    if first:
        root = first[0]
        by_resource[root.rstrip("s")].add(root)
for root, forms in sorted(by_resource.items()):
    if len(forms) > 1:
        problems.append(f"同一资源出现多种写法: {sorted(forms)}")

print()
if problems:
    print(f"发现 {len(problems)} 处问题：")
    for x in problems:
        print(f"  - {x}")
else:
    print("未发现命名不一致。")

# 4) 代码里实际注册的路由
print()
print("=" * 78)
print("代码实际注册的路径")
print("=" * 78)
import os
import tempfile  # noqa: E402

tmp = tempfile.mkdtemp()
os.environ["CUOTIBEN_DATABASE_URL"] = f"sqlite:///{Path(tmp).as_posix()}/a.db"
sys.path.insert(0, ".")
from app.main import app  # noqa: E402

code_paths = set()
for p, ops in app.openapi()["paths"].items():
    for op in ops:
        code_paths.add((op.upper(), p))
for m, p in sorted(code_paths, key=lambda x: (x[1], x[0])):
    print(f"  {m:<7} {p}")

doc_paths = {(m, p) for m, p, _ in rows}
print()
print("-- 文档有但代码未实现（未开工的模块，正常）--")
for m, p in sorted(doc_paths - code_paths, key=lambda x: (x[1], x[0])):
    print(f"  {m:<7} {p}")
print()
print("-- 代码有但文档未列（文档漏记）--")
for m, p in sorted(code_paths - doc_paths, key=lambda x: (x[1], x[0])):
    print(f"  {m:<7} {p}")
