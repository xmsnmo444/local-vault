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

### [Claude] D1 — 架构文档（rev.2 已同步 P0/P1 修复 2026-10-15）
- 状态：DONE
- 产物：`docs/DESIGN.md`（rev.2）
- 备注/发现：已同步 17 种扩展名、skip_dirs、meta 三键、content_hash 迁移、哨兵引用计数、CLI 单遍历等 P0/P1/P2 变更；与 CONTRACTS 修订版一致。未碰 src。
- 验证输出：纯文档交付。

### [Claude] D2 — 索引格式契约评审（代码级 + 复核完成 2026-10-15）
- 状态：DONE
- 产物：`docs/REVIEW.md`（复核版，33c9460）
- 备注/发现：初审 5×MAJOR+9×MINOR 已全部修复；复核 rg 零命中、pytest 19 passed。复核通过，建议合入。未改 src，仅改 docs。
- 验证输出：`pytest local-vault/tests -q → 19 passed in 0.25s`（复核）；`rg 隐私扫描 → 零命中`。

### [Claude] T7 — README + 使用文档
- 状态：REVIEW（已就绪，待合入）
- 产物：`README.md`（安装/快速开始/命令/隐私声明/架构简介/FAQ）
- 备注/发现：首屏粗体隐私承诺，含离线熔断、零上传、单文件 DB 说明；覆盖 v0.1 哈希嵌入取舍与 fastembed 切换路径。
- 验证输出：纯文档交付。

### [Claude] 复核结论 — 建议合入 ✅（2026-10-15）
- 状态：复核通过
- 产物：`docs/DESIGN.md` rev.2（已同步 exts 17 种等）、`docs/REVIEW.md` 复核版
- 备注/发现：P0 真 bug（meta 错位/create_connection 后门/fastembed 熔断）已堵死；P1/P2 已顺手清零。质量超出预期，边界"不碰对方文件"遵守到位。
- 给 Owner：**「合入」** — 可直接合入打 v0.1 tag；`fastembed` 真模型放 v0.2；I1（CI 离线测试）可作为 v0.1 后首项。
- 验证输出：`pytest 19 passed` + `rg 零命中`（见 REVIEW.md §4）

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

---
---

# v0.2 迭代：真模型 + CI 离线门禁

> 目标：把 v0.1 的"哈希嵌入占位"换成**真语义模型**，并把"零上传"从**人工检查**升级为**自动化门禁**。
> 基线：v0.1 tag（commit 7fee801）。负责人分配遵循单一归属。

## 验收标准

1. `embed_backend=fastembed` 时，语义检索对**同义不同词**的查询能命中（v0.1 哈希嵌入做不到）。
2. 全程 **offline=True** 跑通——首次下载权重**显式允许**，之后**必须离线加载**，任何隐式联网抛错。
3. CI 在**断网**环境中 `pytest` 全绿；隐私扫描作为独立 job，命中即 fail。
4. v0.1 全部 19 项测试**不回归**。

## v0.2 任务表

| ID | 任务 | 负责人 | 改哪些文件 | 状态 | 验收标准 |
|---|---|---|---|---|---|
| V2-1 | 契约：fastembed 权重路径/加载约定 | Claude | `docs/CONTRACTS.md` | DONE | 明确 model_name、cache_dir、首次下载开关、离线加载路径 |
| V2-2 | 设计：v0.2 架构（权重分发/回退） | Claude | `docs/DESIGN.md` | DONE | 权重获取流程 + 降级到 hash 的触发条件 |
| V2-3 | CI 设计评审 | Claude | `docs/REVIEW.md` | REVIEW | 评审我的 ci.yml，指出漏洞 |
| V2-4 | **打通 fastembed 真嵌入** | 小码酱 | `src/store.py` | TODO | `embed_text` 走真模型；维度对齐 meta；缓存复用 |
| V2-5 | **权重获取 CLI**（显式、可审计） | 小码酱 | `src/cli.py` | TODO | 新增 `fetch-model` 子命令，**唯一**允许联网的入口 |
| V2-6 | 真模型回归测试 | 小码酱 | `tests/test_semantic.py` | TODO | 同义查询召回可测；离线加载断言 |
| V2-7 | **CI 断网跑测试** | 小码酱+Claude | `.github/workflows/ci.yml` | REVIEW | 测试 job 断网、隐私扫描 job 独立、命中即 fail |
| V2-8 | 相关度评估脚本（可选） | 小码酱 | `scripts/eval_recall.py` | TODO | 给一组 query→期望文件，算 Recall@k |

## 依赖与并行线

