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
    chunk_id: str      # 稳定 ID：sha1(f"{path}:{start}:{end}")
    path: str          # 绝对路径
    start: int         # 字符起点
    end: int           # 字符终点
    text: str          # 原始文本片段
    mtime: float       # 文件修改时间，用于增量

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
    exts: tuple[str, ...] = (".txt", ".md", ".py", ".json", ".csv", ".log", ".html")
    max_file_mb: float = 20.0
    embed_batch: int = 32
    offline: bool = True           # 强制离线，True 时禁止任何网络调用

def load_config(**overrides) -> Config: ...
```

### src/indexer.py
```python
def iter_files(root: str, cfg: Config) -> Iterator[str]: ...
def chunk_text(text: str, cfg: Config) -> list[tuple[int, int, str]]: ...
def iter_chunks(root: str, cfg: Config) -> Iterator[Chunk]: ...
```
- `iter_files`：递归遍历，过滤扩展名、跳过 > max_file_mb、跳过隐藏目录 `.git`/`node_modules`。
- `chunk_text`：返回 `(start, end, text)`，chunk 间保留 overlap。
- 二进制检测：前 4096 字节含 `\x00` 则跳过。

### src/store.py
```python
class Store:
    def __init__(self, cfg: Config) -> None: ...
    def init_schema(self) -> None: ...
    def upsert_chunks(self, chunks: Iterable[Chunk]) -> int: ...   # 返回写入数
    def commit(self) -> None: ...
    def needs_reindex(self, path: str, mtime: float) -> bool: ...
    def all_chunks(self) -> Iterator[Chunk]: ...
    def search_vector(self, qvec: bytes, top_k: int) -> list[tuple[str, float]]: ...
```
- 表：`chunks(chunk_id PK, path, start, end, text, mtime, vec BLOB)`
- 向量以 float32 打包成 BLOB 存 SQLite，检索时在进程内做余弦相似度。

### src/search.py
```python
def embed_query(q: str, cfg: Config, store: Store) -> bytes: ...
def search(q: str, cfg: Config, store: Store, top_k: int = 10) -> list[Hit]: ...
```
- 语义 Top-N（N=top_k*4）∪ 关键词 BM25 Top-N → 融合（RRF）→ 取 top_k。
- `source` 字段标明命中来源。

### src/cli.py
```python
# 用法：
#   python -m src.cli index  <dir>
#   python -m src.cli search "<query>" [-k 10]
#   python -m src.cli stats
def main(argv: list[str] | None = None) -> int: ...
```

## 隐私自检清单（每次 PR 必须过）
- [ ] 全仓库无 `requests.post` / `urllib` / `socket` 外连（测试除外，且测试断言断网）
- [ ] `cfg.offline=True` 时，任何模型下载行为被禁止
- [ ] 没有 telemetry / 埋点 / 上报 URL
- [ ] README 明确写出"数据不出本机"
