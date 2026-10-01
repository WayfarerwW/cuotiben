# PoC 结论：WeasyPrint 中文渲染 + pillow-heif 的 HEIC 读写

> 状态：两项 PoC 均**验证通过**，可据此实现 `app/services/pdf_service.py` 与 `image_service.py`。
> 复验脚本：`python tools/verify_font.py`、`python tools/verify_heic.py`

对应 `docs/requirements.md` 第 6 节"风险提示"的两条，以及 AGENTS.md 六节。

---

## 一、结论速览

| 项 | 结论 |
|---|---|
| WeasyPrint 能否在本机跑起来 | **能**，但必须依赖 MSYS2 提供的 GTK/Pango 原生库 |
| 中文是否会变方框 | **不会**。样张中文、全角标点、数学符号、生僻字、表格、粗体全部正常 |
| PDF 是否内嵌字体 | **是**。字体被子集化内嵌，PDF 换机器也能正常显示/打印 |
| `fonts/` 放哪个字体 | **`NotoSansSC-VF.ttf`（Noto Sans SC，SIL OFL 1.1，16.9 MB）** |
| HEIC 读写 | **支持**。libheif 1.23.4，解码 libde265，编码 x265 |
| 压缩管线是否达标 | **达标**。4032×3024 HEIC → 1080×810 JPEG q75，35 KB（上限 300 KB），等比不变形 |

---

## 二、WeasyPrint 原生依赖（关键坑）

### 2.1 问题

`pip install weasyprint` 只装 Python 包，**不含** Pango / GObject / Cairo 等原生库。
Windows 上直接 `import weasyprint` 会失败：

```
OSError: cannot load library 'libgobject-2.0-0': error 0x7e.
Additionally, ctypes.util.find_library() did not manage to locate a library called 'libgobject-2.0-0'
```

本机排查结果：全盘搜索 `libpango-1.0-0.dll`、`libcairo-2.dll`、`libgdk_pixbuf-2.0-0.dll`
均**不存在**（OneDrive 目录下有一个同名 `libgobject-2.0-0.dll`，但那只是 OneDrive 自己的
组件，缺 Pango/Cairo，不能用于 WeasyPrint）。

### 2.2 解决方案：MSYS2 UCRT64

用 winget 安装 MSYS2，再用 pacman 装 GTK 运行库：

```powershell
winget install --id MSYS2.MSYS2 --silent --accept-package-agreements --accept-source-agreements
```

```bash
# 在 MSYS2 的 bash 里执行（pacman 默认源在国内会超时，先换清华源）
# 见下方"镜像"一节
pacman -S --noconfirm --needed \
  mingw-w64-ucrt-x86_64-pango \
  mingw-w64-ucrt-x86_64-gdk-pixbuf2 \
  mingw-w64-ucrt-x86_64-libffi \
  mingw-w64-ucrt-x86_64-harfbuzz \
  mingw-w64-ucrt-x86_64-fontconfig \
  mingw-w64-ucrt-x86_64-freetype \
  mingw-w64-ucrt-x86_64-glib2
```

装入 `C:\msys64\ucrt64\bin`。选 **ucrt64** 而不是 mingw64：它与 MSVC 构建的
CPython 同为 UCRT 运行时，兼容性最好。

**镜像**：`mirror.msys2.org` 与 `mirror2.archlinux.tw` 在本机超时（下载失败）。
改为清华源后正常（HEAD 请求 0.35s）：

```bash
# /etc/pacman.d/mirrorlist.mingw 首行改为（ucrt64/mingw64/mingw32 共用此文件，
# $repo 会自动替换成仓库名）
Server = https://mirrors.tuna.tsinghua.edu.cn/msys2/mingw/$repo/
```

备选（本机实测同样可达）：`mirrors.ustc.edu.cn`、`mirror.nju.edu.cn`、`mirrors.bfsu.edu.cn`。

### 2.3 DLL 路径的坑（必读）

实测发现一个**危险现象**：MSYS2 装在默认位置 `C:\msys64` 时，即使进程 `PATH`
**完全为空**，WeasyPrint 也能加载到库并正常渲染。但只要把 `C:\msys64\ucrt64`
改名，立刻报 `libgobject-2.0-0` 加载失败。

也就是说"能跑"是靠 Windows DLL 解析在默认安装路径上碰巧成立的，**换安装目录就不成立**。
因此不能依赖偶然：

- `app/weasyprint_bootstrap.py` 的 `ensure_native_libs()` 显式把 GTK bin 目录前置到
  进程 `PATH`，并调用 `os.add_dll_directory()`。
- `run.py` 必须在**最早时机**（任何业务模块 import 之前）调用它，之后再 `import weasyprint`。
- 支持用环境变量覆盖：`CUOTIBEN_GTK_BIN`（直接指定 bin 目录）、`MSYS2_ROOT`（指定安装根）。

