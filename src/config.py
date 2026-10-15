"""src/config.py — 配置加载，无外部依赖。

契约见 docs/CONTRACTS.md。修改任何字段前须先改契约。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace


# v0.1 默认扩展名：纯文本类，不含二进制/办公格式（后续演进再加）。
DEFAULT_EXTS: tuple[str, ...] = (
    ".txt",
    ".md",
    ".markdown",
    ".py",
    ".js",
    ".ts",
    ".json",
    ".csv",
    ".log",
    ".html",
    ".htm",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".rst",
)

# 遍历时永久跳过的目录名（无论隐藏与否）。
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".idea",
        ".vscode",
    }
)


@dataclass
class Config:
    """运行时配置。所有字段与 docs/CONTRACTS.md 保持一致。"""

    db_path: str = "vault.db"
    model_name: str = "BAAI/bge-small-zh-v1.5"
    chunk_size: int = 512
    chunk_overlap: int = 64
    exts: tuple[str, ...] = DEFAULT_EXTS
    max_file_mb: float = 20.0
    embed_batch: int = 32
    offline: bool = True
    rrf_k: int = 60
    embed_backend: str = "hash"  # "hash"(内置) | "fastembed"(可选)
    embed_dim: int = 256
    skip_dirs: frozenset[str] = field(default_factory=lambda: SKIP_DIRS)

    def validate(self) -> None:
        """参数自检：越界立即报错，避免跑一半才崩。"""
        if self.chunk_size <= 0:
            raise ValueError(f"chunk_size 必须 > 0，当前 {self.chunk_size}")
        if self.chunk_overlap < 0:
            raise ValueError(f"chunk_overlap 必须 >= 0，当前 {self.chunk_overlap}")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap({self.chunk_overlap}) 必须 < chunk_size({self.chunk_size})，"
                "否则切块会死循环"
            )
        if self.max_file_mb <= 0:
            raise ValueError(f"max_file_mb 必须 > 0，当前 {self.max_file_mb}")
        if self.embed_batch <= 0:
            raise ValueError(f"embed_batch 必须 > 0，当前 {self.embed_batch}")
        if self.rrf_k <= 0:
            raise ValueError(f"rrf_k 必须 > 0，当前 {self.rrf_k}")
        if self.embed_dim <= 0:
            raise ValueError(f"embed_dim 必须 > 0，当前 {self.embed_dim}")
        if self.embed_backend not in ("hash", "fastembed"):
            raise ValueError(
                f"embed_backend 只支持 'hash' / 'fastembed'，当前 {self.embed_backend!r}"
            )

    @property
    def max_file_bytes(self) -> int:
        return int(self.max_file_mb * 1024 * 1024)


def load_config(**overrides) -> Config:
    """构造并校验配置。

    支持从环境变量兜底（LV_DB_PATH / LV_OFFLINE），显式 overrides 优先级最高。
    """
    env_overrides: dict[str, object] = {}

    db_env = os.environ.get("LV_DB_PATH")
    if db_env:
        env_overrides["db_path"] = db_env

    off_env = os.environ.get("LV_OFFLINE")
    if off_env is not None:
        env_overrides["offline"] = off_env.strip().lower() not in ("0", "false", "no")

    # 允许通过环境变量传 exts（逗号分隔）便于脚本化。
    exts_env = os.environ.get("LV_EXTS")
    if exts_env:
        parsed = tuple(
            e if e.startswith(".") else f".{e}"
            for e in (x.strip().lower() for x in exts_env.split(","))
            if e
        )
        if parsed:
            env_overrides["exts"] = parsed

    merged = {**env_overrides, **overrides}
    cfg = replace(Config(), **merged) if merged else Config()
    cfg.validate()
    return cfg
