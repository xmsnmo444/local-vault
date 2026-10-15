# REVIEW.md — Claude 的代码审查记录

> 格式：`[严重级] 文件:行 — 问题 — 建议`
> 严重级：`[CRITICAL]`（隐私/数据外泄，必须立即修） / `[MAJOR]`（接口/逻辑/正确性） / `[MINOR]`（风格/性能/可观测性）
> 依据：`docs/CONTRACTS.md`（已修订，含 M1~M7）+ `AGENTS.md` 核心铁律（零上传、本地优先、单一归属）

---

## 总览（v0.1 代码级复审 2026-10-15 — 复核 rev.2，commit 33c9460）

* **审查范围**：`src/config.py` / `src/indexer.py` / `src/store.py` / `src/search.py` / `src/cli.py` + `tests/test_core.py` / `tests/test_offline.py`，初审 `3ccd1f7` → 复核 `33c9460`。
* **隐私扫描**：初审 `rg -n "requests|urllib\.request|socket\.create_connection|http\.client|telemetry|sentry|posthog|httpx\." src/` 仅命中白名单常量；复核 `rg` → **零命中**（P2-8 已删死代码），干净。
* **测试**：初审 `14 passed` → 复核 `19 passed in 0.25s`（新增 6 项：model_name 变更、create_connection 拦截、fastembed 熔断、引用计数、旧库迁移）。
* **结论**：此前 D2 预审的 **7×[MAJOR] 已全部落地**、**2×[CRITICAL] 已实现**；本轮代码级发现的 **P0/P1/P2 已全部修复**（见下方"复核"）。**复核通过，建议合入。**

---

## 1. 隐私与离线（Privacy）

### 已修复 — 确认通过

* `[CRITICAL] src/store.py:156-159 — offline 熔断已实现 — Store.__init__ 首行 assert_offline()，替换 socket.socket.connect / socket.getaddrinfo / create_connection 抛 OfflineViolation，close() 时引用计数归零才卸载。tests/test_offline.py 6 项覆盖（connect/create_connection/DNS/引用计数/卸载/可关闭）。符合 CONTRACTS 隐私自检。`
* `[CRITICAL] src/**/*.py — 全仓外连扫描 — 初审仅命中白名单常量，复核零命中。无 requests.post / urllib.request 主动调用。干净。`

### 复核：P0/P1 已全部修复 ✅（v0.2 语义修正已确认）

* `[MAJOR] src/store.py:51-77 — 哨兵未覆盖所有网络面 — ✅ 已修复（33c9460 + 33b3f84 修正）：已追加 socket.create_connection 哨兵；`_embed_fastembed` 的语义已从「offline 即熔断」修正为「offline 允许离线加载，只禁联网下载」（检查 cache_dir + HF_HUB_OFFLINE=1 强制离线），测试 test_fastembed_missing_weights_raises_not_silent_fallback 通过。`
* `[MAJOR] src/store.py:80-88 / 408-414 — 全局哨兵的引用计数缺失 — ✅ 已修复：新增 _offline_refcount，assert_offline 递增、uninstall_offline(force=False) 递减归零才卸载，force=True 供测试 autouse 强制清理；测试 test_offline_refcount_multi_store 通过。`
* `[MINOR] src/store.py:51 — _NETWORK_MODULE_NAMES 未被使用 — ✅ 已修复：已删除该死代码（P2-8）。`
* `[MINOR] src/store.py:237-250 — fastembed 首次加载未显式禁止联网 — ✅ 已修复：_embed_fastembed 检查 resolved_cache_dir，缺失报 RuntimeError 提示 fetch-model，加载时强制 HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1，不再静默联网。`

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

### 新增发现 — 复核已全部修复 ✅

