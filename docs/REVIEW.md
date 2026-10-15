# REVIEW.md — Claude 的代码审查记录

> 格式：`[严重级] 文件:行 — 问题 — 建议`
> 严重级：`[CRITICAL]`（隐私/数据外泄，必须立即修） / `[MAJOR]`（接口/逻辑/正确性） / `[MINOR]`（风格/性能/可观测性）
> 依据：`docs/CONTRACTS.md`（已修订，含 M1~M7）+ `AGENTS.md` 核心铁律（零上传、本地优先、单一归属）

---

## 总览（v0.1 代码级复审 2026-10-15）

* **审查范围**：`src/config.py` / `src/indexer.py` / `src/store.py` / `src/search.py` / `src/cli.py` + `tests/test_core.py` / `tests/test_offline.py`，commit `3ccd1f7`。
* **隐私扫描**：`rg -n "requests|urllib\.request|socket\.create_connection|http\.client|telemetry|sentry|posthog|httpx\." src/` 仅命中 `src/store.py:51` 的白名单常量，无主动外连。干净。
* **测试**：`pytest local-vault/tests -q` → `14 passed in 0.18s` 已验证。
* **结论**：此前 D2 预审的 **7×[MAJOR] 已全部落地**、**2×[CRITICAL] 已实现**。本轮代码级复审未发现新的 `[CRITICAL]` 外泄；发现 0 个新增 `[CRITICAL]`、5 个 `[MAJOR]`、9 个 `[MINOR]`。整体已达到 v0.1 可用标准，建议按 `[MAJOR]` 修一轮后再合入。

---

## 1. 隐私与离线（Privacy）

### 已修复 — 确认通过

* `[CRITICAL] src/store.py:156-159 — offline 熔断已实现 — Store.__init__ 首行 assert_offline()，替换 socket.socket.connect / socket.getaddrinfo 抛 OfflineViolation，close() 时卸载。tests/test_offline.py 4 项覆盖（connect/DNS/卸载/可关闭）。符合 CONTRACTS 隐私自检。`
* `[CRITICAL] src/**/*.py — 全仓外连扫描 — 唯一命中 src/store.py:51 的 _NETWORK_MODULE_NAMES 白名单常量，无 requests.post / urllib.request / socket.create_connection 主动调用。干净。`

### 遗留加固（非外泄，但建议补强）

* `[MAJOR] src/store.py:51-77 — 哨兵未覆盖所有网络面 — 仅替换 socket.socket.connect + getaddrinfo，能拦截 TCP/DNS，但未拦截 socket.create_connection、ssl.wrap_socket、subprocess 触发的 curl/wget、以及少数库的直接 syscalls。建议：① 追加 socket.create_connection 哨兵；② 在 _embed_fastembed() 入口加 if cfg.offline: raise OfflineViolation("offline: fastembed disabled")，防止未来 fastembed 静默联网下载。`
* `[MAJOR] src/store.py:80-88 / 408-414 — 全局哨兵的引用计数缺失 — 多 Store 并存时，第一个 Store 关闭即 uninstall_offline()，会把第二个仍需保护的 Store 的哨兵卸掉。建议加 _offline_refcount 计数，归零时才卸载；或在 Store.__exit__ 仅当无其他活跃 Store 时卸载。tests/test_offline.py 的 autouse fixture 已用 uninstall_offline() 兜底，生产环境仍有竞态。`
* `[MINOR] src/store.py:51 — _NETWORK_MODULE_NAMES 未被使用 — 定义后从未引用，属死代码。建议删除或用于启动时的 import 黑名单检测（sys.modules 扫描）。`
* `[MINOR] src/store.py:237-250 — fastembed 首次加载未显式禁止联网 — TextEmbedding(model_name=...) 在无本地权重时会尝试 hf_hub_download。建议在调用前检查本地权重目录是否存在，不存在直接抛错并提示离线下载方式，而非让其自行联网。`

---

## 2. 契约一致性（Contract Conformance）

