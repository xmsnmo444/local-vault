# TASKS.md — 项目任务板（唯一调度中枢）

> 规则：Owner 在此指派任务；AI 只认领写着自己名字的任务；完成后在"汇报"区贴运行输出。
> 状态：`TODO` / `DOING` / `REVIEW` / `DONE` / `BLOCKED`

---

## 当前迭代：v0.1 — 可用的本地语义检索 MVP

**目标**：用户能对一个大目录跑 `index`，然后 `search "一句话"` 拿回相关文件片段。
**验收**：在 ≥1000 个文件的目录上，索引成功，查询返回 Top-K 且**响应 < 2s**，全程**无网络请求**。

### 任务表

| ID | 任务 | 负责人 | 改哪些文件 | 状态 | 验收标准 |
|---|---|---|---|---|---|
| T1 | 项目骨架 + 配置加载 | 小码酱 | `src/config.py` | DOING | `load_config()` 返回 dataclass，默认值可跑 |
| T2 | 文件遍历 + 切块 | 小码酱 | `src/indexer.py` | TODO | 遍历可选扩展名，按 token 切块，跳过二进制 |
| T3 | 本地嵌入 + SQLite 存储 | 小码酱 | `src/store.py` | TODO | 建表、写入、批量插入，幂等 |
| T4 | 混合检索（语义+关键词） | 小码酱 | `src/search.py` | TODO | Top-K 查询，含 BM25 关键词融合 |
| T5 | CLI 入口 | 小码酱 | `src/cli.py` | TODO | `python -m src.cli index <dir>` / `search <q>` |
| T6 | 测试 + 离线断言 | 小码酱 | `tests/test_*.py` | TODO | 有网络调用即 fail |
| T7 | README + 使用文档 | Claude | `README.md` | REVIEW | 安装、用法、隐私声明完整 |
| D1 | 架构设计文档 | Claude | `docs/DESIGN.md` | DONE | 模块图 + 数据流 + 取舍说明 |
| D2 | 索引格式契约评审 | Claude | `docs/REVIEW.md` | DONE | 审 T3/T4 的接口，指出问题 |
| I1 | GitHub Actions：离线测试 | 小码酱 | `.github/workflows/*` | TODO | CI 跑 tests 且断网 |

### 依赖关系
```
T1 ─→ T2 ─→ T3 ─→ T4 ─→ T5 ─→ T6
                        └────→ D2(Claude 审 T3/T4)
D1(Claude) 可与 T1~T4 并行
T7(Claude) 待 T5 接口稳定后写
```

---

## 契约修订申请（小码酱 → Owner/Claude）

> 依据 AGENTS.md 铁律 3「契约先行」，改动接口前必须先在此声明。
> 来源：Claude D2 预审的 7×[MAJOR]。裁决：采纳 6 项，1 项记为已知局限不修。

| # | Claude 意见 | 我的裁决 | 落地方式 |
|---|---|---|---|
| M1 | `search_vector` 返回 `list[tuple[str,float]]` 的 str 语义不明 | **采纳** | 明确为 `list[tuple[str, float]]`，str = **chunk_id**（非 path），并在契约加注释 |
| M2 | upsert 无孤儿清理，编辑后旧 chunk 残留 | **采纳（重要）** | 新增 `Store.prune_path(path)`；`upsert_chunks` 内对同一 path 先 delete 再插入 |
| M3 | `needs_reindex` 仅 mtime 有局限 | **采纳（记录局限）** | 保留 mtime 为主 + 新增 `content_hash` 列做二次校验，冲突时重索引 |
| M4 | vec BLOB 无维度元数据 | **采纳** | 新增 `meta(key,value)` 表存 `vec_dim` + `model_name`，不一致时报警并要求 reindex |
| M5 | BM25 形态未契约化 | **采纳** | 定为 **纯内存 BM25**（不用 FTS5），中文按 **字符二元 + 英文单词** 分词，逻辑全在 `store.py` |
| M6 | `chunk_text` 空文本行为未定义 | **采纳** | 契约补一句：空白/空文件 → 返回 `[]` |
| M7 | RRF k 未固定 | **采纳** | `Config.rrf_k: int = 60`，`search()` 可覆盖 |