* `[MAJOR] src/store.py:203-222 — meta 存储错位 — ✅ 已修复：meta 改三键 vec_dim/embed_backend/model_name 分开存储，逐键比对，旧库缺键自动补齐；测试 test_model_name_change_triggers_reindex 卡死该回归。`
* `[MAJOR] src/store.py:172-187 — content_hash 列迁移 — ✅ 已修复：init_schema 新增 _migrate()，PRAGMA table_info 检缺列补 ALTER TABLE；测试 test_legacy_db_missing_content_hash_column 通过。`
* `[MAJOR] src/config.py:13-33 / 55-69 — Config 扩展超出契约 — ✅ 已修复：docs/CONTRACTS.md 已更新 exts 17 种 + skip_dirs 字段，TASKS.md 契约修订表已声明。`
* `[MINOR] src/indexer.py:120 — iter_chunks 类型标注 — ✅ 已修复：改为 Iterator[Chunk] + TYPE_CHECKING 导入。`
* `[MINOR] src/store.py:192-196 — _meta_get 类型收窄 — ✅ 已修复：已改为三键逐一比对，缺键分支补齐，逻辑更明确。`
* `[MINOR] docs/CONTRACTS.md — content_hash 默认值说明 — ✅ 已修复：契约已补"旧库 content_hash='' 视为无需二次校验"及迁移说明。`

---

## 3. 逐文件审查

### 3.1 src/config.py — ✅ 无遗留

* `Config` 11 字段（新增 skip_dirs）均与 CONTRACTS 一致，validate 覆盖全部越界（含 overlap>=size 死循环拦截），max_file_bytes 正确。
* 环境变量 `LV_DB_PATH/LV_OFFLINE/LV_EXTS` 属便利扩展，已在 CONTRACTS 备注为非契约功能。

### 3.2 src/indexer.py — ✅ 已修复

* `[MAJOR] 过滤 "$" 前缀 — ✅ 已修复：已移除 d.startswith("$") 隐式过滤，仅保留 cfg.skip_dirs。`
* `iter_chunks` 类型标注已修正为 `Iterator[Chunk]` + `TYPE_CHECKING`。
* 空文件/二进制跳过、chunk 空白块丢弃、utf-8 容错均符合契约，v0.1 可接受。

### 3.3 src/store.py — ✅ 已修复

* `_tokens_for_embed` 已复用 `_bm25_tokens`（P2-6），不再双实现。
* 其余 `_hash_embed` 归一化、`_cosine` 双归一化、`INSERT OR REPLACE` 冗余属 P2 可选优化，不影响正确性。

### 3.4 src/search.py — ✅ 无遗留

* `hybrid` 判定、`fanout` 已在实现中保持 `top_k*4`，`search` 空查询早退正确。

### 3.5 src/cli.py — ✅ 已修复

* `[P2-7] 双重遍历 — ✅ 已修复：单次遍历 all_files 复用。`
* `[P2-7] skipped 混叠 — ✅ 已修复：拆为 skipped_uptodate / skipped_error，输出"未变/失败"。`
* `[P2-7] -k 校验 — ✅ 已修复：新增 _positive_int，拒绝 <=0。`

### 3.6 tests/ — ✅ 覆盖度提升

* `test_core.py` 新增 `test_model_name_change_triggers_reindex`、`test_legacy_db_missing_content_hash_column`。
* `test_offline.py` 新增 `test_offline_blocks_create_connection`、`test_embed_fastembed_blocked_when_offline`、`test_offline_refcount_multi_store`，并改 `force=True` 强制清理。

---

## 4. 隐私扫描证据（复核）

```
初审: rg -n "requests|urllib\.request|socket\.create_connection|..." src/
→ 仅 src/store.py:51 常量白名单，无主动外连。
复核: 同一 rg → 零命中（P2-8 已删 _NETWORK_MODULE_NAMES），更干净。
验证: pytest 19 passed，含离线哨兵 7 项全部通过。
```

---

## 5. 复核结论 — 建议合入 ✅（v0.2 更新：仍通过）

**P0 两项已修（真 bug 已堵）**

1. ✅ `meta` 三键分开存储，`model_name` 单改可触发重建（回归测试已卡死）。
2. ✅ `create_connection` 已拦截 + `fastembed` 离线加载语义已修正（见下）。

**P1/P2 已全部顺手修完（超出预期）**

3. ✅ 引用计数；4. ✅ 契约声明；5. ✅ 旧库迁移；6. ✅ 分词合并；7. ✅ CLI 单遍历+计数拆分+校验；8. ✅ 死代码删除。

**v0.2 增补**：契约语义「offline 允许离线加载，只禁联网下载」已确认正确；CI 3 个 bug 已修；`.venv` 隔离已落地。