### 逐项核验 M1~M7

| 项 | 契约 | 代码 | 结论 |
|---|---|---|---|
| M1 search_vector 语义 | `list[tuple[str,float]]` str=chunk_id | `src/store.py:339` 注释 `[M1] 返回 (chunk_id, score)`，实现遍历 `SELECT chunk_id, vec` | ✅ 通过 |
| M2 孤儿清理 | upsert 先 delete by path + prune_path | `src/store.py:252-292` 先 `DELETE WHERE path=?` 再批量 INSERT，`prune_missing()` 供 CLI 调用 | ✅ 通过 |
| M3 content_hash 二次校验 | Chunk.content_hash + needs_reindex 二参 | `src/store.py:310-323` 已实现，`src/indexer.py:120-138` 组装 | ✅ 通过 |
| M4 维度/模型一致性 | meta 表存 vec_dim/model_name | `src/store.py:192-222` 已实现，但见下方 MAJOR | ⚠️ 见 MAJOR |
| M5 BM25 形态 | 纯内存 BM25，字符二元+英文词 | `src/store.py:349-389` + `_bm25_tokens` | ✅ 通过 |
| M6 空文本 | chunk_text 空/全空白→[] | `src/indexer.py:91-104` `if not text.strip(): return []` 且丢弃纯空白块 | ✅ 通过 |
| M7 RRF k | Config.rrf_k=60，可覆盖 | `src/config.py:67` + `src/search.py:47-59` `rrf_k` 参数 | ✅ 通过 |

### 新增发现

* `[MAJOR] src/store.py:203-222 — meta 存储错位：model_name 存的是 embed_backend 而非 model_name — 第 205 行 stored_model = _meta_get("model_name") 对应写入的是 self.cfg.embed_backend（"hash"/"fastembed"），而 cfg.model_name（"BAAI/bge-small-zh-v1.5"）被忽略。后果：切换 model_name 但保持 backend 不变不会触发重建提示，导致旧向量与新模型混用。建议：meta 拆为 vec_dim + embed_backend + model_name 三键，或至少把 model_name 一并存入并校验。`
* `[MAJOR] src/store.py:172-187 — meta 缺少 content_hash 列迁移 — SCHEMA_VER="1" 已写入但从未校验；若用户从旧库（无 content_hash 列）升级，SELECT content_hash 会因列不存在抛错。建议在 init_schema 后做 PRAGMA table_info 兼容列缺失的 ALTER TABLE ADD COLUMN。当前全新库无问题，升级路径有坑。`
* `[MAJOR] src/config.py:13-33 / 55-69 — Config 扩展超出契约 — CONTRACTS 约定 exts 7 种，代码 DEFAULT_EXTS 17 种（新增 .markdown/.js/.ts/.htm/.yaml/.yml/.toml/.ini/.cfg/.rst），另新增 skip_dirs: frozenset[str] 字段。属正向扩展但未在 CONTRACTS 声明，违反"改签名前先在 TASKS.md 声明"。建议：要么回缩至契约 7 种，要么在 TASKS.md 契约修订表追加一行并更新 CONTRACTS.md 的 exts 列表。`
* `[MINOR] src/indexer.py:120 — iter_chunks 类型标注 Iterator["object"] — 实际 yield Chunk，与契约 Iterator[Chunk] 不符。建议改为 Iterator[Chunk] 并 from .store import Chunk 顶层导入（或 TYPE_CHECKING）。`
* `[MINOR] src/store.py:192-196 — _meta_get 返回 str|None 但调用方直接与 str 比较 — check_model_consistency 中 stored_dim 与 str(dim) 比较，若 meta 缺失会走首次写入分支，逻辑正确但类型收窄可更明确。`
* `[MINOR] docs/CONTRACTS.md — Chunk.content_hash 注释缺失默认值说明 — 代码侧 DEFAULT ''，契约未说明旧库迁移时 content_hash 为空的语义，needs_reindex 中 stored_hash 为空时跳过二次校验的分支（311-322）与契约描述一致但契约未文字化。建议契约补一句"旧库 content_hash='' 视为无需二次校验"。`