**结论**：7 项全部处理（6 采纳 + 1 记录为已知局限）。契约文件 `docs/CONTRACTS.md` 将按上表同步修订，并请 Claude 复核。

---

## 汇报区（AI 在此贴进度 + 运行输出）

### [小码酱] 契约修订 + T1~T6 — v0.1 骨架
- 状态：DONE（待 Claude 代码级复审 D2）
- 改动：`docs/CONTRACTS.md`（按上表 7 项修订）、`src/{config,indexer,store,search,cli}.py`、`tests/{test_core,test_offline}.py`
- 验证输出：
  ```
  $ pytest tests -q
  14 passed in 0.17s

  # 端到端
  $ python -m src.cli --db vault.db index demo
  完成：索引 4 个文件 / 4 个 chunk，跳过 0，耗时 0.03s
  $ python -m src.cli --db vault.db search "逆向" -k 3
  1. [hybrid] demo/re.md:0-52  逆向工程笔记：APK 脱壳、smali、Frida hook…
  $ python -m src.cli --db vault.db search "做饭" -k 3
  1. [hybrid] demo/cook.txt:0-29  红烧肉做法：五花肉、冰糖…

  # 增量 / 孤儿清理 / 删文件
  再跑 index → 跳过 4（增量生效）
  编辑 cook.txt 后 index → 索引 1 文件 / 3 chunk，库内 4→6（旧 chunk 已清）
  删除 games.md 后 index → 清理失效 chunk：1，文件数 4→3

  # 隐私扫描
  src/*.py 外连关键字命中 = 1（仅 store.py:51 熔断常量表白名单），无主动外连
  ```
- 备注：
  - `[CRITICAL]` 离线熔断已实现：`Store.__init__` 首行 `assert_offline()`，运行时替换 `socket.connect`/`getaddrinfo` 抛 `OfflineViolation`；`close()` 时卸载。测试 4 项覆盖。
  - `[M2]` 孤儿清理：`upsert_chunks` 内先 `DELETE WHERE path=?`；另加 `prune_missing()` 清理磁盘已删文件。
  - `[M4]` meta 表存 `vec_dim`/`model_name`，不一致抛 `RuntimeError` 要求 reindex；有测试覆盖。
  - v0.1 嵌入后端为内置 `hash`（零依赖离线可跑）；`fastembed` 接口已留，换真模型只改 `cfg.embed_backend`。
  - 语义路当前为哈希嵌入，正例召回靠关键词路兜底，属 v0.1 预期；接真模型后语义质量提升。
- 待办：Claude 代码级复审（D2 续）；README（T7）；CI 离线断言（I1）

### [Claude] D1 — 架构文档
- 状态：DONE
- 产物：`docs/DESIGN.md`（v0.1 冻结版，模块图/数据流/存储模型/取舍/隐私/性能/演进）
- 备注/发现：仅基于 CONTRACTS 冻结契约撰写，未碰 src。契约修订后无需重写，已在 REVIEW 中复核一致性。
- 验证输出：纯文档交付，无 src 变更。

### [Claude] D2 — 索引格式契约评审（代码级完成 2026-10-15）
- 状态：DONE
- 产物：`docs/REVIEW.md`（代码级复审，含隐私扫描证据与分级建议）
- 备注/发现：7×MAJOR 契约项已全部落地验证（M1~M7 ✅）；2×CRITICAL 已实现（哨兵+外连扫描干净）；本轮代码级发现 0×CRITICAL、5×MAJOR、9×MINOR。P0 两项（meta存储错位、哨兵覆盖）建议合入前修，P1/P2 可作 v0.2 路标。未改 src，仅改 docs。
- 验证输出：`pytest local-vault/tests -q → 14 passed`（复跑通过）；`rg 隐私扫描 → 仅白名单常量`。

### [Claude] T7 — README + 使用文档
- 状态：REVIEW
- 产物：`README.md`（安装/快速开始/命令/隐私声明/架构简介/FAQ）
- 备注/发现：首屏粗体隐私承诺，含离线熔断、零上传、单文件 DB 说明；覆盖 v0.1 哈希嵌入取舍与 fastembed 切换路径。
- 验证输出：纯文档交付。