### 2.4 另一个坑：MSYS2 的 python.exe 会抢占 PATH

`C:\msys64\ucrt64\bin` 里有 MSYS2 自带的 `python.exe`。若把该目录前置到 PATH，
`python` 会解析到 MSYS2 的 Python（没装本项目依赖），表现为莫名其妙的
`ModuleNotFoundError: No module named 'weasyprint'`。

**规避**：项目一律用绝对路径或虚拟环境里的解释器启动，不要依赖 PATH 里的裸 `python`。

---

## 三、中文字体选型（`fonts/` 放什么）

### 3.1 本机可用中文字体

`C:\Windows\Fonts` 下覆盖中文的字体有 36 个。其中**完整覆盖**探针字符（含
生僻字龘齉靐鱻麤爨蠹饕餮等）的 22 个。

### 3.2 为什么选 Noto Sans SC

| 候选 | 授权 | 能否随仓库分发 | 结论 |
|---|---|---|---|
| **Noto Sans SC** (`NotoSansSC-VF.ttf`) | **SIL OFL 1.1** | **可以** | **采用** |
| Noto Serif SC | SIL OFL 1.1 | 可以 | 备选（衬线，打印更省墨） |
| Microsoft YaHei / SimSun / SimHei | 微软专有 | **不可以** | 仅可引用系统字体，不随仓库分发 |
| 方正/华文等 | 专有 | 不可以 | 不用 |

微软字体虽可免费使用，但**不允许随项目分发**，所以不进 `fonts/`。
Noto Sans SC 是 OFL 1.1，允许自由使用、修改、再分发（含商用），只需保留授权信息。

已随仓库放入 `fonts/NotoSansSC-OFL.txt` 记录授权（OFL 要求随字体分发授权文本）。

### 3.3 可变字体的注意点

`NotoSansSC-VF.ttf` 是**可变字体（variable font）**。其 name 表里
typo family / subfamily 存在不一致（`NotoSansSC-Thin` 之类的记录）。

实测结论：**WeasyPrint/Pango 能正确处理**。生成的 PDF 里嵌入了
`CuotibenCN` 与 `CuotibenCN-Bold` 两个子集，粗细表现正常。

但实现 `pdf_service.py` 时仍需注意：

- 若需要**精确控制字重**，用静态字重文件（如 Noto Sans SC Regular/Bold 两个 ttf）
  比可变字体更可控。
- 当前 PoC 用 `font-weight: bold` 让 Pango 做**合成粗体**，效果可接受（见样张第 5 节）。

### 3.4 缺字：U+2080 下标数字

所有 36 个中文字体**都缺 U+2080（₀）**。即"∫₀¹"这种写法会变方框。

**规避**：PDF 模板里用 HTML 语义标签而不是 Unicode 下标字符：

```html
<!-- 不要 --> ∫₀¹ f(x)dx
<!-- 要   --> ∫<sub>0</sub><sup>1</sup> f(x)dx
```

### 3.5 嵌入方式：必须用 `@font-face` + 绝对 `file:///` 路径

这是 PoC 里踩得最深的坑。实测对比（每种方案独立进程渲染，避免
`FontConfiguration` 进程内缓存污染结果）：

| 方案 | 结果 |
|---|---|
| `@font-face` + 绝对 `file:///` 指向 `fonts/` 内文件 | **嵌入指定字体** ✓ |
| 纯族名 `font-family:"Microsoft YaHei"` | 嵌入系统字体，能用但**依赖本机字体** |
| `@font-face` + 相对 URL | **静默失效**，回退到系统字体 ✗ |

三种"能用"的方案在**本机**都能出中文，区别在于**可移植性**：

- 用 `@font-face` 绝对路径 → PDF 内嵌 Noto Sans SC 子集，**换机器也能正常显示**。
- 用系统族名 → 依赖 fontconfig 解析本机字体；换到没装该字体的机器就可能变方框。

**因此 `pdf_service.py` 必须**：运行时算出 `fonts/NotoSansSC-VF.ttf` 的**绝对路径**，
拼成 `file:///` URL 写进 `@font-face`，并把同一个 `FontConfiguration` 同时传给
渲染和 `write_pdf`。

### 3.6 已被证伪的两个误判

排查过程中出现过两个错误结论，记录以免重蹈：

1. **"fontconfig 看不到 Windows 字体"** —— 错。
   在 MSYS2 的 login shell 里跑 `fc-list` 返回 0 条，一度据此判断 fontconfig 是瞎子。
   实际用 `C:\msys64\ucrt64\bin\fc-list.exe` 并带上该目录的 PATH 后，返回 **464 条**，
   含 43 条中文族名；`fc-match "Microsoft YaHei"` 正确解析到 `msyh.ttc`。
   所以"某族名不生效"是别的原因（见第 3.5 节相对 URL 失效），不是 fontconfig 的问题。

