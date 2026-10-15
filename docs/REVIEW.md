# REVIEW.md — Claude 的代码审查记录

> 格式：每条按 `[严重级] 文件:行 — 问题 — 建议` 书写。
> 严重级：`[CRITICAL]`（隐私/数据外泄，必须立即修） / `[MAJOR]`（接口/逻辑） / `[MINOR]`（风格/优化）

## 待审
- `src/**`（等小码酱提交 v0.1 骨架后开始）

## 审查历史
（暂无）

---
### 示例格式
- `[CRITICAL] src/store.py:42 — 疑似把用户文本 POST 到远端 — 删除该调用，改为纯本地计算`
- `[MAJOR] src/search.py:88 — RRF 融合权重未归一化 — 建议对分数做 min-max 归一化后再融合`
- `[MINOR] src/cli.py:12 — 缺少 arg parse 错误处理 — 用 argparse 自带报错`
