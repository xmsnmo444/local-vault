# DESIGN.md — local-vault 架构设计

> 版本：v0.1 MVP（rev.2，已同步 P0/P1 修复） | 负责人：Claude（架构/文档）| 依据：`AGENTS.md` + `docs/CONTRACTS.md`（冻结契约，含 exts 17 种 + skip_dirs + meta 三键）

## 1. 一句话定位

**local-vault** 是「本地优先、零上传」的个人数据保险箱。把散落在各处的非结构化文件（`txt/md/markdown/py/js/ts/json/csv/log/html/htm/yaml/yml/toml/ini/cfg/rst` 共 17 种纯文本扩展，未来可扩展 `.pdf` / 截图 OCR / 微信导出）做**遍历 → 二进制过滤 → 切块 → 本地嵌入 → SQLite 落盘 → 混合检索**的闭环，让用户用一句话自然语言即刻找回原文片段。

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
                    │  chunks 表 + meta 表 + vec BLOB │
                    └─────────────────┘
```

**依赖方向**：`config → indexer → store → search → cli`，单向无环。`cli` 仅编排，不持有状态。

| 模块 | 职责 | 输入 | 输出 | 允许依赖 |
|---|---|---|---|---|
| `config` | 配置加载 | `**overrides` | `Config` dataclass | 无 |
| `indexer` | 遍历与切块 | `root, cfg` | `Iterator[Chunk]` | `config` |
| `store` | 嵌入与持久化 | `Chunk` | `chunks` + `meta` 表 | `config` |
| `search` | 混合检索 | `q: str` | `list[Hit]` | `config`, `store` |
| `cli` | 命令行 | `argv` | `exit code` | 全部 |

---

## 3. 数据流

### 3.1 索引链路 `index <dir>`

```
dir ── iter_files(cfg.exts 17种, max_file_mb, 跳过 cfg.skip_dirs[.git/node_modules/__pycache__/.venv/dist/build/.idea/.vscode 等], 二进制检测 \x00)
      ── 读文件 (utf-8, errors=ignore, 前 4096 字节探针)
      ── chunk_text(cfg.chunk_size=512, overlap=64) → list[(start,end,text)]（空/全空白→[]）
      ── 组装 Chunk(chunk_id=sha1(f"{abs_path}:{start}:{end}").hexdigest() 40位, path, mtime, content_hash=sha1(text))
      ── Store.needs_reindex(path, mtime, content_hash)? ──否──▶ 跳过
      │                                              ──是──▶ 本地模型嵌入 (hash 256d / fastembed 可选, batch=32)
      │                                                     float32 → bytes (BLOB, 已归一化)
      └───────────────────────────────────────────────────── Store.upsert_chunks()先按 path 清孤儿再批量插入 → commit() + prune_missing()
```

* 幂等：`chunk_id` 稳定，重复 `index` 为 upsert，不产生重复行；同 path 先 `DELETE WHERE path=?` 再插入，编辑后旧 chunk 不残留。
* 增量：`needs_reindex` 以 `mtime` 为主探针 + `content_hash` 二次校验，`mtime` 被篡改或同 `mtime` 不同内容可被捕捉。
* 离线：`cfg.offline=True` 时，`Store.__init__` 首行 `assert_offline()` 安装哨兵（拦截 `socket.connect` / `getaddrinfo` / `create_connection`，引用计数归零才卸载），`_embed_fastembed` 入口直接熔断，禁止任何 `hf_hub_download` 网络回退。

### 3.2 查询链路 `search "<q>" [-k 10]`

```
q ── embed_query(q, cfg, store) → qvec: bytes (float32, 已归一化)
  ├─ 语义分支：Store.search_vector(qvec, N=top_k*4) → 进程内余弦相似度 Top-N（已归一化则退化为点积）
  ├─ 关键词分支：Store.search_keyword(q, N) → 纯内存 BM25（中文按字符二元、英文按词）Top-N
  └─ 融合：RRF (Reciprocal Rank Fusion, k=cfg.rrf_k=60 可覆盖) → 融合分数排序 → 取 top_k → list[Hit{source}]
