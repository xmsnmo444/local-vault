"""tests/test_offline.py — 离线不变量测试。

对应 Claude D2 的 [CRITICAL]：offline=True 时任何网络访问必须失败。
每个用例结束后卸载哨兵，保证测试互不污染。
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import load_config  # noqa: E402
from src.store import OfflineViolation, Store, uninstall_offline  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_sentinel():
    """每个用例前后都强制卸载哨兵，杜绝顺序污染。"""
    uninstall_offline(force=True)
    yield
    uninstall_offline(force=True)


def test_offline_blocks_connect():
    cfg = load_config(db_path=":memory:", offline=True)
    _ = Store(cfg)  # 安装哨兵
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect(("127.0.0.1", 9))
        raise AssertionError("offline 模式竟然允许了 socket.connect")
    except OfflineViolation:
        pass
    finally:
        sock.close()


def test_offline_blocks_create_connection():
    """[P0-2] socket.create_connection 绕过 socket.connect，必须单独拦。"""
    cfg = load_config(db_path=":memory:", offline=True)
    _ = Store(cfg)
    with pytest.raises(OfflineViolation):
        socket.create_connection(("127.0.0.1", 9), timeout=1)


def test_offline_blocks_dns():
    cfg = load_config(db_path=":memory:", offline=True)
    _ = Store(cfg)
    with pytest.raises(OfflineViolation):
        socket.getaddrinfo("example.com", 80)


def test_offline_refcount_multi_store():
    """[P1-3] 两个 Store 并存，先关一个不应拆掉另一个的哨兵。"""
    cfg1 = load_config(db_path=":memory:", offline=True)
    cfg2 = load_config(db_path=":memory:", offline=True)
    s1 = Store(cfg1)
    s2 = Store(cfg2)
    s1.close()  # 计数 2 -> 1，哨兵应仍在
    with pytest.raises(OfflineViolation):
        socket.getaddrinfo("example.com", 80)
    s2.close()  # 计数 1 -> 0，哨兵卸载


def test_offline_uninstalled_after_close():
    """Store 关闭后哨兵应卸载。"""
    cfg = load_config(db_path=":memory:", offline=True)
    with Store(cfg) as store:
        store.init_schema()
    assert socket.getaddrinfo("localhost", 80)


def test_offline_can_be_disabled():
    """offline=False 时不安装哨兵。"""
    cfg = load_config(db_path=":memory:", offline=False)
    _ = Store(cfg)
    try:
        socket.getaddrinfo("localhost", 80)
    except OfflineViolation:
        raise AssertionError("offline=False 时仍被熔断")
    except OSError:
        pass


def test_fastembed_missing_weights_raises_not_silent_fallback(tmp_path):
    """[v0.2] offline 且权重缺失 → 明确报错提示 fetch-model，绝不静默降级 hash。

    语义变更：v0.1 时 offline 直接熔断 fastembed；v0.2 允许**离线加载**本地权重，
    只禁止联网下载。因此这里的期望是"权重缺失报错"，而非 OfflineViolation。
    """
    empty_cache = tmp_path / "empty_models"
    empty_cache.mkdir()
    cfg = load_config(
        db_path=":memory:",
        offline=True,
        embed_backend="fastembed",
        model_cache_dir=str(empty_cache),
    )
    with Store(cfg) as store:
        store.init_schema()
        with pytest.raises(RuntimeError) as exc:
            store.embed_text("hello")
        msg = str(exc.value)
        # 期望因"权重缺失"报错（而非 fastembed 未安装导致的间接报错）
        assert "fetch-model" in msg, f"报错应提示 fetch-model，实际：{msg}"
        assert "未找到本地模型权重" in msg, f"应明确说明权重缺失，实际：{msg}"


def test_fastembed_offline_load_when_weights_present(tmp_path):
    """[v0.2] 权重存在时，offline 下应能**离线加载**（不熔断）。

    若无本地权重（CI 环境常见）则跳过，避免依赖网络。
    """
    from src.config import DEFAULT_MODEL_CACHE

    if not os.path.isdir(DEFAULT_MODEL_CACHE):
        pytest.skip("本机无已下载权重，跳过离线加载用例")

    cfg = load_config(
        db_path=":memory:", offline=True, embed_backend="fastembed"
    )
    with Store(cfg) as store:
        store.init_schema()
        vec = store.embed_text("红烧肉怎么做")
        assert len(vec) == 512, f"bge-small-zh 维度应为 512，实际 {len(vec)}"
