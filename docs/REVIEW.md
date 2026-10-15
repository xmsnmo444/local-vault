# REVIEW.md — Claude 的代码审查记录

> 格式：每条按 `[严重级] 文件:行 — 问题 — 建议` 书写。
> 严重级：`[CRITICAL]`（隐私/数据外泄，必须立即修） / `[MAJOR]`（接口/逻辑） / `[MINOR]`（风格/优化）
> 依据：`docs/CONTRACTS.md` 冻结契约 + `AGENTS.md` 核心铁律（零上传、本地优先）

## 当前审查：v0.1 契约预审（2026-10-15）

> 状态：`src/**` 骨架尚未提交（`src/` 为空，仅有 `.gitignore`），本轮为**契约层静态预审**，不涉及行号代码级定位。待小码酱提交 `src/config.py` / `store.py` / `search.py` 后追加代码级审查。

### [CRITICAL] 隐私与离线 — 契约已约定，待代码验证

- `[CRITICAL] src/store.py:* — offline 熔断未在签名中显式 — CONTRACTS 要求 cfg.offline=True 时禁止任何网络调用，建议 Store.__init__ 首行即校验并在触发 urllib/requests/socket/hf_hub_download 时抛 RuntimeError("offline mode: network disabled")，并在 tests 中加入"有网络调用即 fail"的断言（对应 T6/I1）`
- `[CRITICAL] src/**/*.py:* — 全仓库外连扫描 — CI 必须执行 grep -R "requests\\.post\\|urllib\\.request\\|socket\\.create" src/ 且结果为 0，README 首屏必须粗体声明"数据不出本机"`

> 预审结论：契约层面已满足"零上传"定义，风险点在于实现阶段是否真正熔断模型下载回退（如 transformers 的 hf_hub_download 自动联网）。D2 代码级审查时将逐文件 grep 验证。

### [MAJOR] 接口与数据一致性 — 需 Owner / 小码酱确认

- `[MAJOR] docs/CONTRACTS.md:76 — Store.search_vector 签名 list[tuple[str, float]] 语义模糊 — str 未说明是 chunk_id 还是 path，建议明确为 list[tuple[chunk_id, score]] 或 list[tuple[Hit, score]]，避免 search.py 误用 path 去重导致多 chunk 命中被合并`
- `[MAJOR] docs/CONTRACTS.md:72 — upsert_chunks 幂等但无"孤儿清理" — chunk_id=sha1(f"{path}:{start}:{end}") 在文件编辑后 start/end 偏移会产生新 ID，旧 chunk 残留导致库膨胀与召回噪音。建议追加 Store.prune(path) 或 upsert 时先 delete where path=? 再批量插入，或在 cli index 中加入 --prune 阶段`
- `[MAJOR] docs/CONTRACTS.md:74 — needs_reindex 仅以 mtime 判定 — mtime 可被 touch 篡改、且同 mtime 不同内容会漏索引。v0.1 接受该取舍，但建议文档化为已知局限，并在 D1 中已标注；后续可加可选 content_hash 列做二次校验`
- `[MAJOR] docs/CONTRACTS.md:78-79 — 向量 BLOB 无维度/归一化元数据 — 若后续更换 model_name（如 384→768）旧 BLOB 无法自描述，建议建表时追加 vec_dim INTEGER 或在 Store.init_schema 中校验 cfg.model_name 与已存 vec 维度一致，不一致时提示 reindex`
- `[MAJOR] docs/CONTRACTS.md:83-86 — search 融合未定义 BM25 索引形态 — 契约未说明 BM25 是 SQLite FTS5 还是内存倒排、是否持久化、中文分词器（jieba/字符二元）如何选择。建议在 store.py 中明确建表时是否创建 FTS5 虚表及分词方案，避免 search.py 与 store.py 各自实现导致不一致`
- `[MAJOR] docs/CONTRACTS.md:60 — chunk_text 返回 list[tuple[int,int,str]] 未定义越界与空文本行为 — 空文件/仅空白字符应返回 [] 还是 [(0,0,"")] 需在契约中补一句话，避免 indexer 与 store 对空文件处理分歧`
- `[MAJOR] src/search.py:* — RRF k 值未契约化 — 契约仅写"融合（RRF）"，未固定 k=60 等参数，建议在 Config 中增 rrf_k: int=60 并在 search 签名中可覆盖，保证可复现与可调参`

### [MINOR] 健壮性与可观测性

- `[MINOR] docs/CONTRACTS.md:63 — iter_files 未契约符号链接策略 — 建议显式 followlinks=False 并在 DESIGN.md 已补充，防止环链导致遍历死循环`
- `[MINOR] docs/CONTRACTS.md:50 — max_file_mb 跳过策略无统计 — 建议 Store/CLI 在 stats 中暴露 skipped_large / skipped_binary / skipped_hidden 计数，便于用户排查"为什么没搜到"`
- `[MINOR] docs/CONTRACTS.md:90-95 — CLI 未定义错误码 — 建议 main 返回 0/1/2（0 成功、1 参数错误、2 索引失败），便于脚本化调用`
- `[MINOR] 命名 — chunk_id 用 sha1 足够但建议标注 hexdigest 长度 40，避免与 sha256 混淆`

## 待代码级审查（等 src 骨架提交后）

- [ ] `src/config.py` — load_config 默认值、offline 熔断、类型校验
- [ ] `src/indexer.py` — 扩展名过滤、max_file_mb、二进制探针、overlap 正确性
- [ ] `src/store.py` — 建表、upsert 事务、vec 打包/解包、余弦相似度数值稳定性
- [ ] `src/search.py` — embed_query 归一化、BM25 实现、RRF 融合、source 标注
- [ ] `src/cli.py` — 参数解析、编排顺序、错误提示
- [ ] 全仓库隐私扫描：`rg -n "requests|urllib|socket|http.client|telemetry|sentry|posthog" src/`

> 审查方法：逐文件读 `src/**`，逐行对照本契约签名，发现偏离即按上表格式追加条目；任何网络外发直接标 [CRITICAL]。

## 审查历史

### 2026-10-15 — D1 架构文档交付 + D2 契约预审

- 产物：`docs/DESIGN.md`（模块图、数据流、取舍、隐私设计、性能估算）
- 预审：`docs/REVIEW.md` 本文件（契约层，7 项 MAJOR + 4 项 MINOR，2 项 CRITICAL 待代码验证）
- 下一步：小码酱提交 src v0.1 后，追加代码级审查并更新本文件

---

### 示例格式（保留）

- `[CRITICAL] src/store.py:42 — 疑似把用户文本 POST 到远端 — 删除该调用，改为纯本地计算`
- `[MAJOR] src/search.py:88 — RRF 融合权重未归一化 — 建议对分数做 min-max 归一化后再融合`
- `[MINOR] src/cli.py:12 — 缺少 arg parse 错误处理 — 用 argparse 自带报错`