### [Claude] D2 — 复审回执（待小码酱处理）
- 需 Owner 裁决：是否按 REVIEW.md P0/P1 修后再合入；T7 是否直接合入。
- 给小码酱：P0-1) 修复 `src/store.py:203-222` meta 错位（model_name 存成 embed_backend）；P0-2) 补 `socket.create_connection` 哨兵 + fastembed 入口 offline 熔断。P1：哨兵引用计数 / Config exts 契约声明 / 旧库 content_hash 迁移。
- 回复 Owner 的三选一：建议 **「等 Claude 复审」→ 已完成复审，待你按 P0 修一轮后合入；README 已就绪可并行合入；接 fastembed 可作 v0.2。**

### [小码酱] 代码复审修复（P0/P1/P2）— 待 Claude 复核
- 状态：DONE（commit 33c9460）
- 改动：`src/store.py`、`src/cli.py`、`src/indexer.py`、`tests/*`、`docs/CONTRACTS.md`（未碰 `docs/DESIGN.md`）
- **P0 修复**
  1. **meta 错位**：`check_model_consistency` 改为三键 `vec_dim`/`embed_backend`/`model_name` 逐键比对；旧库缺键自动补齐。→ 新增测试 `test_model_name_change_triggers_reindex`
  2. **哨兵补全**：新增 `socket.create_connection` 拦截；`_embed_fastembed` 入口加 `if cfg.offline: raise OfflineViolation`，加载前检查本地缓存目录。→ 新增测试 `test_offline_blocks_create_connection` / `test_embed_fastembed_blocked_when_offline`
- **P1 修复**
  3. 哨兵**引用计数** `_offline_refcount`：多 Store 并存归零才卸载；`uninstall_offline(force=True)` 供测试隔离。→ 新增测试 `test_offline_refcount_multi_store`
  4. 契约补齐：`exts` 17 种 + `skip_dirs` 已在 `docs/CONTRACTS.md` 声明（[P1-4] 段）
  5. 旧库迁移：`init_schema` 加 `_migrate()`，`PRAGMA table_info` 检列缺失则 `ALTER TABLE ADD COLUMN`。→ 新增测试 `test_legacy_db_missing_content_hash_column`
- **P2 修复**
  6. 分词合并：`_tokens_for_embed` 复用 `_bm25_tokens`
  7. CLI 单次遍历；`skipped` 拆为 `未变`/`失败`；`-k`/`--rrf-k` 用 `_positive_int` 拒绝 <=0
  8. 删除死代码 `_NETWORK_MODULE_NAMES`；`$` 前缀目录过滤移除（改显式 `skip_dirs`）
- 验证输出：
  ```
  $ pytest tests -q
  19 passed in 0.27s        # 从 14 增至 19，新增 6 项覆盖 P0/P1

  $ python -m src.cli --db vault.db index demo      # 全新
  完成：索引 4 个文件 / 6 个 chunk，未变 0，失败 0，耗时 0.03s
  $ python -m src.cli --db vault.db index demo      # 增量
  完成：索引 0 个文件 / 0 个 chunk，未变 4，失败 0，耗时 0.01s
  $ python -m src.cli --db vault.db search "逆向" -k 2
  1. [hybrid] demo/re.md:0-52
  $ python -m src.cli --db vault.db search "x" -k 0
  error: argument -k: 必须 > 0，收到 0      # exit=2

  # 隐私扫描（rg）→ 零命中（死代码常量表已移除，比上轮更干净）
  ```
- 待办：Claude 复核 P0/P1；请 Claude 同步 `docs/DESIGN.md` 中 exts 数量（7→17）
- 备注：`docs/DESIGN.md` 归 Claude（单一归属），我未改。复核通过后请 Owner 合入；`fastembed` 真模型接线放 v0.2。

---

## 给 Claude 的本次任务请求（Owner 可直接转发）
```
项目 local-vault，本地优先个人数据保险箱。
请先读 AGENTS.md 与 docs/CONTRACTS.md，然后：
1) 认领 D1（写 docs/DESIGN.md）与 D2（评审 docs/REVIEW.md）。
2) 你只改 docs/ 与 README.md，不要碰 src/。
3) 完成后把产物写进文件，并在 TASKS.md 汇报区追加 [Claude] 段落。
```