```

`source` 标明 `"semantic" | "keyword" | "hybrid"`，便于排错与可解释性。

### 3.3 存储模型

```sql
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id     TEXT PRIMARY KEY,  -- sha1(path:start:end).hexdigest() 40位
  path         TEXT NOT NULL,
  start        INTEGER NOT NULL,
  end          INTEGER NOT NULL,
  text         TEXT NOT NULL,
  mtime        REAL NOT NULL,
  content_hash TEXT NOT NULL DEFAULT '', -- sha1(整文件内容)，供 needs_reindex 二次校验；旧库缺列时自动 ALTER TABLE 迁移
  vec          BLOB NOT NULL       -- float32 LE 打包，维度 = cfg.embed_dim（hash 256 / bge-small 384）
);
CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL              -- 存 vec_dim / embed_backend / model_name / schema_ver
);
```

* 向量不建外部索引，v0.1 在进程内做 `cosine = dot(a,b)/(||a||·||b||)` 全扫；1000 文件 × 平均 5 chunks ≈ 5000 向量，256 维 float32，全扫 < 200ms，满足验收 `<2s`。
* 旧库升级：`init_schema` 内 `_migrate()` 用 `PRAGMA table_info` 检测列缺失自动补列；`check_model_consistency()` 对 `vec_dim` / `embed_backend` / `model_name` 三键逐一比对，不一致抛 `RuntimeError` 提示删库重建，旧库缺键自动补齐。
* `content_hash=''` 的旧行为空视为无需二次校验，保持兼容。
* 未来数据量 > 5 万 chunks 再引入 `sqlite-vec` / `hnswlib` / `usearch`。

---

## 4. 关键技术取舍与理由

| 决策 | 选项A（采用） | 选项B（放弃） | 理由 |
|---|---|---|---|
| **向量库** | SQLite BLOB + 内存余弦 | Milvus / Chroma / pgvector | 零依赖、单文件可备份、离线可用；MVP 数据量下全扫足够快，引入外部服务违背"本地单文件"定位 |
| **嵌入模型** | `hash` 256d（v0.1 默认，零依赖）/ `BAAI/bge-small-zh-v1.5` 384d（fastembed 可选） | `bge-large` / OpenAI API | 小模型 CPU 可跑、中文优化、离线权重可分发；large 显存/内存翻倍，API 违背零上传；v0.1 先以 hash 让管线在任何机器上跑通 |
| **切块单位** | 字符近似 token (512/64)，纯空白块丢弃不落库 | 精确 tokenizer 切分 | 避免引入 `tiktoken`/`transformers` tokenizer 的额外依赖与速度损耗；512 字符≈ 300–500 token，对检索召回影响可接受，后续可加可选精确模式 |
| **检索融合** | RRF (k=60) 融合语义+BM25 | 线性加权 / 仅语义 | RRF 对分数尺度不敏感，无需归一化调参，鲁棒；关键词分支补足专有名词/错误别字的召回 |
| **增量策略** | `mtime` 主探针 + `content_hash` 二次校验 | 纯 mtime / 纯内容 hash | `mtime` O(1) 且无需读文件；`content_hash` 兜底篡改与同 mtime 不同内容，兼顾速度与正确性 |
| **离线强制** | `cfg.offline=True` 运行时熔断（socket 哨兵+引用计数+fastembed 入口熔断） | 仅文档约定 | 代码层 `if cfg.offline: 禁止 socket/requests/hf_download` + 测试层"有网络调用即 fail"，双重保障 |
| **并发** | 单进程串行索引，单次遍历复用 | 多进程/多线程 / 双重遍历 | 简化 SQLite 写入与模型并发；瓶颈在嵌入，v0.1 用 `embed_batch=32` 已足够，后续再加 `ProcessPool` |

---

## 5. 隐私与安全设计

1. **零上传不变量**：`rg -n "requests\.post\|urllib\.request\|socket\.create" src/` 在 CI 中必须为 0（测试除外）。`cfg.offline=True` 时，`store.py` 哨兵拦截 `socket.connect` / `getaddrinfo` / `create_connection`，引用计数归零才卸载，`_embed_fastembed` 入口直接抛 `OfflineViolation`。
2. **数据不出本机声明**：`README.md` 首屏以粗体声明，所有 `docs/**` 重申。
3. **无遥测**：禁止 `telemetry`、`analytics`、`sentry`、`posthog` 等任何埋点；`REVIEW.md` 中 `[CRITICAL]` 一票否决。
4. **文件读取最小化**：`iter_files` 跳过 `>max_file_mb`、隐藏目录（`cfg.skip_dirs`）、二进制文件，不读取系统敏感路径（后续可加 allowlist）；`followlinks=False` 防环。
5. **数据库本地性**：`vault.db` 默认为项目根相对路径，不做任何云同步；用户自行决定备份位置。

详见 `docs/CONTRACTS.md` 的"隐私自检清单"，每次 PR 必过。

---

## 6. 性能与容量估算

* **索引**：1000 文件 × 平均 20KB ≈ 20MB 文本 → 约 4000–6000 chunks；`bge-small` CPU (ONNX) 约 150–300 chunks/s → 20–40s 完成首建；增量二次索引仅处理变更文件，秒级；CLI 单次遍历复用避免双倍 I/O。
* **查询**：`search_vector` 全扫 5000×256 点积 ≈ 1.3M FLOPs，Python < 100ms；BM25 内存倒排 < 50ms；RRF 融合 O(N log N) 可忽略；端到端 < 500ms，满足 `<2s`。
* **存储**：每 chunk `vec 256*4=1024 B` + `text ~512 字符` → 约 1.5KB/chunk → 5000 chunks ≈ 7.5MB 数据库，单文件可随身携带。

---

## 7. 错误处理与边界

* **编码**：`open(..., encoding="utf-8", errors="ignore")`，坏行不中断遍历。
* **权限**：`iter_files` 捕获 `PermissionError` 跳过并 `log.warning`，不中断全量。
* **符号链接**：默认不跟随 `followlinks=False`，防环。
* **大文件**：`>max_file_mb` 直接跳过。
* **二进制**：前 4096 字节含 `\x00` 判二进制，跳过。
* **空文本**：`chunk_text` 对空/全空白返回 `[]`，纯空白块不落库。
* **参数校验**：`Config.validate()` 拦截 `overlap>=size` 死循环；CLI `-k`/`--rrf-k` 用 `_positive_int` 拒绝 `<=0`。
* **空目录/零命中**：`search` 返回 `[]`，`cli` 打印友好提示而非栈追踪；`skipped` 拆为 `未变`/`失败` 分别计数。

---

## 8. 未来演进（非 v0.1）

* 精确 tokenizer 切块（可选 `--precise`）。
* `sqlite-vec` 或 `hnswlib` ANN 索引，支持 10 万+ chunks。
* 截图 OCR（`paddleocr` 本地）与 PDF 解析（`pymupdf`）。
* 文件监听 `watchdog` 增量自动索引。
* TUI / 本地 Web UI（仍零上传，`localhost`  only）。
* 接 `fastembed` 真模型（已预留 `cfg.embed_backend`，本地权重可分发）。

---

## 9. 校验

* 本文档已同步至 `docs/CONTRACTS.md` 修订版（exts 17 种、`skip_dirs`、`content_hash` 迁移、`meta` 三键、`cli` 单遍历与参数校验）。
* `docs/REVIEW.md` 已完成代码级复审及 P0/P1/P2 修复复核，重点核查零上传与接口一致性。
