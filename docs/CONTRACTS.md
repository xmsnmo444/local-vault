# CONTRACTS.md — 接口契约（冻结后不得随意改）

> 改任何签名前，必须先在 `TASKS.md` 声明并获得 Owner 确认。

## 模块边界与职责

```
src/config.py   : 配置加载，无依赖
src/indexer.py  : 遍历 + 切块，产出 Chunk 流
src/store.py    : 嵌入 + SQLite 读写
src/search.py   : 混合检索，返回 Hit 列表
src/cli.py      : 命令行入口，只做编排
```

## 数据结构（冻结）

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Chunk:
    chunk_id: str      # 稳定 ID：sha1(f"{path}:{start}:{end}").hexdigest()（40 位 hex）[MINOR]
    path: str          # 绝对路径
    start: int         # 字符起点
    end: int           # 字符终点
    text: str          # 原始文本片段
    mtime: float       # 文件修改时间，用于增量
    content_hash: str  # [M3] sha1(整文件内容).hexdigest()，mtime 之外的二次校验

@dataclass(frozen=True)
class Hit:
    path: str
    start: int
    end: int
    text: str
    score: float       # 越大越相关
    source: str        # "semantic" | "keyword" | "hybrid"
```

## 函数签名（冻结）

### src/config.py
```python
@dataclass
class Config:
    db_path: str = "vault.db"
    model_name: str = "BAAI/bge-small-zh-v1.5"
    chunk_size: int = 512          # 以 token 近似，按字符估算
    chunk_overlap: int = 64
    exts: tuple[str, ...] = (".txt", ".md", ".markdown", ".py", ".js", ".ts",
                             ".json", ".csv", ".log", ".html", ".htm",
                             ".yaml", ".yml", ".toml", ".ini", ".cfg", ".rst")
    max_file_mb: float = 20.0
    embed_batch: int = 32
    offline: bool = True           # 强制离线，True 时禁止任何网络调用
    rrf_k: int = 60                # [M7] RRF 融合常数，可被 search() 覆盖
    embed_backend: str = "hash"    # "hash"(内置，零依赖，v0.1 默认) | "fastembed"(可选)
    embed_dim: int = 256           # [M4] 当前后端向量维度，写入 meta 表（hash=256；fastembed/bge-small-zh=512，见 v0.2 修正）
    skip_dirs: frozenset[str] = ... # [P1-4] 遍历时跳过的目录名集合（.git/node_modules 等）

def load_config(**overrides) -> Config: ...
```

> [P1-4] 说明：`exts` 已从初版 7 种扩展为 17 种纯文本扩展名（新增 `.markdown/.js/.ts/.htm/.yaml/.yml/.toml/.ini/.cfg/.rst`），并新增 `skip_dirs` 字段。此为正向扩展，已在本表声明。
> [MINOR] 环境变量兜底：`LV_DB_PATH` / `LV_OFFLINE` / `LV_EXTS` 可覆盖默认值（非契约扩展，属便利功能）。
> [P1-5] meta 表三键 `vec_dim` / `embed_backend` / `model_name` 分别存储；旧库缺键自动补齐。旧库缺 `content_hash` 列时 `init_schema` 自动 `ALTER TABLE` 迁移；旧库 `content_hash=''` 视为无需二次校验。

### v0.2 新增：权重获取与离线两阶段约定（Owner 已确认方案 A）

> 实测结论（2026-10-15，`D:\逆向\local-vault\.venv`）：`BAAI/bge-small-zh-v1.5`（fastembed/Qdrant 版）实测维度 **512**（非 384）；`onnxruntime 1.31.0` 在本机 `import` 即 segfault，`1.18.1` 可用但会降 `numpy` 至 1.26，故 **v0.2 必须走独立 `.venv`**。

**V2-0 隔离环境（已落地）**

- 宿主 Python 不动（`numpy 2.5.4 / onnxruntime 1.31.0` 保留）。
- 项目自带 `local-vault/.venv`（Python 3.12），锁定 `onnxruntime==1.18.1`、`numpy==1.26.4`、`fastembed==0.9.0`，清单 `requirements-v0.2.txt`（`pip freeze`）。
- `.venv/` 已在 `.gitignore`，不入仓；CI 与本地均以 `.venv` 为准。

**Config 维度修正与新增字段**

```python
@dataclass
class Config:
    # ...（v0.1 字段不变）
    embed_dim: int = 0  # 0 = 按后端自动推断（见 resolve_embed_dim），>0 则显式覆盖
    model_cache_dir: str = ""  # "" = 默认 ~/.cache/local-vault/models

    def resolve_embed_dim(self) -> int: ...
        # hash → 256；fastembed → 按 model_name 查表（bge-small-zh 实测 512，未知模型回退 512）

    @property
    def resolved_cache_dir(self) -> str: ...  # model_cache_dir or DEFAULT_MODEL_CACHE
