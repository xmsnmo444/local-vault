# local-vault — 本地优先的个人数据保险箱 + 语义检索

> **隐私承诺：数据不出本机，零上传，默认离线。** 任何代码路径都不会把你的文件或文本发往网络；`cfg.offline=True` 时任何联网企图直接抛 `OfflineViolation`。全程可断网运行。
> **v0.2 已接入真语义模型**：`fastembed + bge-small-zh-v1.5`（512 维实测），`怎么烧肉` 能找回 `红烧肉做法`，不再只靠关键词。

## 一句话

遍历散落文件（`txt/md/py/json/csv/log/html` 等 17 种）→ 二进制过滤 → 按字符切块（512/64 overlap）→ 本地嵌入（hash 或 fastembed）→ 存入单文件 SQLite（`vault.db`）→ 一句话 `search` 找回原文片段。单文件可备份、可随身携带。

---

## 安装

### 路径 A：零依赖（v0.1 哈希后端，克隆即跑）

```bash
pip install -r requirements.txt   # 仅 pytest
python -m src.cli --db vault.db index demo --backend hash
python -m src.cli --db vault.db search "逆向" --backend hash
```

### 路径 B：真语义模型（v0.2 推荐）

```bash
# 1. 建独立虚拟环境（重要：与宿主隔离，避免 onnxruntime 冲突）
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 2. 装依赖（含 fastembed，已锁定 onnxruntime==1.18.1）
pip install -r requirements-v0.2.txt

# 3. 一次性下载模型权重（唯一联网步骤，约 90MB）
python -m src.cli fetch-model
# → 完成：权重就绪，向量维度 512，耗时 0.1s（已存在时幂等）

# 4. 之后全程离线
python -m src.cli --db vault.db index demo --backend fastembed
python -m src.cli --db vault.db search "怎么炖肉比较好吃" --backend fastembed -k 5
# → [hybrid] cook.txt ✅（与原文零字面重叠也能命中）
```

> **为什么要独立 `.venv`？** `onnxruntime 1.31.0` 在部分机器上会让 `fastembed` 直接 segfault，本项目在 `.venv` 内锁定 `onnxruntime==1.18.1 + numpy==1.26.4 + fastembed==0.9.0`，你的全局环境（`numpy 2.5.4 / onnxruntime 1.31.0`）不受影响。详见 `docs/CONTRACTS.md` v0.2 章节。

要求：Python ≥ 3.10，SQLite（Python 自带）。

---

## 快速开始

```bash
# 1. 索引一个目录（递归，跳过 .git/node_modules/__pycache__ 等）
python -m src.cli --db vault.db index demo --backend hash
# → 完成：索引 4 个文件 / 4 个 chunk，未变 0，失败 0，耗时 0.03s

# 2. 搜索（一句话，Top-K，语义+关键词 RRF 融合）
python -m src.cli --db vault.db search "逆向" -k 3 --backend hash
# → 1. [hybrid] demo/re.md:0-52  逆向工程笔记：APK 脱壳、smali、Frida hook...

python -m src.cli --db vault.db search "怎么炖肉比较好吃" -k 3 --backend fastembed
# → 1. [hybrid] demo/cook.txt:0-30  红烧肉做法：五花肉切块...

# 3. 查看库统计
python -m src.cli --db vault.db stats
# → 文件数：4  chunk 数：4  文本量：0.00 MB

# 4. 增量/孤儿清理已自动处理
python -m src.cli --db vault.db index demo --backend hash
# → 完成：索引 0 个文件 / 0 个 chunk，未变 4，失败 0

python -m src.cli --db vault.db index demo --force --backend hash  # 强制全量重建
```

编程式：

```python
from src.config import load_config
from src.store import Store
from src.search import search

cfg = load_config(db_path="vault.db", embed_backend="fastembed")  # offline=True 默认
with Store(cfg) as store:
    store.init_schema()
    hits = search("Frida hook 怎么写", cfg, store, top_k=5)
    for h in hits:
        print(h.source, h.path, h.text[:80])
```

---

## 命令

```
python -m src.cli --db <path> index <dir> [--force] [--backend hash|fastembed] [--model NAME]
python -m src.cli --db <path> search "<query>" [-k 10] [--rrf-k 60] [--backend ...] [--model NAME]
python -m src.cli --db <path> stats
python -m src.cli fetch-model [--model NAME] [--cache-dir DIR]   # 唯一联网命令
```

* `fetch-model`：显式下载权重到 `~/.cache/local-vault/models`（不上传任何数据），下载后所有 `index/search` 走 `HF_HUB_OFFLINE=1` 离线加载（0.1s）。
* 权重缺失 + `offline=True` → 明确报错提示 `fetch-model`，**绝不静默降级**到 hash。
* 维度与权重一一绑定：`bge-small-zh-v1.5` 实测 **512 维**（`hash` 为 256），换模型不重建会抛 `RuntimeError`。

退出码：`0` 成功 / `1` 参数错误 / `2` 索引失败 / `130` Ctrl+C 中断。支持 `--backend`/`--model` 覆盖。

环境变量（可选）：`LV_DB_PATH`、`LV_OFFLINE`（0/false/no 关闭离线）、`LV_EXTS`（逗号分隔，如 `md,txt,py`）。

---

## 隐私与离线