**无新增阻塞项，建议随 v0.2 合入。**

---

## 6. 总评（复核后）

代码质量高、契约落地完整、隐私设计到位（运行时哨兵+引用计数+测试隔离可装卸是亮点）、增量/孤儿清理/维度校验/BM25/RRF 均按 7 项 MAJOR 精确实现。P0 真 bug 修复彻底，P1/P2 一并清零体现工程严谨。哈希嵌入的"先跑通再换强模型"取舍合理，v0.1 工程闭环已就绪，**可合入**。

---

## 审查历史

### 2026-10-15 — D2 契约预审

* 产物：`docs/DESIGN.md` + 本文件初版（7×MAJOR + 4×MINOR + 2×CRITICAL 待验证）

### 2026-10-15 — D2 代码级复审（初审，commit 3ccd1f7）

* 产物：本文件代码级章节（§1-§6），`README.md`（T7），`docs/DESIGN.md` v0.1
* 验证：`pytest → 14 passed`，隐私 rg 仅白名单常量
* 发现：5×MAJOR + 9×MINOR（P0/P1/P2）

### 2026-10-15 — D2 复核（本轮，commit 33c9460）

* 产物：`docs/DESIGN.md` 已同步 rev.2（exts 17 种、meta 三键、哨兵覆盖、迁移、CLI 优化）；本文件全量更新为"已修复"状态；`T7 README` 已就绪
* 验证：`pytest → 19 passed`，`rg 隐私扫描 → 零命中`
* 结论：P0/P1/P2 全部修复，复核通过，建议合入

### 2026-10-15 — v0.2 CI 设计评审 V2-3（commit 33b3f84，隔离 .venv 复核）

* 产物：`.github/workflows/ci.yml` 重写版（双 job：test 断网 + privacy 扫描）
* 验证：`.venv\Scripts\python -m pytest tests -q → 24 passed in 1.17s`（宿主直接跑会因 onnxruntime 1.31.0 segfault，必须走 .venv）
* 初版 3 个 bug 已修（确认小码酱修复正确）：
  1. ✅ `ripgrep` 未安装：已加 `sudo apt-get install -y ripgrep`（privacy job）。
  2. ✅ 测试路径 `local-vault/tests`：已改为 `tests`（仓库根即项目根）。
  3. ✅ `grep -v "_NETWORK"` 死代码：已移除（该常量 v0.1 已删，P2-8）。
* 新增正确性确认：
  * `privacy` job：`rg -n "requests\.post|urllib\.request|http\.client|httpx\.|telemetry|sentry|posthog"` 仅扫 `src/`，并排除哨兵实现行（`guarded_*`），命中即 fail；另加 `hf_hub_download|snapshot_download` 断言 `fetch-model` 为唯一联网入口 — 正确。
  * `test` job：`unshare -rn` 断网执行 `pytest`，含降级警告分支；另加 `OfflineViolation` 对 `create_connection` 的显式断言 — 正确。
  * `requirements`：`pip install pytest fastembed` 让真模型用例在 CI 走 `skip` 而非硬失败（`test_semantic.py` 模块级 `skipif`）— 正确，不依赖网络。
* 契约语义修正确认：
  * v0.1 的「offline 即熔断 fastembed」→ v0.2 的「offline 允许离线加载，只禁联网下载」**正确**。`src/store.py:304-340` 已改为检查 `cache_dir` 存在 + `HF_HUB_OFFLINE=1` 强制离线加载，缺失则提示 `fetch-model`，绝不静默降级；测试 `test_fastembed_missing_weights_raises_not_silent_fallback` / `test_fastembed_offline_load_when_weights_present` 已覆盖。
  * `CONTRACTS.md` / `config.py` 的 `resolve_embed_dim()` / `model_cache_dir` / `resolved_cache_dir` 已在本轮补声明（见 `CONTRACTS.md` v0.2 增补）。
* 编码与发布物料：`requirements.txt`（v0.1 零依赖）/`requirements-v0.2.txt`（UTF-8 已修复）/`LICENSE`（MIT）/`docs/RELEASE-v0.2.md` 已就绪；`demo/*.txt` 的 UTF-8 BOM 已去除。
* 结论：CI 设计无阻塞漏洞，**复核通过，建议随 v0.2 合入**。
