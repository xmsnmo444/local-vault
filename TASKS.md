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
| T7 | README + 使用文档 | Claude | `README.md` | TODO | 安装、用法、隐私声明完整 |
| D1 | 架构设计文档 | Claude | `docs/DESIGN.md` | TODO | 模块图 + 数据流 + 取舍说明 |
| D2 | 索引格式契约评审 | Claude | `docs/REVIEW.md` | TODO | 审 T3/T4 的接口，指出问题 |
| I1 | GitHub Actions：离线测试 | 小码酱 | `.github/workflows/*` | TODO | CI 跑 tests 且断网 |

### 依赖关系
```
T1 ─→ T2 ─→ T3 ─→ T4 ─→ T5 ─→ T6
                        └────→ D2(Claude 审 T3/T4)
D1(Claude) 可与 T1~T4 并行
T7(Claude) 待 T5 接口稳定后写
```

---

## 汇报区（AI 在此贴进度 + 运行输出）

### [小码酱] T1 — 配置加载
- 状态：DOING
- 改动：`src/config.py`
- 验证输出：（待填）
- 待办：接入 T2

### [Claude] D1 — 架构文档
- 状态：TODO
- 备注：等 Owner 指派时启动

---

## 给 Claude 的本次任务请求（Owner 可直接转发）
```
项目 local-vault，本地优先个人数据保险箱。
请先读 AGENTS.md 与 docs/CONTRACTS.md，然后：
1) 认领 D1（写 docs/DESIGN.md）与 D2（评审 docs/REVIEW.md）。
2) 你只改 docs/ 与 README.md，不要碰 src/。
3) 完成后把产物写进文件，并在 TASKS.md 汇报区追加 [Claude] 段落。
```
