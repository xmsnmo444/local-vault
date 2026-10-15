"""src/cli.py — 命令行入口，只做编排，不含业务逻辑。

用法：
    python -m src.cli index  <dir> [--force]
    python -m src.cli search "<query>" [-k 10]
    python -m src.cli stats
退出码：0 成功 / 1 参数错误 / 2 索引失败
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from .config import load_config
from .indexer import chunk_text, content_hash, iter_files, make_chunk_id, read_text
from .search import search


def cmd_index(args: argparse.Namespace) -> int:
    overrides = {"db_path": args.db}
    if getattr(args, "backend", None):
        overrides["embed_backend"] = args.backend
    if getattr(args, "model", None):
        overrides["model_name"] = args.model
    cfg = load_config(**overrides)
    root = os.path.abspath(args.directory)
    if not os.path.isdir(root):
        print(f"错误：目录不存在 {root}", file=sys.stderr)
        return 1

    from .store import Chunk, Store

    t0 = time.time()
    indexed_files = 0
    indexed_chunks = 0
    skipped_uptodate = 0
    skipped_error = 0

    try:
        with Store(cfg) as store:
            store.init_schema()
            store.check_model_consistency()

            # [P2-7] 单次遍历，结果复用，避免二次 walk 带来双倍 I/O
            all_files = [os.path.abspath(p) for p in iter_files(root, cfg)]
            existing_now: set[str] = set(all_files)

            for path in all_files:
                try:
                    st = os.stat(path)
                    text = read_text(path)
                except (OSError, UnicodeDecodeError):
                    skipped_error += 1
                    continue

                chash = content_hash(text)

                # [P7] 增量优化：先比较 mtime 和 size，再看 hash
                if not args.force and not store.needs_reindex(
                    path, st.st_mtime, st.st_size, chash
                ):
                    # mtime 变化但 hash 相同时，只更新 mtime
                    row = store.conn.execute(
                        "SELECT mtime FROM chunks WHERE path=? LIMIT 1", (path,)
                    ).fetchone()
                    if row and abs(row[0] - st.st_mtime) > 1e-6:
                        store.update_mtime_only(path, st.st_mtime)

                    skipped_uptodate += 1
                    continue

                chunks = []
                for start, end, piece in chunk_text(text, cfg):
                    cid = make_chunk_id(path, start, end)
                    chunks.append(
                        Chunk(
                            chunk_id=cid,
                            path=path,
                            start=start,
                            end=end,
                            text=piece,
                            mtime=st.st_mtime,
                            content_hash=chash,
                            size=st.st_size,
                        )
                    )
                if chunks:
                    indexed_chunks += store.upsert_chunks(chunks)
                    indexed_files += 1

            removed = store.prune_missing(existing_now, root)
            if removed:
                print(f"清理失效 chunk：{removed}")

    except RuntimeError as exc:
        print(f"索引失败：{exc}", file=sys.stderr)
        return 2

    dt = time.time() - t0
    print(
        f"完成：索引 {indexed_files} 个文件 / {indexed_chunks} 个 chunk，"
        f"未变 {skipped_uptodate}，失败 {skipped_error}，耗时 {dt:.2f}s"
    )
    print(f"数据库：{os.path.abspath(cfg.db_path)}")
    return 0


def cmd_fetch_model(args: argparse.Namespace) -> int:
    """[V2-5] 唯一允许联网的子命令：显式下载模型权重。

    下载后所有 index/search 走离线加载（HF_HUB_OFFLINE=1）。
    这是本项目"零上传"红线的唯一例外，且只下载、不上传。
    """
    cfg = load_config(db_path=args.db, offline=False)
    cache_dir = args.cache_dir or cfg.resolved_cache_dir
    model = args.model or cfg.model_name

    os.makedirs(cache_dir, exist_ok=True)
    print(f"模型：{model}")
    print(f"缓存目录：{cache_dir}")
    print("正在下载权重（这是唯一联网操作，只下载不上传）…")

    try:
        from fastembed import TextEmbedding  # type: ignore
    except ImportError:
        venv_py = (
            r".venv\Scripts\python"
            if os.name == "nt"
            else ".venv/bin/python"
        )
        print(
            "错误：未安装 fastembed。请在项目虚拟环境中执行：\n"
            f"  {venv_py} -m pip install -r requirements-v0.2.txt",
            file=sys.stderr,
        )
        return 2

    import time

    t0 = time.time()
    try:
        model_obj = TextEmbedding(model_name=model, cache_dir=cache_dir)
        # 触发一次真实嵌入，确保权重完整可用
        vecs = list(model_obj.embed(["验证权重可用性"]))
        dim = len(vecs[0])
    except Exception as exc:  # noqa: BLE001 — 网络/磁盘/版本错误统一提示
        print(f"下载失败：{exc}", file=sys.stderr)
        print(
            "提示：若为 SSL/网络错误，请稍后重试；"
            "若为 onnxruntime segfault，请确认使用项目 .venv（锁定 1.18.1）。",
            file=sys.stderr,
        )
        return 2

    dt = time.time() - t0
    print(f"完成：权重就绪，向量维度 {dim}，耗时 {dt:.1f}s")
    print(f"接下来 index/search 将离线加载（无需联网）。")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    overrides = {"db_path": args.db, "rrf_k": args.rrf_k}
    if getattr(args, "backend", None):
        overrides["embed_backend"] = args.backend
    if getattr(args, "model", None):
        overrides["model_name"] = args.model
    if getattr(args, "threshold", None) is not None:
        overrides["semantic_threshold"] = args.threshold
    cfg = load_config(**overrides)

    from .store import Store

    with Store(cfg) as store:
        store.init_schema()
        if store.count() == 0:
            print("库为空，请先运行：python -m src.cli index <dir>")
            return 0
        t0 = time.time()
        hits = search(args.query, cfg, store, top_k=args.k, rrf_k=args.rrf_k)
        dt = time.time() - t0

    if not hits:
        print(f"未找到与「{args.query}」相关的内容。（{dt:.3f}s）")
        return 0

    print(f"「{args.query}」 → {len(hits)} 条结果（{dt:.3f}s）\n")
    for i, h in enumerate(hits, start=1):
        snippet = " ".join(h.text.split())
        if len(snippet) > 160:
            snippet = snippet[:160] + "…"
        print(f"{i:>2}. [{h.source}] {h.path}:{h.start}-{h.end}")
        print(f"    {snippet}\n")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    cfg = load_config(db_path=args.db)
    if not os.path.exists(cfg.db_path):
        print(f"数据库不存在：{cfg.db_path}")
        return 0

    from .store import Store

    with Store(cfg) as store:
        store.init_schema()
        s = store.stats()

    print(f"数据库：{os.path.abspath(cfg.db_path)}")
    print(f"文件数：{s['files']}")
    print(f"chunk 数：{s['chunks']}")
    print(f"文本量：{s['text_bytes'] / 1024 / 1024:.2f} MB")
    return 0


def _positive_int(value: str) -> int:
    """[MINOR] 拒绝 <=0 的 -k / --rrf-k。"""
    try:
        ivalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"必须是整数：{value!r}")
    if ivalue <= 0:
        raise argparse.ArgumentTypeError(f"必须 > 0，收到 {ivalue}")
    return ivalue


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="local-vault", description="本地优先的个人数据保险箱 + 语义检索"
    )
    p.add_argument("--db", default="vault.db", help="SQLite 数据库路径（默认 vault.db）")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("index", help="索引一个目录")
    pi.add_argument("directory", help="要索引的目录")
    pi.add_argument("--force", action="store_true", help="忽略增量，强制重索引")
    pi.add_argument(
        "--backend", choices=["hash", "fastembed"], help="嵌入后端（默认 hash）"
    )
    pi.add_argument("--model", help="fastembed 模型名（默认 BAAI/bge-small-zh-v1.5）")
    pi.set_defaults(func=cmd_index)

    ps = sub.add_parser("search", help="语义 + 关键词混合检索")
    ps.add_argument("query", help="查询语句")
    ps.add_argument(
        "-k", type=_positive_int, default=10, help="返回条数（默认 10）"
    )
    ps.add_argument(
        "--rrf-k", type=_positive_int, default=60, help="RRF 常数（默认 60）"
    )
    ps.add_argument(
        "--threshold", type=float, default=0.0, help="语义检索相似度阈值（0.0-1.0，默认 0.0 不过滤）"
    )
    ps.add_argument(
        "--backend", choices=["hash", "fastembed"], help="嵌入后端（须与索引时一致）"
    )
    ps.add_argument("--model", help="fastembed 模型名（须与索引时一致）")
    ps.set_defaults(func=cmd_search)

    pt = sub.add_parser("stats", help="查看库统计")
    pt.set_defaults(func=cmd_stats)

    pf = sub.add_parser(
        "fetch-model", help="[唯一联网命令] 下载 fastembed 模型权重"
    )
    pf.add_argument("--model", help="模型名（默认 BAAI/bge-small-zh-v1.5）")
    pf.add_argument("--cache-dir", help="权重缓存目录（默认 ~/.cache/local-vault/models）")
    pf.set_defaults(func=cmd_fetch_model)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
    except FileNotFoundError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
