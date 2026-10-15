"""tests/test_core.py — 切块、存储、检索、隐私的核心测试。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import load_config  # noqa: E402
from src.indexer import chunk_text, content_hash, iter_files, read_text  # noqa: E402
from src.search import search  # noqa: E402
from src.store import Chunk, Store  # noqa: E402


# ---------- chunk_text ----------
def test_chunk_empty_returns_empty():
    cfg = load_config(db_path=":memory:", chunk_size=10, chunk_overlap=2)
    assert chunk_text("", cfg) == []
    assert chunk_text("   \n\t  ", cfg) == []


def test_chunk_progress_and_overlap():
    cfg = load_config(db_path=":memory:", chunk_size=10, chunk_overlap=2)
    text = "abcdefghijklmnopqrstuvwxyz"  # 26 字符
    spans = chunk_text(text, cfg)
    assert spans, "应产出至少一个块"
    # 覆盖完整
    assert spans[0][0] == 0
    assert spans[-1][1] == len(text)
    # 相邻块有重叠 step = 10-2 = 8
    for a, b in zip(spans, spans[1:]):
        assert b[0] == a[0] + 8, f"步进错误: {a[0]} -> {b[0]}"


def test_chunk_no_infinite_loop_when_size_equals_overlap():
    """validate() 应拦截 overlap >= size。"""
    try:
        load_config(db_path=":memory:", chunk_size=5, chunk_overlap=5)
        raise AssertionError("overlap>=size 应被 validate 拦截")
    except ValueError:
        pass


# ---------- iter_files ----------
def test_iter_files_filters(tmp_path):
    cfg = load_config(db_path=":memory:", max_file_mb=0.001)
    (tmp_path / "a.md").write_text("hello world", encoding="utf-8")
    (tmp_path / "b.exe").write_text("nope", encoding="utf-8")  # 扩展名不符
    (tmp_path / "big.md").write_text("x" * 5000, encoding="utf-8")  # 超限
    (tmp_path / "bin.md").write_bytes(b"\x00\x01\x02binary")  # 二进制
    sub = tmp_path / "node_modules"
    sub.mkdir()
    (sub / "c.md").write_text("skip me", encoding="utf-8")

    found = {Path(p).name for p in iter_files(str(tmp_path), cfg)}
    assert "a.md" in found
    assert "b.exe" not in found
    assert "big.md" not in found
    assert "bin.md" not in found
    assert "c.md" not in found


# ---------- store + search ----------
def test_upsert_prunes_orphans(tmp_path):
    """[M2] 同一 path 重索引不应残留旧 chunk。"""
    cfg = load_config(db_path=str(tmp_path / "v.db"), chunk_size=10, chunk_overlap=2)
    with Store(cfg) as store:
        store.init_schema()
        store.check_model_consistency()
        p = str(tmp_path / "doc.md")
        c1 = [
            Chunk("id1", p, 0, 10, "aaaaaaaaaa", 1.0, "h"),
            Chunk("id2", p, 8, 18, "aaaaaaaaaa", 1.0, "h"),
        ]
        store.upsert_chunks(c1)
        assert store.count() == 2

        # 文件编辑后只产生一个 chunk
        c2 = [Chunk("id9", p, 0, 3, "bb", 2.0, "h2")]
        store.upsert_chunks(c2)
        assert store.count() == 1, "旧 chunk 未被清理（孤儿残留）"
        rows = list(store.all_chunks())
        assert rows[0].chunk_id == "id9"


def test_needs_reindex_mtime_and_hash(tmp_path):
    cfg = load_config(db_path=str(tmp_path / "v.db"))
    with Store(cfg) as store:
        store.init_schema()
        store.check_model_consistency()
        p = str(tmp_path / "d.md")
        store.upsert_chunks([Chunk("x", p, 0, 5, "hello", 100.0, "hashA")])
        assert store.needs_reindex(p, 100.0, "hashA") is False
        assert store.needs_reindex(p, 200.0, "hashA") is True   # mtime 变
        assert store.needs_reindex(p, 100.0, "hashB") is True   # 内容变
        assert store.needs_reindex(str(tmp_path / "new.md"), 0.0) is True


def test_model_consistency_mismatch(tmp_path):
    """[M4] 维度不一致应报错。"""
    db = str(tmp_path / "v.db")
    cfg = load_config(db_path=db, embed_dim=256)
    with Store(cfg) as store:
        store.init_schema()
        store.check_model_consistency()
    cfg2 = load_config(db_path=db, embed_dim=128)
    with Store(cfg2) as store2:
        store2.init_schema()
        try:
            store2.check_model_consistency()
            raise AssertionError("维度不一致竟未报错")
        except RuntimeError as exc:
            assert "不匹配" in str(exc) or "reindex" in str(exc)


def test_model_name_change_triggers_reindex(tmp_path):
    """[P0-1] 只改 model_name（backend 不变）也必须触发不匹配报错。"""
    db = str(tmp_path / "v.db")
    cfg = load_config(db_path=db, model_name="model-A", embed_backend="hash")
    with Store(cfg) as store:
        store.init_schema()
        store.check_model_consistency()
    cfg2 = load_config(db_path=db, model_name="model-B", embed_backend="hash")
    with Store(cfg2) as store2:
        store2.init_schema()
        try:
            store2.check_model_consistency()
            raise AssertionError("改 model_name 竟未触发重建提示（meta 错位 bug 未修）")
        except RuntimeError as exc:
            assert "model_name" in str(exc)


def test_legacy_db_missing_content_hash_column(tmp_path):
    """[P1-5] 旧库无 content_hash 列时应自动补列，不抛错。"""
    import sqlite3

    db = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE chunks (
          chunk_id TEXT PRIMARY KEY, path TEXT NOT NULL, start INTEGER NOT NULL,
          end INTEGER NOT NULL, text TEXT NOT NULL, mtime REAL NOT NULL,
          vec BLOB NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO chunks VALUES('old','p',0,1,'t',1.0,X'00000000')"
    )
    conn.commit()
    conn.close()

    cfg = load_config(db_path=db)
    with Store(cfg) as store:
        store.init_schema()          # 应迁移成功
        store.check_model_consistency()
        assert store.count() == 1    # 旧数据保留


def test_search_returns_hits(tmp_path):
    cfg = load_config(db_path=str(tmp_path / "v.db"), chunk_size=64, chunk_overlap=8)
    with Store(cfg) as store:
        store.init_schema()
        store.check_model_consistency()
        p = str(tmp_path / "notes.md")
        store.upsert_chunks(
            [
                Chunk("a", p, 0, 20, "python 逆向 反调试 崩溃定位", 1.0, "h"),
                Chunk("b", p, 20, 40, "今天天气很好适合钓鱼", 1.0, "h"),
            ]
        )
        hits = search("逆向", cfg, store, top_k=5)
        assert hits, "关键词命中应为空"
        assert any("逆向" in h.text for h in hits)
        assert all(h.source in ("semantic", "keyword", "hybrid") for h in hits)


def test_search_empty_query(tmp_path):
    cfg = load_config(db_path=str(tmp_path / "v.db"))
    with Store(cfg) as store:
        store.init_schema()
        store.check_model_consistency()
        assert search("", cfg, store) == []
        assert search("   ", cfg, store) == []


def test_content_hash_stable():
    assert content_hash("abc") == content_hash("abc")
    assert content_hash("abc") != content_hash("abd")
    assert len(content_hash("abc")) == 40


def test_prune_missing_scoped_to_directory(tmp_path):
    """[P1] prune_missing 只应清理指定目录下的路径，不应误删其他目录的索引。"""
    cfg = load_config(db_path=str(tmp_path / "v.db"))
    with Store(cfg) as store:
        store.init_schema()
        store.check_model_consistency()

        dir_a = tmp_path / "dir_a"
        dir_b = tmp_path / "dir_b"
        dir_a.mkdir()
        dir_b.mkdir()

        path_a = str(dir_a / "file_a.md")
        path_b = str(dir_b / "file_b.md")

        # 索引两个不同目录的文件
        store.upsert_chunks([Chunk("a1", path_a, 0, 5, "content a", 1.0, "ha")])
        store.upsert_chunks([Chunk("b1", path_b, 0, 5, "content b", 1.0, "hb")])
        assert store.count() == 2

        # 清理 dir_a，但 dir_a 内文件已不存在（existing_paths 为空）
        removed = store.prune_missing(set(), str(dir_a))
        assert removed == 1, "应该删除 dir_a 下的 1 个 chunk"
        assert store.count() == 1, "dir_b 的 chunk 应该保留"

        # 验证保留的是 dir_b 的内容
        remaining = list(store.all_chunks())
        assert remaining[0].path == path_b


def test_upsert_transaction_safety(tmp_path):
    """[P2] upsert_chunks 嵌入失败时应回滚，不留半成品。"""
    cfg = load_config(db_path=str(tmp_path / "v.db"))

    class FailingStore(Store):
        def embed_text(self, text: str) -> list[float]:
            if "fail" in text:
                raise RuntimeError("模拟嵌入失败")
            return super().embed_text(text)

    with FailingStore(cfg) as store:
        store.init_schema()
        store.check_model_consistency()

        p = str(tmp_path / "doc.md")
        # 先插入正常数据
        store.upsert_chunks([Chunk("id1", p, 0, 5, "good", 1.0, "h1")])
        assert store.count() == 1

        # 尝试更新，但嵌入失败
        try:
            store.upsert_chunks([Chunk("id2", p, 0, 10, "fail this", 2.0, "h2")])
            raise AssertionError("应该抛出 RuntimeError")
        except RuntimeError as e:
            assert "嵌入失败" in str(e)

        # 验证旧数据仍然存在（回滚成功）
        assert store.count() == 1, "失败时应保留旧数据"
        chunks = list(store.all_chunks())
        assert chunks[0].chunk_id == "id1"
        assert chunks[0].text == "good"


def test_read_text_encoding_fallback(tmp_path):
    """[P3] read_text 应先尝试 UTF-8，再回退 GB18030，都失败则抛错。"""
    # UTF-8 文件
    utf8_file = tmp_path / "utf8.txt"
    utf8_file.write_bytes("你好世界".encode("utf-8"))
    assert read_text(str(utf8_file)) == "你好世界"

    # UTF-8 with BOM
    bom_file = tmp_path / "bom.txt"
    bom_file.write_bytes(b"\xef\xbb\xbf\xe4\xbd\xa0\xe5\xa5\xbd")  # BOM + "你好"
    assert read_text(str(bom_file)) == "你好"

    # GBK 文件
    gbk_file = tmp_path / "gbk.txt"
    gbk_file.write_bytes("测试GBK编码".encode("gbk"))
    assert read_text(str(gbk_file)) == "测试GBK编码"

    # 无效编码（既不是 UTF-8 也不是 GB18030）
    bad_file = tmp_path / "bad.txt"
    bad_file.write_bytes(b"\xff\xfe\x00\x01\x02\x03")
    try:
        read_text(str(bad_file))
        raise AssertionError("无效编码应抛出 UnicodeDecodeError")
    except UnicodeDecodeError:
        pass  # 预期行为


def test_iter_files_skips_symlinks(tmp_path):
    """[P4] iter_files 应跳过符号链接，避免环路和意外的外部访问。"""
    import sys

    cfg = load_config(db_path=":memory:")
    real_file = tmp_path / "real.md"
    real_file.write_text("real content", encoding="utf-8")

    link_file = tmp_path / "link.md"
    # Windows 符号链接需要管理员权限或开发者模式，跳过 Windows
    if sys.platform == "win32":
        import pytest
        pytest.skip("Windows 符号链接需要特殊权限")
    else:
        link_file.symlink_to(real_file)

    found = {Path(p).name for p in iter_files(str(tmp_path), cfg)}
    assert "real.md" in found
    assert "link.md" not in found, "符号链接应被跳过"


def test_vector_dimension_mismatch_raises(tmp_path):
    """[P5] 向量维度不匹配时应抛错，而非静默返回 0。"""
    cfg = load_config(db_path=str(tmp_path / "v.db"), embed_dim=128)
    with Store(cfg) as store:
        store.init_schema()
        store.check_model_consistency()
        store.upsert_chunks([Chunk("x", "p", 0, 5, "test", 1.0, "h")])

    # 用不同维度查询
    cfg2 = load_config(db_path=str(tmp_path / "v.db"), embed_dim=256)
    with Store(cfg2) as store2:
        store2.init_schema()
        # check_model_consistency 应该报错
        try:
            store2.check_model_consistency()
            raise AssertionError("维度不匹配应抛错")
        except RuntimeError as exc:
            assert "不匹配" in str(exc)