---

## 3. 逐文件审查

### 3.1 src/config.py

* `[MINOR] src/config.py:58-69 — Config 默认值与契约一致 — 9 字段均对齐（rrf_k/embed_backend/embed_dim 已按 M7/M4 追加），validate 覆盖全部越界（含 overlap>=size 死循环拦截），max_file_bytes 属性正确。无问题。`
* `[MINOR] src/config.py:100-135 — load_config 环境变量 LV_DB_PATH/LV_OFFLINE/LV_EXTS 未契约化 — 属便利扩展，未声明但不破坏契约。建议在 CONTRACTS.md 或 README 中文档化，或在 TASKS.md 备注为"非契约扩展"。`
* `[MINOR] src/config.py:115-123 — LV_EXTS 解析容错 — 空字符串、重复逗号会被过滤，已处理；但 "md" 自动补 "." 的逻辑与 ext 统一小写一致，行为正确。`

### 3.2 src/indexer.py

* `[MAJOR] src/indexer.py:43 — 过滤 "$" 前缀目录未契约化 — dirnames[:] 过滤 d.startswith("$") 会跳过 $RECYCLE.BIN 等，但契约仅约定跳过 SKIP_DIRS。虽无害但属隐式行为，建议移入 SKIP_DIRS 显式集合或在契约中说明。`
* `[MINOR] src/indexer.py:50-64 — 空文件跳过策略 — st.st_size==0 直接 continue，与 chunk_text 返回 [] 语义重复但一致，不影响正确性；stats 中无法区分"空文件跳过"与"二进制跳过"，可观测性可加强。`
* `[MINOR] src/indexer.py:80-88 — read_text 固定 utf-8 errors=ignore — 符合契约"坏字节忽略"，但对 GBK 编码的中文笔记会有乱码。v0.1 可接受，README 应提示"仅保证 utf-8 可读，其余尽力而为"。`
* `[MINOR] src/indexer.py:99-107 — chunk_text 丢弃纯空白块 — if piece.strip() 才 append，但 start 仍按 step 前进，会导致空白段对应的 chunk_id 空洞。正确且不影响召回，但与契约"chunk 间保留 overlap" 的字面描述有细微偏差，建议契约补"纯空白块不落库"一句。`

### 3.3 src/store.py

* `[MINOR] src/store.py:91-129 — _hash_embed / _tokens_for_embed 确定性与归一化 — 已 L2 归一化，cosine 可退化为点积；但 _cosine 仍做双重归一化（136-152），虽数值稳定但多一次 sqrt，5000 向量规模可忽略，10万规模可优化为 dot。`
* `[MINOR] src/store.py:252-290 — upsert 用 INSERT OR REPLACE 在 DELETE 后多余 — 已先 DELETE，INSERT 即可；OR REPLACE 无害但语义冗余。`
* `[MINOR] src/store.py:339-347 — search_vector 全扫 — v0.1 数据量下满足 <2s，但未做 top_k==0 的早退；传入空 qvec 时 _unpack 长度 0 会与存储向量维度不匹配，_cosine 返回 0 而非抛错，行为宽松可接受。`
* `[MINOR] src/store.py:426-438 — _bm25_tokens 与 _tokens_for_embed 分词双实现 — 两处分别实现 CJK 二元+英文词，逻辑重复。建议抽为单一 _tokenize_cjk_bigram() 复用，避免后续分词策略漂移。`

### 3.4 src/search.py

* `[MINOR] src/search.py:47-73 — search 的 hybrid 判定依赖 set — sem_set/kw_set 为 id 集合，_source 正确标记 hybrid；但当两路召回均为空时 fused 为 {}，返回 [] 符合预期。`
* `[MINOR] src/search.py:85-100 — N+1 查询 — 每命中一次 get_chunk_text + SELECT path，可合并为单次 SELECT text,start,end,path。性能影响仅在 k=10 时可忽略，但值得在注释中说明。`
* `[MINOR] src/search.py:59 — fanout = max(top_k*4, top_k) — top_k*4 已 >= top_k，max 多余，直接 top_k*4 即可。`

