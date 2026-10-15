"""src/store.py — 嵌入 + SQLite 持久化 + 向量/关键词检索。

契约见 docs/CONTRACTS.md。关键点：
- [CRITICAL] __init__ 首行执行离线熔断：offline=True 时禁用任何网络。
- [M2] upsert 对同一 path 先删后插，避免编辑后孤儿 chunk 残留。
- [M4] meta 表记录 vec_dim / model_name，不一致时报错要求 reindex。
- [M5] 关键词检索为纯内存 BM25（字符二元 + 英文词），不依赖 FTS5/jieba。
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import sqlite3
import struct
from dataclasses import dataclass
from typing import Iterable, Iterator

from .config import Config

log = logging.getLogger("local_vault.store")

SCHEMA_VER = "1"

_TOKEN_RE = re.compile(r"[0-9a-zA-Z_]+")


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    path: str
    start: int
    end: int
    text: str
    mtime: float
    content_hash: str
    size: int = 0  # 文件大小（字节），用于增量索引优化


# --------------------------------------------------------------------------
# 离线熔断
# --------------------------------------------------------------------------
class OfflineViolation(RuntimeError):
    """offline=True 时检测到网络访问企图。"""


# [P1-3] 哨兵引用计数：多个 Store 并存时，只有归零才真正卸载。
_offline_refcount = 0


def assert_offline() -> None:
    """安装网络访问哨兵：offline 模式下任何联网企图直接抛错。

    [P0-2] 覆盖 socket.socket.connect / getaddrinfo / create_connection。
    [P1-3] 用引用计数支持多 Store 并存，归零才卸载。
    """
    global _offline_refcount
    import socket

    _offline_refcount += 1

    if getattr(socket.socket, "_lv_patched", False):
        return  # 已装，计数已加

    socket._lv_orig_socket_connect = socket.socket.connect  # type: ignore[attr-defined]
    socket._lv_orig_getaddrinfo = socket.getaddrinfo  # type: ignore[attr-defined]
    socket._lv_orig_create_connection = socket.create_connection  # type: ignore[attr-defined]

    def guarded_connect(self, address):  # type: ignore[no-untyped-def]
        raise OfflineViolation(
            "offline mode: network disabled（local-vault 不允许把数据发往网络）"
        )

    def guarded_getaddrinfo(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OfflineViolation("offline mode: DNS disabled")

    def guarded_create_connection(*args, **kwargs):  # type: ignore[no-untyped-def]
        # [P0-2] socket.create_connection 绕过 socket.connect 需单独拦
        raise OfflineViolation("offline mode: create_connection disabled")

    socket.socket.connect = guarded_connect  # type: ignore[assignment]
    socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]
    socket.create_connection = guarded_create_connection  # type: ignore[assignment]
    socket.socket._lv_patched = True  # type: ignore[attr-defined]


def uninstall_offline(force: bool = False) -> None:
    """卸载网络哨兵。

    force=False（默认）：引用计数减一，仅归零时真正卸载。
    force=True：无条件卸载（测试隔离用）。
    """
    global _offline_refcount
    import socket

    if force:
        _offline_refcount = 0
    else:
        _offline_refcount = max(0, _offline_refcount - 1)
        if _offline_refcount > 0:
            return

    if not getattr(socket.socket, "_lv_patched", False):
        return
    socket.socket.connect = socket._lv_orig_socket_connect  # type: ignore[attr-defined]
    socket.getaddrinfo = socket._lv_orig_getaddrinfo  # type: ignore[attr-defined]
    socket.create_connection = socket._lv_orig_create_connection  # type: ignore[attr-defined]
    socket.socket._lv_patched = False  # type: ignore[attr-defined]


def _hash_embed(text: str, dim: int) -> list[float]:
    """内置哈希嵌入：确定性、零依赖、离线。

    原理：对文本做字符 bigram + 英文词，用 sha1 决定落桶，符号由奇偶决定，
    再做 L2 归一化。不是语义模型，但能给出稳定可测的相似度，
    让整条管线（存储→检索→融合→CLI）在无重依赖下先跑通。
    """
    vec = [0.0] * dim
    tokens = _tokens_for_embed(text)
    if not tokens:
        return vec

    for tok in tokens:
        h = hashlib.sha1(tok.encode("utf-8")).digest()
        idx = struct.unpack("<I", h[:4])[0] % dim
        sign = 1.0 if (h[4] & 1) else -1.0
        vec[idx] += sign

    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def _tokens_for_embed(text: str) -> list[str]:
    """[P2-6] 复用统一分词，避免与 BM25 策略漂移。"""
    return _bm25_tokens(text)


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    """两向量余弦相似度；调用方保证已归一化时等价于点积。"""
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        raise ValueError(
            f"向量维度不匹配：查询向量 {len(a)} 维，库内向量 {len(b)} 维。"
            "可能索引时使用了不同的模型或维度配置，请删库重建索引。"
        )
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------
class Store:
    def __init__(self, cfg: Config) -> None:
        # [CRITICAL] 首行熔断
        if cfg.offline:
            assert_offline()
        self.cfg = cfg
        self.conn = sqlite3.connect(cfg.db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        # 近期 token 缓存，避免重复算嵌入
        self._embed_cache: dict[str, list[float]] = {}

    # ---------- schema ----------
    def init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS chunks (
              chunk_id     TEXT PRIMARY KEY,
              path         TEXT NOT NULL,
              start        INTEGER NOT NULL,
              end          INTEGER NOT NULL,
              text         TEXT NOT NULL,
              mtime        REAL NOT NULL,
              content_hash TEXT NOT NULL DEFAULT '',
              vec          BLOB NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);

            CREATE TABLE IF NOT EXISTS meta (
              key   TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            """
        )
        self._migrate(cur)
        self.conn.commit()

    def _migrate(self, cur: sqlite3.Cursor) -> None:
        """[P1-5] 兼容旧库：缺失的列补上，避免 SELECT 报错。"""
        cols = {row[1] for row in cur.execute("PRAGMA table_info(chunks)")}
        if "content_hash" not in cols:
            cur.execute(
                "ALTER TABLE chunks ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''"
            )
            log.info("已迁移：chunks.content_hash 列已补上（旧库升级）")
        if "size" not in cols:
            cur.execute(
                "ALTER TABLE chunks ADD COLUMN size INTEGER"
            )
            log.info("已迁移：chunks.size 列已补上（旧库升级）")


    def _meta_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def _meta_set(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def check_model_consistency(self) -> None:
        """[M4] 维度/模型与库内 meta 不一致时，提示需要 reindex。

        meta 三键：vec_dim / embed_backend / model_name。
        [P0-1] backend 与 model_name 必须分开存，否则切换 model_name 不触发重建。
        """
        dim = str(self.cfg.resolve_embed_dim())
        backend = self.cfg.embed_backend
        model = self.cfg.model_name
        keys = {
            "vec_dim": dim,
            "embed_backend": backend,
            "model_name": model,
        }

        stored_dim = self._meta_get("vec_dim")
        if stored_dim is None:
            # 首次使用，写入全部三键
            for k, v in keys.items():
                self._meta_set(k, v)
            self._meta_set("schema_ver", SCHEMA_VER)
            self.conn.commit()
            return

        mismatched = []
        for k, v in keys.items():
            stored = self._meta_get(k)
            if stored is not None and stored != v:
                mismatched.append(f"{k}: 库={stored} 当前={v}")
            elif stored is None:
                # 旧库缺键：补齐，不视为不匹配
                self._meta_set(k, v)

        if mismatched:
            self.conn.commit()
            raise RuntimeError(
                "库内向量不匹配：\n  " + "\n  ".join(mismatched) + "\n"
                f"请删除 {self.cfg.db_path} 后重建索引。"
            )
        self.conn.commit()

    # ---------- 写入 ----------
    def embed_text(self, text: str) -> list[float]:
        cached = self._embed_cache.get(text)
        if cached is not None:
            return cached
        if self.cfg.embed_backend == "hash":
            vec = _hash_embed(text, self.cfg.resolve_embed_dim())
        else:  # fastembed（可选，本地权重）
            vec = self._embed_fastembed(text)
        if len(self._embed_cache) < 4096:
            self._embed_cache[text] = vec
        return vec

    def _embed_fastembed(self, text: str) -> list[float]:
        """fastembed 真模型后端（v0.2）。

        两阶段语义（见 docs/CONTRACTS.md v0.2）：
        - offline=True 允许**离线加载本地权重**，只是禁止联网下载。
        - 权重缺失 → 明确报错并提示 `fetch-model`，绝不静默降级到 hash。
        - 加载时强制 HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1，物理上杜绝联网。
        """
        if not hasattr(self, "_fe"):
            cache_dir = self.cfg.resolved_cache_dir
            if not os.path.isdir(cache_dir) or not os.listdir(cache_dir):
                raise RuntimeError(
                    f"未找到本地模型权重：{cache_dir}\n"
                    f"请先运行：python -m src.cli fetch-model "
                    f"--model {self.cfg.model_name}\n"
                    "（或改用 --backend hash 走内置嵌入）"
                )

            try:
                from fastembed import TextEmbedding  # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    "embed_backend='fastembed' 需要安装 fastembed："
                    "pip install -r requirements-v0.2.txt"
                ) from exc

            # 强制离线加载：即使宿主能联网，也只在本地缓存里找权重
            saved = {}
            for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
                saved[var] = os.environ.get(var)
                os.environ[var] = "1"
            try:
                self._fe = TextEmbedding(
                    model_name=self.cfg.model_name, cache_dir=cache_dir
                )
            finally:
                for var, old in saved.items():
                    if old is None:
                        os.environ.pop(var, None)
                    else:
                        os.environ[var] = old

        vecs = list(self._fe.embed([text]))
        return [float(x) for x in vecs[0]]

    def upsert_chunks(self, chunks: Iterable[Chunk]) -> int:
        """[M2] 先嵌入并准备数据，再在一个事务内删除和插入。返回写入行数。"""
        batch = list(chunks)
        if not batch:
            return 0

        paths = {c.path for c in batch}

        # 先完成嵌入，准备好所有数据
        rows = []
        seen: set[str] = set()
        for c in batch:
            if c.chunk_id in seen:
                continue
            seen.add(c.chunk_id)
            vec = self.embed_text(c.text)
            rows.append(
                (
                    c.chunk_id,
                    c.path,
                    c.start,
                    c.end,
                    c.text,
                    c.mtime,
                    c.content_hash,
                    c.size,
                    _pack(vec),
                )
            )

        # 现在在一个事务内完成删除和插入
        cur = self.conn.cursor()
        cur.execute("BEGIN")
        try:
            for path in paths:
                cur.execute("DELETE FROM chunks WHERE path=?", (path,))

            cur.executemany(
                "INSERT OR REPLACE INTO chunks"
                "(chunk_id, path, start, end, text, mtime, content_hash, size, vec) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        return len(rows)

    def prune_path(self, path: str) -> int:
        cur = self.conn.execute("DELETE FROM chunks WHERE path=?", (path,))
        self.conn.commit()
        return cur.rowcount

    def prune_missing(self, existing_paths: set[str], root_dir: str | None = None) -> int:
        """删除磁盘上已不存在的文件的 chunk。返回删除数。

        root_dir: 如果提供，只清理该目录下的路径，避免误删其他目录的索引。
        """
        all_paths = [r[0] for r in self.conn.execute("SELECT DISTINCT path FROM chunks")]

        if root_dir is not None:
            root_dir = os.path.abspath(root_dir)
            # 只检查本次索引目录下的路径
            relevant_paths = [p for p in all_paths if os.path.abspath(p).startswith(root_dir + os.sep) or os.path.abspath(p) == root_dir]
            gone = [p for p in relevant_paths if p not in existing_paths]
        else:
            gone = [p for p in all_paths if p not in existing_paths]

        total = 0
        for p in gone:
            total += self.prune_path(p)
        return total

    def commit(self) -> None:
        self.conn.commit()

    # ---------- 读取 ----------
    def needs_reindex(
        self,
        path: str,
        mtime: float,
        size: int,
        content_hash: str | None = None,
    ) -> bool:
        """[M3] mtime/size 为主，content_hash 为二次校验。

        流程：
        1. 检查文件是否在库中
        2. 比较 mtime，变化则检查 content_hash
        3. mtime 未变但提供了 size，检查 size 是否一致
        4. 都通过则不需要重新索引
        """
        row = self.conn.execute(
            "SELECT mtime, content_hash, size FROM chunks WHERE path=? LIMIT 1",
            (path,),
        ).fetchone()
        if row is None:
            return True

        stored_mtime, stored_hash, stored_size = row

        # mtime 变化，需要进一步检查
        if abs(stored_mtime - mtime) > 1e-6:
            # 如果提供了 content_hash，用它判断
            if content_hash is not None and stored_hash and stored_hash == content_hash:
                # 内容未变，只需更新 mtime（由调用方决定）
                return False
            # mtime 变了但没有 hash 对比，保守认为需要重建
            return True

        # mtime 未变，检查 size（如果提供）
        if size >= 0 and stored_size is not None and stored_size != size:
            return True

        # 如果提供了 content_hash 做最终校验
        if content_hash is not None and stored_hash and stored_hash != content_hash:
            return True

        return False

    def update_mtime_only(self, path: str, new_mtime: float) -> None:
        """[M3] mtime 变化但内容未变时，仅更新 mtime 而不重新嵌入。"""
        self.conn.execute(
            "UPDATE chunks SET mtime=? WHERE path=?",
            (new_mtime, path),
        )

    def all_chunks(self) -> Iterator[Chunk]:
        for row in self.conn.execute(
            "SELECT chunk_id, path, start, end, text, mtime, content_hash FROM chunks"
        ):
            yield Chunk(*row)

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def distinct_paths(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT DISTINCT path FROM chunks")}

    # ---------- 检索：向量 ----------
    def search_vector(self, qvec: bytes, top_k: int) -> list[tuple[str, float]]:
        """[M1] 返回 (chunk_id, score)，score 越大越相似。"""
        query = _unpack(qvec)
        scored: list[tuple[str, float]] = []
        for cid, blob in self.conn.execute("SELECT chunk_id, vec FROM chunks"):
            scored.append((cid, _cosine(query, _unpack(blob))))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    # ---------- 检索：关键词 BM25 ----------
    def search_keyword(self, query: str, top_k: int) -> list[tuple[str, float]]:
        """[M5] 纯内存 BM25，中文按字符二元，英文按词。"""
        q_terms = _bm25_tokens(query)
        if not q_terms:
            return []

        docs: list[tuple[str, dict[str, int], int]] = []
        df: dict[str, int] = {}
        for cid, text in self.conn.execute("SELECT chunk_id, text FROM chunks"):
            toks = _bm25_tokens(text)
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            for t in tf:
                df[t] = df.get(t, 0) + 1
            docs.append((cid, tf, len(toks)))

        n_docs = len(docs)
        if n_docs == 0:
            return []
        avgdl = sum(d[2] for d in docs) / n_docs or 1.0
        k1 = 1.5
        b = 0.75

        results: list[tuple[str, float]] = []
        for cid, tf, dl in docs:
            score = 0.0
            for term in q_terms:
                f = tf.get(term)
                if not f:
                    continue
                n_q = df.get(term, 0)
                idf = math.log(1.0 + (n_docs - n_q + 0.5) / (n_q + 0.5))
                denom = f + k1 * (1 - b + b * dl / avgdl)
                score += idf * (f * (k1 + 1)) / denom
            if score > 0:
                results.append((cid, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    # ---------- 统计 ----------
    def get_chunk_text(self, chunk_id: str) -> tuple[str, int, int] | None:
        row = self.conn.execute(
            "SELECT text, start, end FROM chunks WHERE chunk_id=?", (chunk_id,)
        ).fetchone()
        if row is None:
            return None
        return row[0], row[1], row[2]

    def stats(self) -> dict[str, int]:
        """[MINOR] 暴露可观测计数。"""
        chunks = self.count()
        paths = self.conn.execute("SELECT COUNT(DISTINCT path) FROM chunks").fetchone()[0]
        size = self.conn.execute(
            "SELECT COALESCE(SUM(LENGTH(text)), 0) FROM chunks"
        ).fetchone()[0]
        return {"chunks": chunks, "files": paths, "text_bytes": int(size)}

    def close(self) -> None:
        try:
            self.conn.commit()
        finally:
            self.conn.close()
            if self.cfg.offline:
                uninstall_offline()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# --------------------------------------------------------------------------
# BM25 分词
# --------------------------------------------------------------------------
def _bm25_tokens(text: str) -> list[str]:
    """英文/数字 → 小写词；连续中文 → 单字 + bigram。"""
    tokens: list[str] = []
    for w in _TOKEN_RE.findall(text.lower()):
        tokens.append(w)
    cjk_run = re.findall(r"[\u4e00-\u9fff]+", text)
    for run in cjk_run:
        for ch in run:
            tokens.append(ch)
        for i in range(len(run) - 1):
            tokens.append(run[i] + run[i + 1])
    return tokens
