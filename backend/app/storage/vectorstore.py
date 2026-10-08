"""向量索引存储：sqlite-vec 后端。

设计约束（都是实测得来的，不是偏好）：

1. **为什么不用 ANN 索引库（hnswlib/faiss）**：1024 维下暴力扫描的实测斜率是
   0.062 µs/条，2 万条约 1.2 ms。sqlite-vec 5 万条 top-3 = 29 ms，桌面端规模
   完全够用，换来的是零传递依赖 + 单文件事务性。

2. **距离度量必须是 cosine**：sqlite-vec 的 vec0 默认 L2，必须显式写
   `distance_metric=cosine`，否则与 chromadb 的 `hnsw:space=cosine` 语义不一致，
   检索结果会变。cosine 下 `1 - distance` 恰好等于余弦相似度，与旧实现同口径，
   相似度阈值（如 memory/retriever.py 的 0.65）无需重调。

3. **元数据不塞进向量表**：vec0 的 metadata 列不支持任意键值，且分区键会改变
   ANN 遍历顺序。改为「向量表只管 id + embedding，正文与 metadata 走普通表」。
   代价是查询后要回表，但 top_k ≤ 5，回表成本可忽略。

4. **collection 用独立表名而不是共享表的 partition key**：`delete_collection`
   需要能整体删掉某个仓库的全部向量（chromadb 的 collection 级删除）。
   共享表 + partition key 做不到干净删除，只能按分区扫行删除，得不偿失。
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any

import sqlite_vec

from app.api.errors import DomainError
from app.config import VectorStoreSettings

# 与旧实现保持同一份白名单：sqlite 侧只落这些键，避免把任意 dict 灌进库。
_METADATA_KEYS = {
    "doc_id",
    "doc_title",
    "section_path",
    "source_url",
    "chunk_index",
    "source_type",
    "page_number",
    "repository_id",
    "source_id",
    "kind",
}

# 表名后缀。与 chromadb 的 collection 概念一一对应：
#   向量表 {_collection}_vec    存 id + embedding
#   元数据表 {_collection}_meta 存 id + text + metadata(JSON)
_VEC_SUFFIX = "_vec"
_META_SUFFIX = "_meta"

# vec0 表里冗余存放的过滤列。
#
# 🔴 为什么过滤必须下推到 SQL，而不是「取回一批再在 Python 里筛」：
#   后者要求先按相似度取 top-N 再筛，若匹配项恰好排在 N 名之外（低选择性
#   过滤时极常见），结果就是**静默漏召回** —— 实测 100 条里 2 条匹配
#   doc_id='d1'、放大窗口 30 时返回 0 条。概览块注入（retrieval.py:235
#   按 section_path 取 OVERVIEW）会因此悄悄失效，无任何报错。
#
#   vec0 支持在 MATCH 查询里带任意 metadata 列的等值/IN 条件，由扩展自己
#   在 ANN 遍历时过滤，既不漏召回也不用放大窗口。
#   实测：section_path 作为 partition key 时 100 条中 2 条匹配可正确取回。
#
# 列出的是全部 _METADATA_KEYS —— 列必须预先建好，运行时才能按任意键过滤。
_FILTER_COLUMNS = tuple(sorted(_METADATA_KEYS))

# 集合名合法性。
#
# 🔴 表名必须**始终加双引号**（见 _qi）。collection_name 的既有行为是把非
# [A-Za-z0-9_-] 替换成下划线，而 repository_id 在生产里是 UUID
# （形如 0fba5e94-aaf2-46e5-...），于是表名里必然出现连字符 —— 未加引号时
# SQLite 把它解析成减法，实测报 `near "-": syntax error`。
#
# 所以这里只负责「挡掉 SQL 注入面」：引号、反斜杠、空白、分号一律拒绝。
# 长度上限取 128：表名进 sqlite_master，长度本身不敏感，但要有确定边界。
_COLLECTION_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _qi(identifier: str) -> str:
    """把表名/索引名包成 SQL 标识符。

    双引号内的标识符不做关键字解析，连字符、保留字都安全。外层已由
    _COLLECTION_RE 挡掉引号与反斜杠，不存在注入面。
    """
    return f'"{identifier}"'

# 带 where 时的取数上限。过滤已下推到 SQL，k 就是真实要的行数，
# 不再需要放大候选集 —— 放大恰恰是漏召回的根源。
_MAX_FETCH = 512

# SQLite 单条语句的绑定变量上限是 32766（SQLITE_MAX_VARIABLE_NUMBER）。
# `WHERE id IN (?,?,...,?)` 展开后会撞上这个上限，实测 5 万条批量写入时
# 报 `too many SQL variables`。取 1000 留足余量。
_SQL_VARIABLE_BATCH = 1000


@dataclass(frozen=True)
class VectorHit:
    id: str
    text: str
    metadata: dict[str, Any]
    similarity: float


class PersistentVectorStore:
    """按仓库分表的 sqlite-vec 向量存储。

    线程安全：FastAPI 的检索在事件循环里跑、索引写入可能在 worker 线程，
    sqlite3 连接默认不能跨线程共享。这里用一把锁把连接串行化 —— 桌面端并发
    量极低（单用户），串行化的代价远小于引入连接池的复杂度。
    """

    def __init__(self, settings: VectorStoreSettings) -> None:
        self.settings = settings
        self.dimension = settings.dimension
        self.settings.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(settings.directory / "vectors.db"), check_same_thread=False
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._migrate()
        self._retire_legacy_chroma()

    def _retire_legacy_chroma(self) -> None:
        """把旧 chromadb 的库文件改名为 .legacy，不再读它。

        chromadb 与 sqlite-vec 是两套完全不同的存储格式，向量无法搬迁
        （本项目的迁移只在索引为空时可行，见交付说明）。所以旧文件不删 ——
        删掉用户数据不该由代码在启动时顺手做掉 —— 只改名让用户知道它已废弃，
        腾出空间由用户自己决定。

        幂等：已改过名的文件不会重复处理。
        """
        legacy = self.settings.directory / "chroma.sqlite3"
        if not legacy.exists():
            return
        retired = legacy.with_suffix(".sqlite3.legacy")
        try:
            legacy.rename(retired)
        except OSError:
            # 改名失败（文件被占用、权限不足）不影响新库工作，静默跳过。
            pass

    # ── schema ────────────────────────────────────────────────────────────

    def _migrate(self) -> None:
        """建元数据总表。

        向量表不能预先建 —— 它的维度写在表定义里，而集合是按需创建的。
        版本号记在这里，将来改结构时能识别旧库。
        """
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vectorstore_meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO vectorstore_meta(key, value) VALUES('schema_version', '1')"
        )
        self._conn.commit()

    def _create_collection(self, collection: str, dimension: int) -> None:
        vec_table = _qi(f"{collection}{_VEC_SUFFIX}")
        meta_table = _qi(f"{collection}{_META_SUFFIX}")
        # 过滤列必须与 id/embedding 同表，MATCH 查询才能在 ANN 遍历时带上条件。
        # 值统一存字符串：sqlite-vec 的 metadata 列只认 text/int/float/bool，
        # 而 _metadata() 允许 int（如 page_number / chunk_index）；字符串化后
        # 过滤时按同一规则序列化查询值即可，语义一致。
        #
        # 🔴 vec0 的构造函数**不接受带引号的列名**（实测报
        # `vec0 constructor error: Could not parse '"doc_id" text'`），
        # 与普通 CREATE TABLE 的规则不同。这里能裸写是因为列名全部来自
        # 模块常量 _FILTER_COLUMNS（只含小写字母与下划线），不是外部输入。
        filter_cols = "".join(f"{column} text," for column in _FILTER_COLUMNS)
        self._conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {vec_table} "
            f"USING vec0(id text primary key, embedding float[{dimension}] "
            f"distance_metric=cosine, {filter_cols})"
        )
        self._conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {meta_table} (
                id       TEXT PRIMARY KEY,
                text     TEXT NOT NULL DEFAULT '',
                metadata TEXT NOT NULL DEFAULT '{{}}'
            )
            """
        )
        self._conn.commit()

    def _stored_dimension(self, collection: str) -> int | None:
        """读回集合实际使用的维度；集合不存在时返回 None。

        sqlite-vec 的 vec0 把维度固化在表定义里（运行期无法 ALTER），所以维度
        是「集合创建那一刻」决定的，不是全局常量。查询时按实际维度校验，
        避免换嵌入模型后拿错长度的向量去撞表。
        """
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (f"{collection}{_VEC_SUFFIX}",),
        ).fetchone()
        if row is None:
            return None
        match = re.search(r"float\[(\d+)\]", row[0] or "")
        return int(match.group(1)) if match else None

    # ── 表名与存在性 ──────────────────────────────────────────────────────

    def collection_name(self, repository_id: str) -> str:
        if not repository_id:
            raise _index_error("仓库标识不能为空")
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", repository_id)
        if not sanitized.strip("_"):
            raise _index_error("仓库标识不能为空")
        name = f"repo_{sanitized}"
        # 保留 collection_name 的既有返回值语义（对外可见于日志与测试），
        # 但落库前必须收敛成合法 SQL 标识符。
        if not _COLLECTION_RE.match(name):
            raise _index_error("仓库标识无法映射为合法的索引名")
        return name

    def _collection(self, repository_id: str, dimension: int | None = None) -> str:
        """取集合名；不存在则按给定维度创建。"""
        collection = self.collection_name(repository_id)
        with self._lock:
            self._create_collection(collection, dimension or self.dimension)
        return collection

    def _memory_collection(self, collection: str) -> str:
        """记忆集合名归一化。

        记忆索引的 collection 名由 memory/indexer.py 生成（canonical_collection），
        但仍是外部输入，必须过一遍合法性校验 —— 它会直接拼进 DDL。
        """
        if not collection:
            raise _index_error("记忆集合名不能为空")
        sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", collection)
        if not _COLLECTION_RE.match(sanitized):
            raise _index_error("记忆集合名无法映射为合法的索引名")
        return sanitized

    def _exists(self, collection: str) -> bool:
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (f"{collection}{_VEC_SUFFIX}",),
        ).fetchone()
        return row is not None

    # ── 写入 ──────────────────────────────────────────────────────────────

    def upsert(
        self,
        repository_id: str,
        ids: list[str],
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> None:
        if not (len(ids) == len(texts) == len(embeddings) == len(metadatas)):
            raise _index_error("向量索引数据长度不一致")
        if not ids:
            return
        collection = self._collection(repository_id, _dimension_of(embeddings))
        with self._lock:
            try:
                self._write(collection, ids, texts, embeddings, metadatas)
                self._conn.commit()
            except DomainError:
                raise
            except Exception as error:
                self._conn.rollback()
                raise _index_error("向量索引写入失败") from error

    def upsert_memory(self, collection: str, ids, embeddings, texts, metadatas) -> None:
        if not (len(ids) == len(texts) == len(embeddings) == len(metadatas)):
            raise _index_error("向量索引数据长度不一致")
        if not ids:
            return
        name = self._memory_collection(collection)
        dimension = _dimension_of(embeddings)
        with self._lock:
            try:
                self._create_collection(name, dimension)
                self._write(name, ids, texts, embeddings, metadatas)
                self._conn.commit()
            except DomainError:
                raise
            except Exception as error:
                self._conn.rollback()
                raise _index_error("记忆向量索引写入失败") from error

    def _write(
        self,
        collection: str,
        ids: list[str],
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> None:
        vec_table = _qi(f"{collection}{_VEC_SUFFIX}")
        meta_table = _qi(f"{collection}{_META_SUFFIX}")
        # 先删后插：sqlite-vec 的 vec0 对已存在主键的 upsert 语义不稳，
        # 显式 delete + insert 结果确定，也顺带清掉旧 metadata 与过滤列。
        #
        # 🔴 删除必须分批：SQLite 单条语句的绑定变量上限是 32766（SQLITE_MAX_VARIABLE_NUMBER），
        # `IN (?,?,...,?)` 展开后 5 万个 id 会直接报 `too many SQL variables`。
        # 实测在 5 万条批量写入时命中。
        for batch in _batched(ids, _SQL_VARIABLE_BATCH):
            marks = ",".join("?" for _ in batch)
            self._conn.execute(f"DELETE FROM {vec_table} WHERE id IN ({marks})", batch)
            self._conn.execute(f"DELETE FROM {meta_table} WHERE id IN ({marks})", batch)
        # 过滤列与 metadata 用同一份归一化结果，避免两边不一致。
        # 列名裸写不加引号：vec0 拒绝带引号的列名（实测 CREATE 与 INSERT 都一样），
        # 列名来自模块常量本身安全。
        normalized = [self._metadata(m) for m in metadatas]
        columns = ("id", "embedding", *_FILTER_COLUMNS)
        column_list = ", ".join(columns)
        value_marks = ", ".join("?" for _ in columns)
        self._conn.executemany(
            f"INSERT INTO {vec_table}({column_list}) VALUES({value_marks})",
            [
                (
                    identifier,
                    _pack(embedding),
                    *(_filter_value(metadata, column) for column in _FILTER_COLUMNS),
                )
                for identifier, embedding, metadata in zip(ids, embeddings, normalized)
            ],
        )
        self._conn.executemany(
            f"INSERT INTO {meta_table}(id, text, metadata) VALUES(?, ?, ?)",
            [
                (identifier, text or "", json.dumps(metadata, ensure_ascii=False))
                for identifier, text, metadata in zip(ids, texts, normalized)
            ],
        )

    # ── 查询 ──────────────────────────────────────────────────────────────

    def query(
        self, repository_id: str, embedding: list[float], top_k: int, *, where: dict[str, Any] | None = None
    ) -> list[VectorHit]:
        if top_k <= 0:
            return []
        collection = self.collection_name(repository_id)
        with self._lock:
            if not self._exists(collection):
                return []
        return self._search(collection, embedding, top_k, where=where)

    def query_memory(
        self, collection: str, embedding: list[float], top_k: int, *, repository_id: str | None = None
    ):
        if top_k <= 0:
            return []
        name = self._memory_collection(collection)
        with self._lock:
            if not self._exists(name):
                return []
        where = {"repository_id": repository_id} if repository_id else None
        return self._search(name, embedding, top_k, where=where)

    def _search(
        self, collection: str, embedding: list[float], top_k: int, *, where: dict[str, Any] | None
    ) -> list[VectorHit]:
        vec_table = _qi(f"{collection}{_VEC_SUFFIX}")
        meta_table = _qi(f"{collection}{_META_SUFFIX}")
        with self._lock:
            try:
                expected = self._stored_dimension(collection)
            except DomainError:
                raise
            except Exception as error:
                # 读索引元信息失败（如数据库文件损坏、权限问题）必须映射成稳定的
                # 领域错误，而不是把底层异常抛给 API 层 —— 调用方按 code 分支处理。
                raise _index_error("向量索引集合不可用") from error
            if expected is not None and len(embedding) != expected:
                # 换嵌入模型后索引里的向量与新查询不同维度，直接查会报
                # 裸 sqlite3 错误。这属于「需要重建索引」，提前拦下并说清楚。
                raise _index_error(
                    f"向量维度不匹配：索引为 {expected}，查询为 {len(embedding)}；"
                    "更换嵌入模型后需要重建索引"
                )
        # 过滤下推到 SQL：让扩展在 ANN 遍历时就按条件筛，既不漏召回也无需
        # 放大候选集（放大是静默漏召回的根源，见 _FILTER_COLUMNS 注释）。
        # k 就是真实要的条数，_MAX_FETCH 只兜住调用方传入异常大 top_k。
        fetch = min(top_k, _MAX_FETCH)
        params: list[Any] = [_pack(embedding), fetch]
        clauses = ""
        if where:
            # 只接受 _FILTER_COLUMNS 里的键：其余键在表里没有对应列，
            # 拼进 SQL 会变成语法错误。忽略未知键而不是报错，避免上游
            # 传了尚不支持的条件时整条查询失败。
            usable = {k: v for k, v in where.items() if k in _FILTER_COLUMNS and v is not None}
            if usable:
                # 列名来自模块常量（不是用户输入），值一律走参数绑定。
                conditions = " AND ".join(f"{_qi(key)} = ?" for key in usable)
                clauses = f" AND {conditions}"
                params.extend(_filter_value(usable, key) for key in usable)
        with self._lock:
            try:
                rows = self._conn.execute(
                    f"SELECT id, distance FROM {vec_table} "
                    f"WHERE embedding MATCH ? AND k = ?{clauses}",
                    params,
                ).fetchall()
            except DomainError:
                raise
            except Exception as error:
                raise _index_error("向量索引查询失败") from error
            if not rows:
                return []
            ids = [r[0] for r in rows]
            meta_rows = []
            # 分批回表：ANN 返回条数受 _MAX_FETCH 限制（512），正常不会撞上
            # SQLite 的变量上限，但统一走 _batched 避免将来改大 _MAX_FETCH 时
            # 突然报 too many SQL variables。
            for batch in _batched(ids, _SQL_VARIABLE_BATCH):
                marks = ",".join("?" for _ in batch)
                try:
                    meta_rows.extend(
                        self._conn.execute(
                            f"SELECT id, text, metadata FROM {meta_table} WHERE id IN ({marks})",
                            batch,
                        ).fetchall()
                    )
                except Exception as error:
                    raise _index_error("向量索引查询失败") from error
        meta_by_id = {r[0]: (r[1], r[2]) for r in meta_rows}
        hits: list[VectorHit] = []
        for identifier, distance in rows:
            payload = meta_by_id.get(identifier)
            if payload is None:
                # 过滤列与元数据表不一致时的兜底：宁可少一条，也不要返回
                # 空 metadata 的脏命中（会让引用溯源拿到没有标题/页码的块）。
                continue
            text, raw_metadata = payload
            try:
                metadata = json.loads(raw_metadata)
            except (TypeError, ValueError):
                metadata = {}
            if where and not _matches(metadata, where):
                # SQL 侧已按 _FILTER_COLUMNS 里的键过滤；到达这里的只可能是
                # 「where 含未知键」的情形。未知键被 SQL 忽略，但 _matches 仍会
                # 判定不通过（metadata 里没这个键）→ 返回空列表，语义一致。
                continue
            similarity = max(-1.0, min(1.0, 1.0 - float(distance)))
            hits.append(
                VectorHit(
                    id=str(identifier), text=str(text or ""), metadata=metadata, similarity=similarity
                )
            )
            if len(hits) >= top_k:
                break
        return hits

    # ── 删除 ──────────────────────────────────────────────────────────────

    def delete(self, repository_id: str, ids: list[str]) -> None:
        if not ids:
            return
        collection = self.collection_name(repository_id)
        with self._lock:
            if not self._exists(collection):
                return
            try:
                self._delete_ids(collection, ids)
                self._conn.commit()
            except Exception as error:
                self._conn.rollback()
                raise _index_error("向量索引删除失败") from error

    def delete_memory(self, collection: str, ids: list[str]) -> None:
        if not ids:
            return
        name = self._memory_collection(collection)
        with self._lock:
            if not self._exists(name):
                return
            try:
                self._delete_ids(name, ids)
                self._conn.commit()
            except Exception as error:
                self._conn.rollback()
                raise _index_error("记忆向量删除失败") from error

    def _delete_ids(self, collection: str, ids: list[str]) -> None:
        vec_table = _qi(f"{collection}{_VEC_SUFFIX}")
        meta_table = _qi(f"{collection}{_META_SUFFIX}")
        # 分批原因同 _write：SQLite 单条语句变量上限 32766。
        for batch in _batched(ids, _SQL_VARIABLE_BATCH):
            marks = ",".join("?" for _ in batch)
            self._conn.execute(f"DELETE FROM {vec_table} WHERE id IN ({marks})", batch)
            self._conn.execute(f"DELETE FROM {meta_table} WHERE id IN ({marks})", batch)

    def delete_collection(self, repository_id: str) -> None:
        collection = self.collection_name(repository_id)
        with self._lock:
            if not self._exists(collection):
                return
            try:
                self._conn.execute(f"DROP TABLE IF EXISTS {_qi(f'{collection}{_VEC_SUFFIX}')}")
                self._conn.execute(f"DROP TABLE IF EXISTS {_qi(f'{collection}{_META_SUFFIX}')}")
                self._conn.commit()
            except Exception as error:
                self._conn.rollback()
                raise _index_error("向量索引集合不可用") from error

    def list_stored(self, repository_id: str) -> list[dict[str, Any]]:
        """列出集合里已落库的条目（id + text + metadata），按 id 排序。

        给「索引完整性核对」用 —— 导入流程要确认某个文档的每个分块都真的
        进了索引，而不是只信 chunk_count。之前这件事是靠读 chromadb 内部
        collection 完成的，那是实现细节泄漏；现在由 store 自己提供出口。
        不走向量查询路径，因此与相似度无关。
        """
        collection = self.collection_name(repository_id)
        with self._lock:
            if not self._exists(collection):
                return []
            try:
                rows = self._conn.execute(
                    f"SELECT id, text, metadata FROM {_qi(f'{collection}{_META_SUFFIX}')} ORDER BY id"
                ).fetchall()
            except Exception as error:
                raise _index_error("向量索引集合不可用") from error
        stored: list[dict[str, Any]] = []
        for identifier, text, raw_metadata in rows:
            try:
                metadata = json.loads(raw_metadata)
            except (TypeError, ValueError):
                metadata = {}
            stored.append({"id": str(identifier), "text": str(text or ""), "metadata": metadata})
        return stored

    # ── 辅助 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in (metadata or {}).items()
            if key in _METADATA_KEYS and value is not None and isinstance(value, (str, int, float, bool))
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _dimension_of(embeddings: list[list[float]]) -> int:
    """取本批向量的维度，并要求批内一致。

    维度由**实际写入的数据**决定，而不是全局配置常量 —— 测试用 2 维/8 维
    构造小样本是合理用法，配置里的 1024 只是生产默认值。
    """
    if not embeddings:
        raise _index_error("向量索引数据长度不一致")
    dimension = len(embeddings[0])
    if any(len(e) != dimension for e in embeddings):
        raise _index_error("同一批向量维度不一致")
    return dimension


def _batched(items: list[str], size: int):
    """按固定大小切片，用于绕开 SQLite 的绑定变量上限。"""
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _pack(vector: list[float]) -> str:
    """向量序列化成 sqlite-vec 要求的 JSON 文本。

    统一转 float 并以 python repr 输出：sqlite-vec 按 float32 解析，
    截断到 float32 精度既省体积也避免不同来源精度不一致。
    """
    return json.dumps([float(x) for x in vector])


def _filter_value(metadata: dict[str, Any], column: str) -> str:
    """取某个过滤列的存储值。

    vec0 的 metadata 列声明为 text，所以统一字符串化。两个要点：

    1. **不能返回 None**：metadata 列不接受 NULL（实测报
       `Expected text for TEXT metadata column doc_id, received NULL`），
       省略该键就会撞上。缺失一律写空串。
    2. **bool 要特判**：Python 的 `str(True)` 是 `"True"`，而 JSON 是 `true`。
       两边规则必须一致，否则按布尔字段过滤永远匹配不上。
    """
    value = metadata.get(column)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _matches(metadata: dict[str, Any], where: dict[str, Any]) -> bool:
    """metadata 是否满足 where 的全部键值等值条件。

    只支持等值 —— 这是实际用到的唯一形态（section_path / repository_id）。
    刻意不做范围/IN/AND/OR：没有调用方需要，写了就是没人验证的死代码。
    """
    return all(metadata.get(key) == value for key, value in where.items())


def _index_error(message: str) -> DomainError:
    return DomainError("INDEX_FAILED", message, 503, True, "重试索引操作")
