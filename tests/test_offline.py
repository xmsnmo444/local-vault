"""tests/test_offline.py — 离线不变量测试。

对应 Claude D2 的 [CRITICAL]：offline=True 时任何网络访问必须失败。
每个用例结束后卸载哨兵，保证测试互不污染。
"""

from __future__ import annotations

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


def test_embed_fastembed_blocked_when_offline():
    """[P0-2] offline 时 fastembed 入口必须熔断，不得静默下载权重。"""
    cfg = load_config(
        db_path=":memory:", offline=True, embed_backend="fastembed"
    )
    with Store(cfg) as store:
        store.init_schema()
        with pytest.raises(OfflineViolation):
            store.embed_text("hello")
