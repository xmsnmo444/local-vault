# DESIGN.md — local-vault 架构设计

> 版本：v0.1 MVP | 负责人：Claude（架构/文档）| 依据：`AGENTS.md` + `docs/CONTRACTS.md`（冻结契约）

## 1. 一句话定位

**local-vault** 是「本地优先、零上传」的个人数据保险箱。把散落在各处的非结构化文件（`.txt` / `.md` / `.py` / `.json` / `.csv` / `.log` / `.html`、未来可扩展 `.pdf` / 截图 OCR / 微信导出）做**遍历 → 二进制过滤 → 切块 → 本地嵌入 → SQLite 落盘 → 混合检索**的闭环，让用户用一句话自然语言即刻找回原文片段。

**隐私红线**：全链路离线可跑，默认 `cfg.offline=True`，任何代码路径不得把用户文本发往网络。文档、代码、CI 均以此为第一优先级。

---

## 2. 模块图

```
                    ┌─────────────────────┐
                    │   src/config.py     │  无依赖，单一真相源
                    │   Config / load_config│
                    └────────┬────────────┘
                             │ cfg
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
┌───────────────┐   ┌────────────────┐   ┌──────────────┐
│ src/indexer.py│   │  src/store.py  │   │ src/search.py│
│ 遍历 + 切块   │──▶│ 嵌入 + SQLite  │◀──│ 混合检索     │
│ iter_files    │   │ Store / vec    │   │ embed_query  │
│ chunk_text    │   │ upsert/commit  │   │ search (RRF) │
│ iter_chunks   │   │ search_vector  │   │              │
└───────┬───────┘   └───────┬────────┘   └──────┬───────┘
        │ Chunk 流          │ Hit/Top-K         │ Hit[]
        └───────────────────┴───────────────────┘
                             │
                    ┌────────▼────────┐
                    │   src/cli.py    │  只做编排，不含业务逻辑
                    │  index/search/  │
                    │  stats          │
                    └─────────────────┘
                             │
                    ┌────────▼────────┐
                    │  vault.db (SQLite) │
                    │  chunks 表 + vec BLOB │
                    └─────────────────┘
```

**依赖方向**：`config → indexer → store → search → cli`，单向无环。`cli` 仅编排，不持有状态。

| 模块 | 职责 | 输入 | 输出 | 允许依赖 |
|---|---|---|---|---|
| `config` | 配置加载 | `**overrides` | `Config` dataclass | 无 |
| `indexer` | 遍历与切块 | `root, cfg` | `Iterator[Chunk]` | `config` |
| `store` | 嵌入与持久化 | `Chunk` | `chunks` 表 | `config` |
| `search` | 混合检索 | `q: str` | `list[Hit]` | `config`, `store` |
| `cli` | 命令行 | `argv` | `exit code` | 全部 |

---

## 3. 数据流

### 3.1 索引链路 `index <dir>`

```
dir ── iter_files(cfg.exts, max_file_mb, 跳过 .git/node_modules, 二进制检测 \x00)
      ── 读文件 (utf-8, errors=ignore, 前 4096 字节探针)
      ── chunk_text(cfg.chunk_size=512, overlap=64) → list[(start,end,text)]
      ── 组装 Chunk(chunk_id=sha1(f"{abs_path}:{start}:{end}"), path, mtime)
      ── Store.needs_reindex(path, mtime)? ──否──▶ 跳过
      │                                   ──是──▶ 本地模型嵌入 (bge-small-zh, batch=32)
      │                                          float32 → bytes (BLOB)
      └────────────────────────────────────────── Store.upsert_chunks() → commit()
```

* 幂等：`chunk_id` 稳定，重复 `index` 为 upsert，不产生重复行。
* 增量：`needs_reindex` 以 `mtime` 为探针，命中则跳过已索引文件（见 §5 取舍）。
* 离线：`cfg.offline=True` 时，模型仅从本地权重加载，禁止任何 `hf_hub_download` 网络回退。

### 3.2 查询链路 `search "<q>" [-k 10]`

```
q ── embed_query(q, cfg, store) → qvec: bytes (float32, 已归一化)
  ├─ 语义分支：Store.search_vector(qvec, N=top_k*4) → 进程内余弦相似度 Top-N
  ├─ 关键词分支：SQLite BM25（或内存 BM25）对 chunks.text 做 Top-N
  └─ 融合：RRF (Reciprocal Rank Fusion, k=60) → 融合分数排序 → 取 top_k → list[Hit{source}]
```

`source` 标明 `"semantic" | "keyword" | "hybrid"`，便于排错与可解释性。

### 3.3 存储模型

```sql
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY,  -- sha1(path:start:end)
  path     TEXT NOT NULL,
  start    INTEGER NOT NULL,
  end      INTEGER NOT NULL,
  text     TEXT NOT NULL,
  mtime    REAL NOT NULL,
  vec      BLOB NOT NULL       -- float32 LE 打包，维度 = 模型隐层 (bge-small 为 384)
);
CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
```

* 向量不建外部索引，v0.1 在进程内做 `cosine = dot(a,b)/(||a||·||b||)` 全扫；1000 文件 × 平均 5 chunks ≈ 5000 向量，384 维 float32，全扫 < 200ms，满足验收 `<2s`。
* 未来数据量 > 5 万 chunks 再引入 `sqlite-vec` / `hnswlib` / `usearch`。