```

- `embed_dim` 默认 `0`，`resolve_embed_dim()` 按后端推断：`hash→256`，`fastembed("BAAI/bge-small-zh-v1.5")→512`。
- `model_cache_dir` / `resolved_cache_dir` / `DEFAULT_MODEL_CACHE` 统一权重路径。

  `Store.check_model_consistency()` 已按三键比对 `vec_dim/resolve_embed_dim()`，维度不匹配抛 `RuntimeError` 提示删库重建（沿用 M4）。

**唯一联网入口：`fetch-model`**

```python
# CLI 新增（唯一允许联网的子命令）
#   python -m src.cli fetch-model [--model BAAI/bge-small-zh-v1.5] [--cache-dir ~/.cache/local-vault/models]
# 约定：
# - index / search / stats 全程必须 offline=True，任何隐式联网抛 OfflineViolation（但 fastembed 的离线加载例外，见下）。
# - fetch-model 显式联网下载一次，写入 cache_dir（默认 ~/.cache/local-vault/models，不用 fastembed 默认路径，避免与其他工具混淆）。
# - 之后所有 Store 加载走 HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1 强制离线（实测 0.1s），权重缺失 + offline=True → 明确报错并提示 fetch-model，禁止静默降级到 hash。
def cmd_fetch_model(args: argparse.Namespace) -> int: ...
```

**两阶段语义（契约级，v0.2 修正）**

> 修正：v0.1 的「offline 即熔断 fastembed」是错的。**offline 允许离线加载本地权重，只禁止联网下载。**

1. 阶段一（联网）：`fetch-model` 一次性下载。
2. 阶段二（离线）：`Store._embed_fastembed` 仅从 `cache_dir` 离线加载；`offline=True` 时**不熔断** `fastembed` 本身，改为检查 `cache_dir` 是否存在且非空 → 强制 `HF_HUB_OFFLINE=1` 加载，缺失则明确报错并提示 `fetch-model`，绝不静默降级到 hash（见 `test_fastembed_missing_weights_raises_not_silent_fallback`）。

**不变量**

- `index`/`search` 永远离线（受 `offline` 哨兵约束，但 `fastembed` 离线加载是例外）；`fetch-model` 是唯一联网例外，CI 中 `fetch-model` 不应被调用。
- 维度与权重一一绑定：换 `model_name` 即换 `resolve_embed_dim()`，旧库向量静默错乱已被 `test_model_name_change_triggers_reindex` 卡死。
```

### src/indexer.py
```python
def iter_files(root: str, cfg: Config) -> Iterator[str]: ...
def chunk_text(text: str, cfg: Config) -> list[tuple[int, int, str]]: ...
def iter_chunks(root: str, cfg: Config) -> Iterator[Chunk]: ...
```
- `iter_files`：递归遍历，过滤扩展名、跳过 > max_file_mb、跳过隐藏目录 `.git`/`node_modules`；`followlinks=False` [MINOR]，捕获 `PermissionError` 跳过。
- `chunk_text`：返回 `(start, end, text)`，chunk 间保留 overlap。[M6] **空文件 / 全空白文本 → 返回 `[]`**（不产生空 chunk）。
- 二进制检测：前 4096 字节含 `\x00` 则跳过。
- [M3] `iter_chunks` 在 `Chunk.mtime` 之外补齐 `Chunk.content_hash`，供 `needs_reindex` 二次校验。

### src/store.py
```python
class Store:
    def __init__(self, cfg: Config) -> None: ...            # 首行执行离线熔断校验 [CRITICAL]
    def init_schema(self) -> None: ...                       # 建 chunks 表 + meta 表 [M4]
    def check_model_consistency(self) -> None: ...           # [M4] 维度/模型不一致 → 抛异常提示 reindex
    def upsert_chunks(self, chunks: Iterable[Chunk]) -> int: ...  # [M2] 同 path 先 delete 再 insert
    def prune_path(self, path: str) -> int: ...              # [M2] 删除该 path 的孤儿 chunk，返回删除数
    def commit(self) -> None: ...
    def needs_reindex(self, path: str, mtime: float, content_hash: str | None = None) -> bool: ...
    def all_chunks(self) -> Iterator[Chunk]: ...
    def search_vector(self, qvec: bytes, top_k: int) -> list[tuple[str, float]]: ...
    # [M1] 返回 (chunk_id, score)，str 语义 = chunk_id，不是 path
    def search_keyword(self, query: str, top_k: int) -> list[tuple[str, float]]: ...  # [M5] 内存 BM25
    def stats(self) -> dict[str, int]: ...                   # [MINOR] 暴露 skipped/计数
```
- 表：`chunks(chunk_id PK, path, start, end, text, mtime, content_hash, vec BLOB)`
- 表：`meta(key TEXT PK, value TEXT)` —— 存 `vec_dim`、`model_name`、`schema_ver` [M4]
- [M5] 关键词检索为**纯内存 BM25**，分词 = 中文按字符二元（bigram）+ 英文/数字按词；不引入 FTS5、不依赖 jieba。
- 向量以 float32 打包成 BLOB 存 SQLite，检索时在进程内做**余弦相似度**（查询向量已归一化，存储向量入库时归一化）。

### src/search.py
```python
def embed_query(q: str, cfg: Config, store: Store) -> bytes: ...
def search(q: str, cfg: Config, store: Store, top_k: int = 10, rrf_k: int | None = None) -> list[Hit]: ...
```
- 语义 Top-N（N=top_k*4）∪ 关键词 BM25 Top-N → **RRF 融合（k=`cfg.rrf_k`，默认 60，可覆盖）** [M7] → 取 top_k。
- `source` 字段标明 `"semantic" | "keyword" | "hybrid"`。

### src/cli.py
```python
# 用法：
#   python -m src.cli index  <dir> [--force]
#   python -m src.cli search "<query>" [-k 10]
#   python -m src.cli stats
def main(argv: list[str] | None = None) -> int: ...
# [MINOR] 退出码：0 成功 / 1 参数错误 / 2 索引失败
```

## 隐私自检清单（每次 PR 必须过）
- [ ] 全仓库无 `requests.post` / `urllib` / `socket` 外连（测试除外，且测试断言断网）
- [ ] `cfg.offline=True` 时，任何模型下载行为被禁止
- [ ] 没有 telemetry / 埋点 / 上报 URL
- [ ] README 明确写出"数据不出本机"
