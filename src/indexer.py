"""src/indexer.py — 文件遍历 + 切块。

契约见 docs/CONTRACTS.md：
- 空文件/全空白 → chunk_text 返回 []
- 忽略二进制（前 4096 字节含 \\x00）
- followlinks=False，捕获 PermissionError 不中断
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Iterator

from .config import Config

log = logging.getLogger("local_vault.indexer")

# 二进制探针读取长度
_PROBE_BYTES = 4096


def _looks_binary(head: bytes) -> bool:
    """前 4096 字节含 NUL 即视为二进制。"""
    return b"\x00" in head


def iter_files(root: str, cfg: Config) -> Iterator[str]:
    """递归遍历 root，产出合格文件的绝对路径。

    过滤规则：
      - 扩展名必须在 cfg.exts
      - 大小 <= cfg.max_file_bytes
      - 跳过 cfg.skip_dirs 中的目录名
      - 不跟随符号链接（防环）
      - 权限错误跳过而非中断
    """
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # 原地过滤子目录，避免深入无用目录
        dirnames[:] = [d for d in dirnames if d not in cfg.skip_dirs]

        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext not in cfg.exts:
                continue

            path = os.path.join(dirpath, name)

            # 跳过符号链接，避免环路和意外的外部访问
            if os.path.islink(path):
                log.debug("跳过符号链接 %s", path)
                continue

            try:
                st = os.stat(path)
            except (PermissionError, OSError) as exc:
                log.warning("stat 失败，跳过 %s: %s", path, exc)
                continue

            if not os.path.isfile(path):
                continue
            if st.st_size > cfg.max_file_bytes:
                log.debug("跳过大文件 %s (%.1f MB)", path, st.st_size / 1024 / 1024)
                continue
            if st.st_size == 0:
                continue

            # 二进制探针
            try:
                with open(path, "rb") as fh:
                    head = fh.read(_PROBE_BYTES)
            except (PermissionError, OSError) as exc:
                log.warning("读取失败，跳过 %s: %s", path, exc)
                continue
            if _looks_binary(head):
                log.debug("跳过二进制 %s", path)
                continue

            yield path


def read_text(path: str) -> str:
    """读取文本，依次尝试 UTF-8 和 GB18030，失败则跳过。

    - utf-8-sig: 自动去除 BOM
    - gb18030: GBK 的超集，覆盖所有中文编码
    - 都失败时抛 UnicodeDecodeError，由调用方决定如何处理
    """
    # 先尝试 UTF-8
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return fh.read()
    except UnicodeDecodeError:
        pass

    # 回退到 GB18030
    try:
        with open(path, "r", encoding="gb18030") as fh:
            return fh.read()
    except UnicodeDecodeError as exc:
        # 两种编码都失败，记录并重新抛出
        log.warning("编码检测失败，跳过 %s: %s", path, exc)
        raise


def content_hash(text: str) -> str:
    """整文件内容 sha1（hex, 40 位），供 needs_reindex 二次校验。"""
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def make_chunk_id(path: str, start: int, end: int) -> str:
    """按路径和字符偏移生成稳定的 chunk ID。"""
    return hashlib.sha1(f"{path}:{start}:{end}".encode("utf-8")).hexdigest()


def chunk_text(text: str, cfg: Config) -> list[tuple[int, int, str]]:
    """按字符近似 token 切块，块间保留 overlap。

    返回 [(start, end, chunk_str), ...]。
    - 空 / 全空白 → []      （契约 M6）
    - 切块严格前进，不会死循环
    """
    if not text or not text.strip():
        return []

    size = cfg.chunk_size
    overlap = cfg.chunk_overlap
    step = size - overlap  # validate() 保证 > 0

    spans: list[tuple[int, int, str]] = []
    n = len(text)
    start = 0
    while start < n:
        end = min(start + size, n)
        piece = text[start:end]
        if piece.strip():  # 丢弃纯空白块，但不影响偏移推进
            spans.append((start, end, piece))
        if end >= n:
            break
        start += step

    return spans


