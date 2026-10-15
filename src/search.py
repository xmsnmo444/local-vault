"""src/search.py — 语义 + 关键词混合检索（RRF 融合）。

契约见 docs/CONTRACTS.md：
- 语义 Top-N 与 BM25 Top-N 各自取 top_k*4
- RRF 融合 k=cfg.rrf_k（默认 60，可覆盖）
- Hit.source ∈ {"semantic","keyword","hybrid"}
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import Config
from .store import Store, _pack

log = logging.getLogger("local_vault.search")

_FANOUT = 4  # 每路召回 = top_k * _FANOUT


@dataclass(frozen=True)
class Hit:
    path: str
    start: int
    end: int
    text: str
    score: float
    source: str


def embed_query(q: str, cfg: Config, store: Store) -> bytes:
    """查询向量打包为 float32 bytes（用于持久化无关的检索接口）。"""
    vec = store.embed_text(q)
    return _pack(vec)


def _rrf(ranked_lists: list[list[str]], k: int) -> dict[str, float]:
    """Reciprocal Rank Fusion：score = Σ 1/(k + rank)。"""
    scores: dict[str, float] = {}
    for ranking in ranked_lists:
        for rank, cid in enumerate(ranking, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return scores


def search(
    q: str,
    cfg: Config,
    store: Store,
    top_k: int = 10,
    rrf_k: int | None = None,
) -> list[Hit]:
    q = (q or "").strip()
    if not q:
        return []

    k = rrf_k if rrf_k is not None else cfg.rrf_k
    fanout = max(top_k * _FANOUT, top_k)

    # 检查模型一致性，避免维度不匹配导致静默失败
    store.check_model_consistency()

    # 语义路
    qvec = embed_query(q, cfg, store)
    sem_pairs = store.search_vector(qvec, fanout)
    sem_ids = [cid for cid, _ in sem_pairs]

    # 关键词路
    kw_pairs = store.search_keyword(q, fanout)
    kw_ids = [cid for cid, _ in kw_pairs]

    sem_set, kw_set = set(sem_ids), set(kw_ids)

    # 融合
    fused = _rrf([sem_ids, kw_ids], k)

    # 归因：两路都命中 → hybrid
    def _source(cid: str) -> str:
        in_s, in_k = cid in sem_set, cid in kw_set
        if in_s and in_k:
            return "hybrid"
        return "semantic" if in_s else "keyword"

    ordered = sorted(fused.items(), key=lambda x: x[1], reverse=True)[:top_k]

    hits: list[Hit] = []
    for cid, score in ordered:
        row = store.get_chunk_text(cid)
        if row is None:
            continue
        text, start, end = row
        path = store.conn.execute(
            "SELECT path FROM chunks WHERE chunk_id=?", (cid,)
        ).fetchone()[0]
        hits.append(
            Hit(
                path=path,
                start=start,
                end=end,
                text=text,
                score=score,
                source=_source(cid),
            )
        )
    return hits
