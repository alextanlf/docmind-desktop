# docmind-tex

让 DocMind 直接导入 LaTeX 源文件——不需要先编译成 PDF。

- 支持的扩展名：`.tex`、`.latex`
- 依赖：**无**。转换器只用 Python 标准库（DocMind 打包后的 runtime 里没有安装器，
  带第三方依赖的插件在用户机器上根本装不上；这个插件刻意保持零依赖）
- 转换逻辑全部在 `docmind_tex/latex.py`，**该模块不 import DocMind 任何东西**（有断言锁住）——
  所以它能在不读宿主代码的情况下读懂，也能在不经过宿主的条件下单测。包的
  `__init__.py` 才认识 DocMind，它只负责把转换结果包装成宿主期望的形状。

## 安装

把仓库放进 DocMind 的插件目录即可，**不需要构建、不需要 pip**：

```bash
git clone https://github.com/alextanlf/docmind-desktop "$TMP"
ln -s "$TMP/plugins/docmind-tex" "$PLUGINS/docmind-tex"
rm -rf "$TMP"
```

或者从本地工作副本直接软链：

```bash
ln -s ~/code/docmind-desktop/plugins/docmind-tex "$PLUGINS/docmind-tex"
```

`$PLUGINS` 是插件目录的实际路径：打开 **设置 → 插件**，页面底部写着它，可以直接复制。
（它由数据目录派生、跟随平台和应用名，所以不要照抄文档里某台机器上的路径。）

软链的目录改完源码重启 DocMind 就生效——没有构建步骤。

## 它怎么转换

文档结构决定检索质量，所以下面这些不是「尽量保留」，而是**有断言锁住的**：

| LaTeX | 转成 |
|---|---|
| `\part` / `\chapter` / `\section` / `\subsection` / `\subsubsection` / `\paragraph` | `#` ~ `######`（固定映射，不按文档重排层级） |
| `tabular` + booktabs 规则 | GFM 表格 |
| `\caption` | 表格/图上方一行粗体文本 |
| `itemize` / `enumerate` / `description`（含嵌套） | markdown 列表 |
| `lstlisting` / `verbatim` / `minted` | 围栏代码块（保留语言） |
| `equation` / `align*` / `\[...\]` | `$$` 块，内容**逐字保留** |
| `$...$` / `\(...\)` | 原样保留，包括 `\frac`、`\mathcal` |
| `\ref` / `\cite` | `[key]` |

**故意丢掉的**：`tikzpicture` 等绘图（图本身带不过来，留着就是噪声）、字号颜色命令
（`\large`、`\color`）、版式命令（`\vspace`、`\clearpage`）、`\label`。
交叉引用**不做解析**——`\ref{tab:main}` 变成 `[tab:main]`，保留可追溯性，
而不是去编一个源文件里没有的编号。

`\multicolumn{3}{c}{预测}` 只保留「预测」两个字，跨列信息丢掉；表头因此读作一个值
而不是三个。

## 标题

优先 `\title{}`；没有就用文档的第一个标题（`\section` 之类）。一份没写 `\title`
的实验报告，这个取法比暂存文件名（一个裸 UUID）有用得多。

## 已知限制

- `\input` / `\include` 不展开，多文件项目只能导入单个 `.tex`
- 自定义宏不展开，未知命令保留其参数、丢掉命令本身
- 参考文献列表（`thebibliography`）不保留

## 测试

```bash
cd backend && env -u PYTHONPATH .venv/bin/python -m pytest tests/plugins -q
```

`tests/plugins/test_tex_plugin.py` 里有两类断言：一类走**真实加载器**把这个目录装进
一个真实 host（证明插件层的缝是通的），一类逐构造验证转换结果（因为转换质量本身
才是这个插件的功能）。