---

## 4. 关键技术取舍与理由

| 决策 | 选项A（采用） | 选项B（放弃） | 理由 |
|---|---|---|---|
| **向量库** | SQLite BLOB + 内存余弦 | Milvus / Chroma / pgvector | 零依赖、单文件可备份、离线可用；MVP 数据量下全扫足够快，引入外部服务违背"本地单文件"定位 |
| **嵌入模型** | `BAAI/bge-small-zh-v1.5` (384d) | `bge-large` / OpenAI API | 小模型 CPU 可跑、中文优化、离线权重可分发；large 显存/内存翻倍，API 违背零上传 |
| **切块单位** | 字符近似 token (512/64) | 精确 tokenizer 切分 | 避免引入 `tiktoken`/`transformers` tokenizer 的额外依赖与速度损耗；512 字符≈ 300–500 token，对检索召回影响可接受，后续可加可选精确模式 |
| **检索融合** | RRF (k=60) 融合语义+BM25 | 线性加权 / 仅语义 | RRF 对分数尺度不敏感，无需归一化调参，鲁棒；关键词分支补足专有名词/错误别字的召回 |
| **增量策略** | `mtime` 探针 | 内容 hash / 逐 chunk hash | `mtime` O(1) 且无需读文件；hash 需全量读，1000 文件场景 I/O 成本高。v0.1 接受"mtime 被篡改"的极小误判 |
| **离线强制** | `cfg.offline=True` 熔断网络 | 仅文档约定 | 代码层 `if cfg.offline: 禁止 socket/requests/hf_download` + 测试层"有网络调用即 fail"，双重保障 |
| **并发** | 单进程串行索引 | 多进程/多线程 | 简化 SQLite 写入与模型并发；瓶颈在嵌入，v0.1 用 `embed_batch=32` 已足够，后续再加 `ProcessPool` |

---

## 5. 隐私与安全设计

1. **零上传不变量**：`grep -R "requests\.post\|urllib\.request\|socket\.create" src/` 在 CI 中必须为 0（测试除外）。`cfg.offline=True` 时，`store.py` 中任何模型加载路径若触发 `urllib`/`http` 立即抛 `RuntimeError("offline mode: network disabled")`。
2. **数据不出本机声明**：`README.md` 首屏以粗体声明，所有 `docs/**` 重申。
3. **无遥测**：禁止 `telemetry`、`analytics`、`sentry`、`posthog` 等任何埋点；`REVIEW.md` 中 `[CRITICAL]` 一票否决。
4. **文件读取最小化**：`iter_files` 跳过 `>max_file_mb`、隐藏目录、二进制文件，不读取系统敏感路径（后续可加 allowlist）。
5. **数据库本地性**：`vault.db` 默认为项目根相对路径，不做任何云同步；用户自行决定备份位置。

详见 `docs/CONTRACTS.md` 的"隐私自检清单"，每次 PR 必过。

---

## 6. 性能与容量估算

* **索引**：1000 文件 × 平均 20KB ≈ 20MB 文本 → 约 4000–6000 chunks；`bge-small` CPU (ONNX) 约 150–300 chunks/s → 20–40s 完成首建；增量二次索引仅处理变更文件，秒级。
* **查询**：`search_vector` 全扫 5000×384 点积 ≈ 2M FLOPs，Python + numpy < 100ms；BM25 内存倒排 < 50ms；RRF 融合 O(N log N) 可忽略；端到端 < 500ms，满足 `<2s`。
* **存储**：每 chunk `vec 384*4=1536 B` + `text ~512 字符` → 约 2KB/chunk → 5000 chunks ≈ 10MB 数据库，单文件可随身携带。

---

## 7. 错误处理与边界

* **编码**：`open(..., encoding="utf-8", errors="ignore")`，坏行不中断遍历。
* **权限**：`iter_files` 捕获 `PermissionError` 跳过并 `log.warning`，不中断全量。
* **符号链接**：默认不跟随 `followlinks=False`，防环。
* **大文件**：`>max_file_mb` 直接跳过并计入 `stats.skipped_large`。
* **二进制**：前 4096 字节含 `\x00` 判二进制，跳过。
* **空目录/零命中**：`search` 返回 `[]`，`cli` 打印友好提示而非栈追踪。

---

## 8. 未来演进（非 v0.1）

* 精确 tokenizer 切块（可选 `--precise`）。
* `sqlite-vec` 或 `hnswlib` ANN 索引，支持 10 万+ chunks。
* 截图 OCR（`paddleocr` 本地）与 PDF 解析（`pymupdf`）。
* 文件监听 `watchdog` 增量自动索引。
* TUI / 本地 Web UI（仍零上传，`localhost`  only）。

---

## 9. 校验

* 本文档仅依赖 `docs/CONTRACTS.md` 冻结接口，未改动任何 `src/**`。
* 后续 `docs/REVIEW.md` 将对 `src/store.py` 与 `src/search.py` 的实现做 `[CRITICAL]/[MAJOR]/[MINOR]` 分级审查，重点核查零上传与接口一致性。