2. **"所有 CSS 方案都落到 SimSun"** —— 错，是测试方法有问题。
   用 `stylesheets=[CSS(string=...)]` 注入时，若写法不当会静默失效，导致
   所有 case 都退回默认字体。改用内联 `<style>` 且**每个方案独立进程**后，
   各方案差异才正确显现。

**教训**：`FontConfiguration` 在进程内会缓存字体映射，多方案对比**必须**每种方案
起一个全新进程，否则后一个 case 会复用前一个的字体。

---

## 四、pillow-heif 的 HEIC 读写

### 4.1 结果

| 项 | 结果 |
|---|---|
| 版本 | pillow-heif 1.8.0 / libheif 1.23.4 / Pillow 12.3.0 |
| 解码器 | libde265 1.1.3（HEVC 解码） |
| 编码器 | x265 4.3+1（HEVC 编码） |
| 写 HEIC | 正常（4032×3024 → 171 KB HEIC） |
| 读 HEIC | 正常（format=HEIF, size=(4032,3024), mode=RGB） |
| 压缩管线 | 4032×3024 → **1080×810**，**35 KB**，JPEG q75 |
| 上限校验 | 35 KB ≤ 300 KB ✓ |
| 等比不变形 | 期望高 810，实际 810 ✓ |
| 显式 `open_heif` 解码 | 正常（不依赖 `register_heif_opener`） |

### 4.2 必须显式依赖 pillow-heif（已确认）

Pillow 12.3.0 **自带不含 HEVC 编解码器**。`PIL.features.check()` 里也没有
`libheif` 这个 feature（写了会 `UserWarning: Unknown feature 'libheif'`）。
真正的能力全部来自 `pillow-heif` 内置的 libheif。

判定能力要用 pillow-heif 自己的 API：

```python
import pillow_heif
info = pillow_heif.libheif_info()      # {'encoders': {...}, 'decoders': {...}}
print(pillow_heif.libheif_version())   # '1.23.4'
```

所以 `requirements.txt` 里的 `pillow-heif` **不能省**（AGENTS.md 4.4 已强调），
且 `image_service.py` 必须 `import pillow_heif; pillow_heif.register_heif_opener()`。

### 4.3 实现建议

- 上传前校验扩展名/魔数，单文件上限 10MB（需求 2.6）。
- `compress_uniform()` 按需求 5.6 节实现（宽度 1080、q75、LANCZOS、等比）。
- 压缩后若 > 300KB，可**逐级降质量**（75 → 65 → 55）重试；仍超标则回退原图，
  **不阻断上传**（需求 2.7 明确要求压缩失败不阻断）。
- 建议真机 HEIC 样张再抽验一次（本次用的是合成图。真实手机照片可能带
  EXIF 方向、10bit、多帧等特性）。若无样张，`verify_heic.py` 会自动 SKIP 并提示。

---

## 五、复验方式

```powershell
# 用项目虚拟环境的解释器，或绝对路径指定，避免被 MSYS2 的 python.exe 抢占
python tools/verify_font.py    # 中文渲染 + 字体嵌入
python tools/verify_heic.py    # HEIC 读写 + 压缩管线
```

产物写入 `poc_out/`（已在 `.gitignore` 中忽略）：

- `poc_cn_sample.pdf` / `poc_cn_sample_p1.png` —— 中文样张，供人工核对无方框
- `sample_we_wrote.heic` / `compressed.jpg` —— HEIC 与压缩结果

---

## 六、对后续实现的影响

1. **`requirements.txt` 保持现状即可**，`pillow-heif`、`weasyprint` 都必需。
2. **`run.py` 第一件事**：`from app.weasyprint_bootstrap import ensure_native_libs` 并调用，
   再 import 业务模块。异常要降级为"PDF 导出不可用"提示，**不能让整个应用起不来**——
   WeasyPrint 装不上不该影响录题、复习等核心功能。
3. **`app/services/pdf_service.py`**：`@font-face` 用 `fonts/NotoSansSC-VF.ttf` 的
   绝对 `file:///` 路径；模板里避免 U+2080 等中文字体缺失字符。
4. **`app/services/image_service.py`**：显式 `register_heif_opener()`；压缩失败回退原图。
5. **README 需补一节环境准备**，说明 MSYS2 这一步（当前 README 只写了
   `pip install -r requirements.txt`，不足以让 PDF 导出跑起来）。
6. **首次部署成本提示**：MSYS2 全量安装约 100+ MB。这是 WeasyPrint 在 Windows 上
   的固有成本。若日后觉得太重，可改用 Chromium 无头渲染（Playwright）
   绕开 GTK，但那样需要改 `requirements.txt` 与 pdf_service，属方案变更，
   需另行确认。