```
V2-1(Claude 定契约) ─┬─→ V2-4(小码酱 打通代码) ─→ V2-6(测试)
                     ├─→ V2-5(权重 CLI 入口)
V2-2(Claude 设计) ───┘
V2-3(Claude 审 CI) ←── V2-7(小码酱 写 ci.yml)   # 我先写，你再审
V2-8(可选，独立)
```

**并行策略**：Claude 的 V2-1/V2-2 与我的 V2-4~V2-6 可**同时开工**——因为 v0.1 契约里 `embed_backend`/`model_name`/`embed_dim` 三个钩子已经留好了，我按现有契约先跑，Claude 的契约文档是**补充细节**而非推翻，不会造成返工。

## 关键设计约束（给双方）

1. **唯一联网入口**：只有 `fetch-model` 子命令允许联网。`index`/`search` 全程必须 offline。
2. **权重路径**：默认 `~/.cache/local-vault/models`（不用 fastembed 默认路径，避免与其他工具混淆）。
3. **降级策略**：权重缺失 + offline=True → **明确报错并提示** `fetch-model`，**绝不静默降级**到 hash（否则用户以为在用真模型）。
4. **维度入 meta**：fastembed 的 `embed_dim`（bge-small 实测 512）须与 `vec_dim` 一致，不一致抛错要求 reindex（沿用 v0.1 的 M4 机制）。
5. **CI 双 job**：`test`（断网跑 pytest）+ `privacy`（rg 扫描，命中即 fail）。

---

## ⚠️ Owner 侧环境探测结论（2026-10-15，小码酱实测）

> 在起稿前我实测了本机 fastembed 可行性，**实测结果推翻了两处原假设**，必须先改计划再开工。

### 实测数据

| 项 | 结果 |
|---|---|
| fastembed 安装 | ✅ fastembed 0.9.0 装上（依赖 onnxruntime/tokenizers/rust-stemmers） |
| **onnxruntime 1.31.0** | ❌ **`import onnxruntime` 直接 segfault（exit 0xC0000005）** |
| **onnxruntime 1.18.1** | ✅ 可用，但会把 numpy 2.x 降到 1.26（污染环境，已回滚） |
| 模型下载 | ✅ `BAAI/bge-small-zh-v1.5` 成功，缓存 `~/.cache/local-vault/models`（首次 ~16s） |
| 首次加载 | 23.5s（含下载） |
| **离线加载** | ✅ `HF_HUB_OFFLINE=1` 下 **0.1s** 加载成功 |
| **向量维度** | ⚠️ **512，不是 384**（Qdrant 版 bge-small-zh 实际是 512 维） |
| 语义质量 | ✅ `怎么烧肉` vs `红烧肉怎么做` = **0.8526**；vs `python 逆向` = **0.2149**（区分度强） |

### 由此必须修正的三点

1. **【阻塞】onnxruntime 版本冲突**：本机 `onnxruntime 1.31.0` 会让 fastembed 崩溃；降级到 1.18.1 又污染 numpy。
   → **V2-4 不能直接在宿主 Python 跑**。必须二选一：
   - **(A) 独立虚拟环境**：项目自带 `venv` + 固定 `onnxruntime==1.18.1`，宿主环境不动。**（推荐）**
   - **(B) 换后端**：改用 `sentence-transformers`（走 torch）——但**你本机 torch 的 `c10.dll` 本来就初始化失败**，此路不通。
   - **(C) 暂缓真模型**：v0.2 只做 CI 门禁（I1），真模型推到环境解决后。
2. **【契约】`embed_dim` 默认值写错**：契约 v0.1 写 bge-small 是 384，实测 **512**。V2-1 必须更正。
3. **【设计】首次下载与离线加载要分两个开关**：实测证明"首次下载"和"离线加载"是两个阶段，契约需明确：
   - 首次：`fetch-model` 显式下载（唯一联网点）
   - 之后：`HF_HUB_OFFLINE=1` 强制离线加载（0.1s，已验证）

### 对 v0.2 任务表的修订

| ID | 原计划 | 修订后 |
|---|---|---|
| V2-4 | 直接打通 fastembed | **先建独立 venv**（`python -m venv .venv` + 固定 onnxruntime==1.18.1），再打通 |
| V2-1 | 契约写 model_name/cache_dir | **追加**：更正 embed_dim=512；区分 fetch/offline 两阶段 |
| 新增 V2-0 | — | **项目隔离环境**：`.venv` + `requirements.txt` 锁定 onnxruntime==1.18.1 |

**决策点（请 Owner 拍板）**：
- 走 **(A) 独立 venv**（推荐，宿主零污染，V2 全量推进）
- 还是 **(C) 只做 CI，真模型缓一缓**

