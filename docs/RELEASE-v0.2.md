# 发布说明 — local-vault v0.2

> 面向用户与贡献者。面向 AI 的协作记录见 `docs/COLLAB-PLAYBOOK.md`。

## 一句话

**把你的文件变成可以"一句话找到"的本地知识库——数据不出本机。**

## 核心卖点

1. **零上传**：所有索引与检索都在本机完成。唯一的联网命令是 `fetch-model`（只下载模型，不上传任何数据）。
2. **真语义检索**：v0.2 接入 `fastembed` + `bge-small-zh-v1.5`，能理解"同义不同词"。

   实测（查询与原文**零字面重叠** `猪肉怎么烹饪才好吃`）：

   | 后端 | 正确文档排名 | 相似度 |
   |---|---|---|
   | hash（v0.1 占位） | 第 2 名 ❌ | 0.03（≈噪声） |
   | **fastembed（v0.2）** | **第 1 名** ✅ | **0.62** |

3. **单文件数据库**：`vault.db` 一个文件装下所有索引，可备份、可随身带。
4. **增量索引**：重复索引自动跳过未变文件；编辑后旧片段自动清理；删除文件后失效索引自动回收。

## 快速开始

```bash
# 1. 建虚拟环境（重要：v0.2 需要锁定的 onnxruntime 版本）
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 2. 装依赖（含真模型后端）
pip install -r requirements-v0.2.txt

# 3. 一次性下载模型权重（唯一联网步骤，约 90MB）
python -m src.cli fetch-model

# 4. 索引你的目录（全程离线）
python -m src.cli --db vault.db index D:\我的笔记 --backend fastembed

# 5. 一句话找回
python -m src.cli --db vault.db search "上次那个关于部署的文档" -k 5 --backend fastembed
```

> 只想快速试、不想下模型？用内置哈希后端，零依赖：
> `python -m src.cli --db vault.db index <dir> --backend hash`

## 为什么需要独立虚拟环境

`onnxruntime` 的某些版本（如 1.31.0）在部分机器上会导致 `fastembed` 崩溃。
本项目锁定 **`onnxruntime==1.18.1`** 于 `.venv` 内，**不动你的全局环境**。

- 项目环境：`numpy 1.26.4` / `onnxruntime 1.18.1` / `fastembed 0.9.0`
- 你的全局环境：不受影响

## 命令一览

```
python -m src.cli --db <path> index <dir> [--force] [--backend hash|fastembed] [--model NAME]
python -m src.cli --db <path> search "<query>" [-k 10] [--rrf-k 60] [--backend ...]
python -m src.cli --db <path> stats
python -m src.cli fetch-model [--model NAME] [--cache-dir DIR]   # 唯一联网命令
```

退出码：`0` 成功 / `1` 参数错误 / `2` 索引失败 / `130` Ctrl+C 中断。

## 隐私设计（可自行验证）

```bash
# 1. 扫描代码：不允许任何主动外连
rg -n "requests\.post|urllib\.request|http\.client|telemetry|sentry" src/
# → 零命中

# 2. 运行时不变量：离线模式下任何联网企图直接抛错
.venv\Scripts\python -m pytest tests -q
# → 24 passed（含离线哨兵与真模型用例）

# 3. 断网后照样跑 index/search
```

**离线语义**：`offline=True`（默认）**允许离线加载本地权重**，只禁止联网下载。
权重缺失时会明确报错提示你运行 `fetch-model`，**绝不静默降级**到弱后端。

## 与 v0.1 的差异

| | v0.1 | v0.2 |
|---|---|---|
| 嵌入后端 | 仅内置 hash | hash + **fastembed 真模型** |
| 语义泛化 | 差（靠关键词兜底） | **强（同义改写可召回）** |
| 联网入口 | 无 | `fetch-model`（唯一、显式） |
| 环境 | 宿主直接跑 | **独立 `.venv`**（隔离 onnxruntime 冲突） |
| CI | 无 | 断网测试 + 隐私扫描双 job |
| 测试 | 19 | **24** |

## 已知限制

- 仅支持纯文本类扩展名（`.txt/.md/.py/.json/.csv/.log/.html` 等 17 种）；PDF、Office、图片 OCR 未支持。
- 中文分词为字符二元（BM25 路），未接 jieba。
- 向量检索为全库扫描，>5 万片段建议后续接 ANN 索引。
- 首次 `fetch-model` 约需下载 90MB 权重（一次性）。

## 许可

（待定 — 发布前请 Owner 确认）
