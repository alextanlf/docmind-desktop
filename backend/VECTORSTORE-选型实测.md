# DocMind 向量库选型：chromadb 现状与替代方案实测

> 2026-10-06 实测。所有数字来自本机运行，非估算。

## 一、耦合面：只有 1 个文件

`grep -rn "chromadb" app/ tests/` 全仓命中点：

| 位置 | 内容 |
|---|---|
| `app/storage/vectorstore.py:7-8` | `import chromadb` / `from chromadb.errors import NotFoundError` |
| `app/storage/vectorstore.py:37` | `chromadb.PersistentClient(path=...)` |
| `app/storage/vectorstore.py:77,102,159` | `get_or_create_collection` / `delete_collection` |
| `app/storage/vectorstore.py:95,103,115,148,162` | `except NotFoundError` |
| `app/storage/vectorstore.py:170` | `metadata={"hnsw:space": "cosine"}` |

**其余 0 处。** 其他 7 个测试文件（`test_retrieval.py`、`test_memory_retriever.py`、
`test_service.py`、`test_repositories.py`、`test_document_recovery.py`、
`test_documents.py`、`test_refresher.py`）都只调 `PersistentVectorStore` 的方法，
不 import chromadb 本身。`app/core/retrieval.py`、`app/memory/{retriever,indexer}.py`
只持有实例引用。

**换库要改的接口面**：`PersistentVectorStore` 共 6 个方法
（`upsert` / `query` / `delete` / `delete_collection` /
`upsert_memory` / `query_memory` / `delete_memory`），外部调用点只用到
`query`（retrieval.py:120,233,239）、`query_memory`（memory/retriever.py:39）、
`upsert_memory`（memory/indexer.py:52）、`delete_memory`（memory/indexer.py:55,92）。

## 二、数据规模：远未触及需要 ANN 的阈值

实测本机 `~/Library/Application Support/docmind-desktop/`：

- `vectorstore/chroma.sqlite3` = **184K**，`segments=0`，`embeddings=0`
- `database/docmind.sqlite3` 的 `documents` 表 = **0 行**

按记忆笔记：平均分块 2848 字符。设单文档 20 块、1000 篇文档 = 2 万条向量。

## 三、体积构成（`du -sh` 实测 site-packages）

| 包 | 体积 | 裁剪判断 |
|---|---|---|
| `chromadb` 本体 | 3.3M | — |
| `chromadb_rust_bindings` | 50M | 换库后可删 |
| `kubernetes` | 44M | 换库后可删。`chromadb-1.5.9.dist-info/METADATA:27` 是无条件 `Requires-Dist: kubernetes>=28.1.0`，不在任何 `Provides-Extra` 下，pip 必装 |
| `grpc` | 40M | 换库后可删。同上 `METADATA:24` |
| **合计可删** | **134M** | |
| `numpy` 24M | 保留（换库后自己要用） | |
| `tokenizers` 10M | 保留 | |
| `bcrypt` 2.1M | 换库后可删（chromadb 硬依赖） | |
| `opentelemetry*` ~2.3M | 换库后可删（fastapi/starlette 也有，非 chromadb 独有） | |

注意：只删 kubernetes+grpc 而保留 chromadb = 省 84M；
**彻底换库 = 省 134M**（但会失去 Rust 版 HNSW，见下）。

## 四、性能实测：暴力扫描完全够用

`numpy` 2.5.2，1024 维 float32，Apple Silicon，top-3：

| 向量数 | 纯矩阵乘 + argpartition |
|---|---|
| 1,000 | 0.03 ms |
| 10,000 | 0.62 ms |
| 50,000 | 3.12 ms |

**线性关系，斜率 0.062 µs/条。** 10 万条 = 6.2 ms。

存储方案对比（N=10,000）：

| 方案 | 查询 | 说明 |
|---|---|---|
| 内存缓存矩阵 + `M @ q` | **0.61 ms** | 冷加载 14 ms（一次性） |
| SQLite 逐行 `frombuffer` | 20.8 ms | Python 层循环，比矩阵乘慢 34× |

**结论：性能瓶颈不是"有没有 HNSW"，而是"有没有把向量批量反序列化成矩阵"。**
前者 0.6ms，后者 20.8ms。HNSW 能省的是前者那 0.6ms 里的一部分，量级上无关紧要。

## 五、sqlite-vec 评估

- 需编译 C 扩展或下载预编译 wheel（沙箱内 `uv pip install` 可能失败，需先验证）
- `sqlite3` lib 版本 = **3.53.1**，远高于 sqlite-vec 要求的 3.41+
- 优势：单文件、事务性、与业务库 `docmind.sqlite3` 可合并
- 劣势：仍是 1024 维暴力扫描，扩展点少

## 六、结论

**换库可行且值得**，但要注意代价：
- 失去 Rust 版 HNSW 的增量索引能力（当前规模不需要）
- 换库必须**重建嵌入索引**（记忆：fp32 vs int8 cos≈0.987，不能混用）
- 需保留 `vectorstore` 抽象层（`app/storage/` 目录已具备此结构）

**最低风险路径**：
1. 先只删 `kubernetes` + `grpc`（84M），不换库，验证后端测试全绿
2. 再评估是否彻底换库