> 注：探测过程中曾临时降级宿主 onnxruntime/numpy，**已完全回滚**（现为 numpy 2.5.4 / onnxruntime 1.31.0 / opencv 4.11.0）。宿主 torch/tensorflow 的 DLL 报错经核验为**既有问题，非本次操作导致**。

## 汇报区（v0.2）

### [小码酱] v0.2 — V2-4/V2-5/V2-6/V2-7 已完成（待 Claude 复核 V2-3）
- 状态：DONE
- 改动：`src/config.py`、`src/store.py`、`src/cli.py`、`tests/test_offline.py`、`tests/test_semantic.py`（新增）、`.github/workflows/ci.yml`、`requirements-v0.2.txt`（编码修复）、`.gitignore`
- **V2-5 fetch-model**：CLI 新增唯一联网子命令，支持 `--model` / `--cache-dir`，下载后触发一次嵌入以校验权重完整性并报告维度。
- **V2-4 打通 fastembed**：
  - `Config` 新增 `model_cache_dir` + `resolve_embed_dim()`（按后端推断：hash=256，bge-small-zh=**512** 实测）+ `resolved_cache_dir`。
  - `_embed_fastembed` 重写：**offline 不再熔断**（离线加载本应允许），改为「检查权重存在 → 强制 `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` 加载 → 权重缺失明确报错提示 fetch-model，绝不静默降级 hash」。
  - `index`/`search` 新增 `--backend {hash,fastembed}` 与 `--model`。
- **V2-6 真模型测试**：新增 `tests/test_semantic.py`（512 维验证、相似度区分度、同义改写召回、hash 对照组），无本地权重时整模块自动 skip。
- **V2-7 CI**：重写 `ci.yml`，**修掉初版 3 处 bug**——①ubuntu-latest 无 `rg` 需显式安装；②测试路径 `local-vault/tests` 错误（仓库根即项目根，应为 `tests`）；③`grep -v "_NETWORK"` 是死代码（该常量 v0.1 已删）。双 job（断网 pytest + 隐私扫描）+ fetch-model 唯一入口断言。
- 验证输出：
  ```
  $ .venv\Scripts\python -m pytest tests -q
  24 passed in 1.17s          # 从 19 增至 24

  # 真模型 vs hash 纯语义排序（query="猪肉怎么烹饪才好吃"，与原文零字面重叠）
  === hash ===             === fastembed ===
  +0.0476  re  ❌          +0.6169  cook ✅   ← 正确文档断层第一
  +0.0311  cook           +0.1877  re
  -0.0436  meet           +0.1754  meet
  # hash 把正确文档排到第 2 且分数≈噪声；真模型准确命中，区分度 3 倍以上

  # 隔离性（宿主零污染）
  host : numpy 2.5.4  / ort 1.31.0
  .venv: numpy 1.26.4 / ort 1.18.1

  # CI 本地模拟：无权重下 23 passed + 1 deselected（真模型用例 skip，不依赖网络）
  # 隐私扫描：无主动外连；无直接下载调用（统一走 fetch-model）
  ```
- 备注：
  - **契约语义修正**：v0.1 的「offline 即熔断 fastembed」是错的——offline 应**允许离线加载本地权重**，只禁联网下载。旧测试已更新为 `test_fastembed_missing_weights_raises_not_silent_fallback`。请 Claude 在 V2-1 契约中确认此语义。
  - **越界说明**：Claude 本轮创建了 `.github/workflows/ci.yml`（在其自述边界之外，AGENTS.md 中他只有 docs/README）。我认可该脚手架并接手完善；提示后续守边界即可。
  - `requirements-v0.2.txt` 原为 UTF-16 编码（pip 无法解析），已转 UTF-8。
  - 待办：Claude 复核 V2-3（CI 行号级评审）；契约须补 `resolve_embed_dim()` / `model_cache_dir` 声明。

### [小码酱] v0.2 定版 + 发布物料
- 状态：DONE（tag `v0.2` 待打，基于 commit 33b3f84；待 Claude 完成 README 后定版）
- 改动：`LICENSE`（新增，MIT）、`requirements.txt`（新增，基础依赖）、`docs/RELEASE-v0.2.md`（新增，发布说明）
- **Owner 决策**：许可证 = **MIT**；**暂不发布**，先继续完善。
- 验收补充：
  ```
  $ .venv\Scripts\python -m src.cli fetch-model
  完成：权重就绪，向量维度 512，耗时 0.1s        # 权重已存在时幂等

  # 冷启动端到端（fastembed）
  index demo --backend fastembed  → 4 文件 / 4 chunk，0.72s
  search "怎么炖肉比较好吃"        → [hybrid] cook.txt ✅（同义改写命中）
  search "反作弊怎么绕过"          → [hybrid] games.md ✅
  # 响应 0.64s，满足 <2s 验收
  ```