* **零上传不变量**：`rg -n "requests\.post|urllib\.request|http\.client|telemetry" src/` 零命中。CI 中该扫描作为独立 job，命中即 fail。
* **运行时熔断**：`Store.__init__` 首行 `assert_offline()`，`offline=True` 时替换 `socket.connect` / `getaddrinfo` / `create_connection`，任何联网直接抛 `OfflineViolation`；`close()` 时引用计数归零才卸载。
* **离线语义**：`offline=True` **允许离线加载本地权重**，只禁止联网下载（v0.2 语义修正，见 `docs/CONTRACTS.md`）。
* **无遥测**：无埋点、无上报 URL。
* **数据本地**：`vault.db` 单文件 SQLite，向量以 float32 BLOB 存库，检索在进程内做余弦相似度；不依赖外部服务。

详见 `docs/CONTRACTS.md` 隐私自检清单，`docs/REVIEW.md` 的 `[CRITICAL]` 一票否决。

---

## 架构简介

```
config → indexer → store → search → cli
         ↓          ↓        ↓
       Chunk 流   SQLite   Hit[]
                vault.db
                          fetch-model (唯一联网)
```

* `config`：无依赖，`Config` + `load_config` + `validate` + `resolve_embed_dim()` / `resolved_cache_dir`。
* `indexer`：`iter_files`（扩展名/大小/二进制探针/隐藏目录过滤）→ `chunk_text`（字符切块，空文本返回 `[]`）；`cli.cmd_index` 组装 `Chunk` 并生成稳定 `chunk_id`。
* `store`：`Store`（建表 `chunks` + `meta`、upsert 先删后插防孤儿、`needs_reindex` 以 mtime+content_hash 双因子、维度/模型一致性校验、纯内存 BM25、向量余弦；`_embed_fastembed` 强制离线加载）。
* `search`：语义 Top-N（`top_k*4`）∪ BM25 Top-N → RRF（`k=cfg.rrf_k=60` 可覆盖）→ `Hit{source=semantic|keyword|hybrid}`。
* `cli`：仅编排，含 `prune_missing` 失效清理与 `fetch-model`。

完整设计见 `docs/DESIGN.md`，接口契约见 `docs/CONTRACTS.md`，代码审查见 `docs/REVIEW.md`，发布说明见 `docs/RELEASE-v0.2.md`。

---

## 常见问题

**Q: v0.2 真模型比 v0.1 强多少？**
A: 实测零字面重叠查询 `猪肉怎么烹饪才好吃`：`hash` 把正确文档排第 2 且分数 `0.03≈噪声`，`fastembed` 断层第 1 且分数 `0.62`（第二名 `0.18`）。同义改写召回是 v0.2 的核心价值。

**Q: 换模型要重建索引吗？**
A: 要。`meta` 表存 `vec_dim`/`embed_backend`/`model_name` 三键，任一不一致抛 `RuntimeError` 提示删库重建（已用 `test_model_name_change_triggers_reindex` 卡死）。

**Q: 支持哪些文件？**
A: 默认 `.txt .md .markdown .py .js .ts .json .csv .log .html .htm .yaml .yml .toml .ini .cfg .rst` 共 17 种，超 `max_file_mb=20`、含 `\x00` 的二进制、`.git/node_modules` 等目录自动跳过。可用 `LV_EXTS` 覆盖。

**Q: 如何验证离线？**
A: `.venv\Scripts\python -m pytest tests -q` → `24 passed`（含 7 项离线哨兵 + 4 项真模型，含 `HF_HUB_OFFLINE=1` 强制离线），或断网后跑 `index`/`search`，任何联网企图抛 `OfflineViolation`。CI 在 `unshare -rn` 断网环境中全绿。

**Q: 性能？**
A: 1000 文件 × ~5000 chunks，BLOB 全扫 < 200ms，查询端到端 < 500ms，满足 `<2s` 验收。`fastembed` 首次加载 0.1s（离线）/ 首次下载约 90MB。

---

## 平台支持

| 平台 | 状态 | 激活虚拟环境 |
|---|---|---|
| Windows | ✅ 主要开发平台 | `.venv\Scripts\activate` |
| macOS (Intel / Apple Silicon) | ✅ 支持，需 macOS 11.0+ | `source .venv/bin/activate` |
| Linux | ✅ 支持 | `source .venv/bin/activate` |

CI 在 `ubuntu-24.04`、`macos-15-intel`（Intel x86_64）、`macos-15`（Apple Silicon arm64）三平台自动验证。真模型依赖（`onnxruntime 1.18.1` / `tokenizers` / `py-rust-stemmers` / `mmh3` / `numpy`）各平台均有预编译 wheel，无需本地编译 Rust。

> **macOS 注意事项**：需 **macOS 11.0+**（`onnxruntime` wheel 下限）；Apple Silicon 建议用 arm64 版 Python，Rosetta 下的 x86_64 也能跑（universal2 兼容）；`fastembed` 为纯 Python 包，跨平台无需编译。

---

## 开发

```bash
# 哈希后端（零依赖）
python -m pytest tests -q          # 24 passed（无权重时真模型用例自动 skip）
# 真模型后端（需 .venv + 权重）
.venv\Scripts\python -m pytest tests -q   # 24 passed

rg -n "requests|urllib|socket" src/  # 零命中
```

CI：GitHub Actions 双 job — `test`（`unshare -rn` 断网跑 pytest + 离线哨兵断言）+ `privacy`（`ripgrep` 扫描，含 `fetch-model` 唯一入口断言）。

分支与提交遵循 `AGENTS.md`：`feat/<name>-<scope>` / `docs/<task>`，`type(scope): summary`。

---

## 许可

MIT — 见 `LICENSE`。
