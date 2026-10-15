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
        dirnames[:] = [
            d for d in dirnames if d not in cfg.skip_dirs and not d.startswith("$")
        ]

        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext not in cfg.exts:
                continue

            path = os.path.join(dirpath, name)
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
    """读取文本，坏字节忽略，绝不因编码中断遍历。"""
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        return fh.read()


def content_hash(text: str) -> str:
    """整文件内容 sha1（hex, 40 位），供 needs_reindex 二次校验。"""
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


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


def iter_chunks(root: str, cfg: Config) -> Iterator["object"]:
    """遍历并产出 Chunk 流。

    延迟导入 Chunk 以避免与 store 的循环依赖。
    """
    from .store import Chunk

    for path in iter_files(root, cfg):
        try:
            text = read_text(path)
            st = os.stat(path)
        except (PermissionError, OSError) as exc:
            log.warning("读取失败，跳过 %s: %s", path, exc)
            continue

        chash = content_hash(text)
        for start, end, piece in chunk_text(text, cfg):
            cid = hashlib.sha1(f"{path}:{start}:{end}".encode("utf-8")).hexdigest()
            yield Chunk(
                chunk_id=cid,
                path=path,
                start=start,
                end=end,
                text=piece,
                mtime=st.st_mtime,
                content_hash=chash,
            )