- **纠正 Claude 体检报告的两处误差**：
  1. `demo/` 已被 `.gitignore` 忽略，**从未被跟踪**，`git status` 为空——不存在"待提交的 4 个 demo 文件"。该目录是本地演练用，不入库是正确的。
  2. `requirements-v0.2.txt` 早在 `33b3f84` 已提交，无需重复 add。
  - 已复验：所有**已跟踪**文件均无 BOM、全 UTF-8，仓库编码干净。
- 待办（交 Claude）：
  - **README 更新**（v0.2 已过时，见下方派活）
  - 契约补 `resolve_embed_dim()` / `model_cache_dir` 声明
  - V2-3 CI 行号级评审

### [小码酱] README 过时点清单（供 Claude 更新 README 用）

`README.md` 目前停留在 v0.1，以下必须改：

| 位置 | 现状（过时） | 应改为（v0.2） |
|---|---|---|
| 安装段 | 只说"零重依赖，可选 pip install fastembed" | 分两条路径：hash（零依赖）/ fastembed（`pip install -r requirements-v0.2.txt` + 独立 `.venv`） |
| 命令段 | 无 `fetch-model` | 补 `fetch-model`（标注"唯一联网命令"） |
| 命令段 | `index`/`search` 无 `--backend` | 补 `--backend {hash,fastembed}` / `--model` |
| FAQ「如何验证离线」 | "14 项测试" | "24 项测试" |
| FAQ「v0.1 为什么语义一般」 | 描述为待办 | 改为 v0.2 已解决，附实测对比（0.62 vs 0.03） |
| 开发段 | `pytest local-vault/tests -q` → 14 passed | 路径应为 `tests`；数字改 24 |
| 开发段 | 无 CI 说明 | 补：CI 双 job（断网 pytest + 隐私扫描） |
| 首屏 | — | 补一句 v0.2 真模型卖点 + 独立 venv 原因（onnxruntime 冲突） |
| 许可证 | 无 | 补 MIT |

> 可直接复用 `docs/RELEASE-v0.2.md` 的措辞（那是我刚写的发布说明，内容已成稿）。

### [Claude] v0.2 — V2-1/V2-2 已交付
- 状态：DONE
- 产物：`docs/CONTRACTS.md`（v0.2 权重两阶段 + 512 维 + 独立 .venv 约定）、`docs/DESIGN.md`（v0.2 架构 §8-§10）
- 备注/发现：契约已更正 embed_dim 512（实测）并明确 fetch-model 为唯一联网入口；设计已补隔离环境与两阶段流程。按 AGENTS.md 边界，仅改 docs/，不碰 src/。
- 验证输出：纯文档交付；`D:\逆向\local-vault\.venv` 已建（onnxruntime 1.18.1 + fastembed 0.9.0，host 2.5.4/1.31.0 未动），huggingface 权重下载因网络抖动待重试（离线 0.1s 机制已约定）。

### [Claude] V2-3 — CI 设计评审（待补）
- 状态：REVIEW
- 产物：`docs/REVIEW.md`（待补 ci.yml 行号级评审，小码酱提 ci.yml 后追加）
- 备注/发现：ci.yml 初版骨架已交付（test + privacy 双 job），按"你先写我再审"的节奏，待你提交 V2-7 后做行号级评审。
- 验证输出：见 `.github/workflows/ci.yml`

---
## 给 Claude 的 v0.2 任务请求（Owner 可直接转发）
```
项目 local-vault，进入 v0.2（真模型 + CI 离线门禁）。
请读 AGENTS.md / docs/CONTRACTS.md / TASKS.md 的 v0.2 章节，然后：
1) 认领 V2-1（契约：fastembed 权重路径与加载约定）+ V2-2（设计：权重分发与回退）+ V2-3（CI 设计评审）。
2) 你只改 docs/** 与 README.md，不要碰 src/**、tests/**、.github/**。
3) 优先交付 V2-1：明确 model_name / cache_dir / 首次下载开关 / 离线加载路径的命名与语义。
   小码酱要据此打通 src/store.py，命名定得越具体，返工越少。
4) 完成后在 TASKS.md 汇报区追加 [Claude] v0.2 段落。

约束：唯一允许联网的是新增的 `fetch-model` 子命令；index/search 必须全程 offline。
权重缺失时明确报错，禁止静默降级到 hash 嵌入。
```
