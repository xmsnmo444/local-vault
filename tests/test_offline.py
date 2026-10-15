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
    """每个用例前后都确保哨兵已卸载，杜绝顺序污染。"""
    uninstall_offline()
    yield
    uninstall_offline()


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


def test_offline_blocks_dns():
    cfg = load_config(db_path=":memory:", offline=True)
    _ = Store(cfg)
    with pytest.raises(OfflineViolation):
        socket.getaddrinfo("example.com", 80)


def test_offline_uninstalled_after_close():
    """Store 关闭后哨兵应卸载。"""
    cfg = load_config(db_path=":memory:", offline=True)
    with Store(cfg) as store:
        store.init_schema()
    # 已卸载，DNS 应恢复（localhost 能解析）
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
