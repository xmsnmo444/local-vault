# local-vault — 本地优先的个人数据保险箱 + 语义检索

> **隐私承诺：数据不出本机，零上传，默认离线。** 任何代码路径都不会把你的文件或文本发往网络；`cfg.offline=True` 时任何联网企图直接抛 `OfflineViolation`。全程可断网运行。

## 一句话

遍历散落文件（`txt/md/py/json/csv/log/html` 等）→ 二进制过滤 → 按字符切块（512/64 overlap）→ 本地嵌入 → 存入单文件 SQLite（`vault.db`）→ 一句话 `search` 找回原文片段。单文件可备份、可随身携带。

---

## 安装

```bash
# 克隆后直接可用，零重依赖（v0.1 哈希嵌入）
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install pytest  # 仅测试需要

# 可选：接真语义模型（本地权重，不联网下载）
pip install fastembed
# 预先下载模型权重到本地，再设 cfg.model_name 指向本地路径
```

要求：Python ≥ 3.10，SQLite（Python 自带）。

---

## 快速开始

```bash
# 1. 索引一个目录（递归，跳过 .git/node_modules/__pycache__ 等）
python -m src.cli --db vault.db index demo
# → 完成：索引 4 个文件 / 4 个 chunk，跳过 0，耗时 0.03s

# 2. 搜索（一句话，Top-K，语义+关键词 RRF 融合）
python -m src.cli --db vault.db search "逆向" -k 3
# → 1. [hybrid] demo/re.md:0-52  逆向工程笔记：APK 脱壳、smali、Frida hook...

python -m src.cli --db vault.db search "做饭" -k 3
# → 1. [hybrid] demo/cook.txt:0-29  红烧肉做法：五花肉、冰糖...

# 3. 查看库统计
python -m src.cli --db vault.db stats
# → 文件数：4  chunk 数：4  文本量：0.00 MB

# 4. 增量/孤儿清理已自动处理
# 再跑 index → 增量跳过；编辑后重跑 → 旧 chunk 自动清理；删除文件后重跑 → 失效 chunk 清理
python -m src.cli --db vault.db index demo --force  # 强制全量重建
```

编程式：

```python
from src.config import load_config
from src.store import Store
from src.search import search

cfg = load_config(db_path="vault.db")  # offline=True 默认
with Store(cfg) as store:
    store.init_schema()
    hits = search("Frida hook 怎么写", cfg, store, top_k=5)
    for h in hits:
        print(h.source, h.path, h.text[:80])
```

---

## 命令

```
python -m src.cli --db <path> index <dir> [--force]
python -m src.cli --db <path> search "<query>" [-k 10] [--rrf-k 60]
python -m src.cli --db <path> stats
```

退出码：`0` 成功 / `1` 参数错误 / `2` 索引失败。支持 `Ctrl+C` 中断（130）。

环境变量（可选）：`LV_DB_PATH`、`LV_OFFLINE`（0/false/no 关闭离线）、`LV_EXTS`（逗号分隔，如 `md,txt,py`）。

---

## 隐私与离线

* **零上传不变量**：`rg -n "requests|urllib|socket|http.client|telemetry" src/` 仅命中白名单常量，无主动外连。CI 中该扫描必须为 0。
* **运行时熔断**：`Store.__init__` 首行 `assert_offline()`，`offline=True` 时替换 `socket.connect` / `getaddrinfo`，任何联网直接抛 `OfflineViolation`；`close()` 时可装卸，避免测试污染。
* **无遥测**：无埋点、无上报 URL、无 telemetry。
* **数据本地**：`vault.db` 单文件 SQLite，向量以 float32 BLOB 存库，检索在进程内做余弦相似度；不依赖外部服务。

详见 `docs/CONTRACTS.md` 隐私自检清单，`docs/REVIEW.md` 的 `[CRITICAL]` 一票否决。

---

## 架构简介

```
config → indexer → store → search → cli
         ↓          ↓        ↓
       Chunk 流   SQLite   Hit[]
                vault.db
```

* `config`：无依赖，`Config` + `load_config` + `validate`。
* `indexer`：`iter_files`（扩展名/大小/二进制探针/隐藏目录过滤）→ `chunk_text`（字符切块，空文本返回 `[]`）→ `iter_chunks`。
* `store`：`Store`（建表 `chunks` + `meta`、upsert 先删后插防孤儿、`needs_reindex` 以 mtime+content_hash 双因子、维度/模型一致性校验、纯内存 BM25（字符二元+英文词）、向量余弦）。
* `search`：语义 Top-N（`top_k*4`）∪ BM25 Top-N → RRF（`k=cfg.rrf_k=60` 可覆盖）→ `Hit{source=semantic|keyword|hybrid}`。
* `cli`：仅编排，含 `prune_missing` 失效清理。

完整设计见 `docs/DESIGN.md`，接口契约见 `docs/CONTRACTS.md`，代码审查见 `docs/REVIEW.md`。

---

## 常见问题

**Q: v0.1 为什么搜"逆向"能中但语义泛化一般？**
A: v0.1 默认 `embed_backend="hash"`（内置哈希嵌入，零依赖、确定性、可离线），正例主要靠 BM25 关键词路兜底，故命中标记为 `[hybrid]`。已预留 `fastembed` 后端，设 `cfg.embed_backend="fastembed"` 即可换真模型，语义召回随之提升。

**Q: 换模型要重建索引吗？**
A: 要。`meta` 表存 `vec_dim`/`model_name`，维度或后端不一致会抛 `RuntimeError` 提示删库重建。

**Q: 支持哪些文件？**
A: 默认 `.txt .md .markdown .py .js .ts .json .csv .log .html .htm .yaml .yml .toml .ini .cfg .rst`，超 `max_file_mb=20`、含 `\x00` 的二进制、`.git/node_modules` 等目录自动跳过。可用 `LV_EXTS` 覆盖。

**Q: 如何验证离线？**
A: `pytest local-vault/tests -q`（14 项，含 4 项离线哨兵测试）或断网后跑 `index`/`search`，任何联网企图会抛 `OfflineViolation`。

**Q: 性能？**
A: 1000 文件 × ~5000 chunks，BLOB 全扫余弦 < 200ms，查询端到端 < 500ms，满足 `<2s` 验收。

---

## 开发

```bash
pytest local-vault/tests -q          # 14 passed
rg -n "requests|urllib|socket" src/  # 仅白名单
```

分支与提交遵循 `AGENTS.md`：`feat/<name>-<scope>` / `docs/<task>`，`type(scope): summary`。
