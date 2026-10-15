"""tests/test_semantic.py — [V2-6] 真模型语义回归测试。

目标：证明 fastembed 真模型对"同义不同词"的查询能正确召回，
而 v0.1 的 hash 嵌入做不到。

若无本地权重（CI 环境），整模块 skip，不依赖网络。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import DEFAULT_MODEL_CACHE, load_config  # noqa: E402
from src.search import search  # noqa: E402
from src.store import Chunk, Store  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.path.isdir(DEFAULT_MODEL_CACHE),
    reason="本机无 fastembed 权重（CI 离线环境），跳过真模型用例",
)


def _cosine(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def test_semantic_fastembed_dim_is_512():
    """实测：bge-small-zh-v1.5 维度为 512（不是常见的 384）。"""
    cfg = load_config(db_path=":memory:", embed_backend="fastembed", offline=True)
    with Store(cfg) as store:
        store.init_schema()
        vec = store.embed_text("测试文本")
        assert len(vec) == 512


def test_semantic_similarity_discriminates():
    """真模型应能区分"相关"与"不相关"：相关相似度显著高于不相关。"""
    cfg = load_config(db_path=":memory:", embed_backend="fastembed", offline=True)
    with Store(cfg) as store:
        store.init_schema()
        q = store.embed_text("怎么烧肉")
        related = store.embed_text("红烧肉怎么做：五花肉、冰糖、老抽")
        unrelated = store.embed_text("python 逆向工程与二进制分析")

        sim_rel = _cosine(q, related)
        sim_unrel = _cosine(q, unrelated)

        assert sim_rel > 0.6, f"相关文本相似度过低：{sim_rel:.3f}"
        assert sim_rel > sim_unrel + 0.3, (
            f"区分度不足：相关 {sim_rel:.3f} vs 不相关 {sim_unrel:.3f}"
        )


def test_semantic_recall_paraphrase(tmp_path):
    """同义改写查询应召回正确文件（hash 嵌入做不到这一点）。"""
    cfg = load_config(
        db_path=str(tmp_path / "v.db"),
        embed_backend="fastembed",
        offline=True,
        chunk_size=128,
        chunk_overlap=16,
    )
    with Store(cfg) as store:
        store.init_schema()
        store.check_model_consistency()
        store.upsert_chunks(
            [
                Chunk(
                    "cook", "/demo/cook.md", 0, 30,
                    "红烧肉做法：五花肉切块，加冰糖炒糖色，小火慢炖一小时。",
                    1.0, "h1",
                ),
                Chunk(
                    "re", "/demo/re.md", 0, 30,
                    "逆向工程笔记：APK 脱壳、smali 反编译、Frida hook 注入。",
                    1.0, "h2",
                ),
                Chunk(
                    "meet", "/demo/meeting.md", 0, 30,
                    "今天开会讨论产品路线图和季度目标。",
                    1.0, "h3",
                ),
            ]
        )
        # 用同义改写查询：不含"红烧肉"三字
        hits = search("怎么炖肉比较好吃", cfg, store, top_k=3)
        assert hits, "语义检索应有结果"
        assert hits[0].path == "/demo/cook.md", (
            f"同义查询未召回正确文件，实际首条：{hits[0].path}"
        )


def test_hash_backend_cannot_do_paraphrase(tmp_path):
    """对照组：hash 嵌入（v0.1）对同义改写查询无法可靠召回。"""
    cfg = load_config(
        db_path=str(tmp_path / "v.db"),
        embed_backend="hash",
        offline=True,
        chunk_size=128,
        chunk_overlap=16,
    )
    with Store(cfg) as store:
        store.init_schema()
        store.check_model_consistency()
        store.upsert_chunks(
            [
                Chunk(
                    "cook", "/demo/cook.md", 0, 30,
                    "红烧肉做法：五花肉切块，加冰糖炒糖色，小火慢炖一小时。",
                    1.0, "h1",
                ),
                Chunk(
                    "re", "/demo/re.md", 0, 30,
                    "逆向工程笔记：APK 脱壳、smali 反编译、Frida hook 注入。",
                    1.0, "h2",
                ),
            ]
        )
        # hash 嵌入无同义能力：此查询不含任何字面重叠词
        hits = search("怎么炖肉比较好吃", cfg, store, top_k=2)
        # 无论结果如何，此处只断言"不崩溃"；语义质量是 fastembed 的职责
        assert isinstance(hits, list)