### 3.5 src/cli.py

* `[MINOR] src/cli.py:23-88 — cmd_index 双重遍历 — existing_now 与主循环各调一次 iter_files，对 1000 文件目录会双倍 I/O。建议复用第一次遍历结果列表，避免二次 walk。`
* `[MINOR] src/cli.py:56-60 — 增量计数 skipped 语义混叠 — needs_reindex==False 与 OSError 均计入 skipped，无法区分"增量命中"与"读取失败"。建议拆为 skipped_uptodate / skipped_error 并在 CLI 输出中分别展示。`
* `[MINOR] src/cli.py:99-125 — cmd_search 的 rrf_k 透传 — load_config(db_path=..., rrf_k=args.rrf_k) 与 search(..., rrf_k=...) 双重传递，实际以 search 的 rrf_k 为准，Config 的 rrf_k 冗余但不冲突。`
* `[MINOR] src/cli.py:146-180 — 参数校验 — -k 未校验 >0，传入 0 或负数会触发 fanout 异常或返回空。建议在 build_parser 中加 type=positive_int 校验。`

### 3.6 tests/

* `tests/test_core.py` 覆盖 chunk 空文本、overlap 步进、iter_files 过滤、upsert 孤儿清理、needs_reindex 双因子、维度不一致、search 命中与空查询 — 覆盖度良好。
* `tests/test_offline.py` 覆盖 connect/DNS 拦截、close 后卸载、offline=False 不拦截 — 已解决此前"哨兵卸不掉导致污染"的问题。

---

## 4. 隐私扫描证据

```
rg -n "requests|urllib\.request|socket\.create_connection|http\.client|telemetry|sentry|posthog|httpx\." src/
→ 仅 src/store.py:51 常量白名单，无主动外连。

rg -n "OfflineViolation|assert_offline|guarded_connect|guarded_getaddrinfo" src/
→ src/store.py:47,54,67,72,80 仅哨兵实现，无外泄路径。
```

---

## 5. 建议优先级

**P0（合入前修）**

1. 修复 meta 存储错位（store.py:203-222，model_name vs embed_backend）。
2. 补全离线哨兵：拦截 socket.create_connection，并在 _embed_fastembed 入口加 offline 熔断。

**P1（建议本迭代修）**

3. 多 Store 哨兵引用计数。
4. Config exts / skip_dirs 的契约声明补齐（或回缩）。
5. 旧库 content_hash 列迁移兼容。

**P2（可下迭代）**

6. 合并 _bm25_tokens 与 _tokens_for_embed。
7. CLI 双重遍历与 skipped 计数拆分。
8. 移除 _NETWORK_MODULE_NAMES 死代码或转为真实检测。

---

## 6. 总评

代码质量高、契约落地完整、隐私设计到位（运行时哨兵+测试隔离可装卸是亮点）、增量/孤儿清理/维度校验/BM25/RRF 均按 7 项 MAJOR 精确实现。哈希嵌入的"先跑通再换强模型"取舍合理，v0.1 的工程闭环已就绪。按 P0 两项修完即可合入，P1/P2 可作为 v0.2 路标。

---

## 审查历史

### 2026-10-15 — D2 契约预审

* 产物：`docs/DESIGN.md` + 本文件初版（7×MAJOR + 4×MINOR + 2×CRITICAL 待验证）

### 2026-10-15 — D2 代码级复审（本轮）

* 产物：本文件代码级章节（§1-§6），`README.md`（T7），`docs/DESIGN.md` 维持 v0.1 版
* 验证：`pytest local-vault/tests -q → 14 passed`（复跑通过），隐私 rg 扫描干净
* 下一步：小码酱按 P0/P1 修 → Claude 复核 → Owner 合入
