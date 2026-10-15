"""src/cli.py — 命令行入口，只做编排，不含业务逻辑。

用法：
    python -m src.cli index  <dir> [--force]
    python -m src.cli search "<query>" [-k 10]
    python -m src.cli stats
退出码：0 成功 / 1 参数错误 / 2 索引失败
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time

from .config import load_config
from .indexer import chunk_text, content_hash, iter_files, read_text
from .search import search


def cmd_index(args: argparse.Namespace) -> int:
    cfg = load_config(db_path=args.db)
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
                except OSError:
                    skipped_error += 1
                    continue

                chash = content_hash(text)
                if not args.force and not store.needs_reindex(
                    path, st.st_mtime, chash
                ):
                    skipped_uptodate += 1
                    continue

                chunks = []
                for start, end, piece in chunk_text(text, cfg):
                    cid = hashlib.sha1(
                        f"{path}:{start}:{end}".encode("utf-8")
                    ).hexdigest()
                    chunks.append(
                        Chunk(
                            chunk_id=cid,
                            path=path,
                            start=start,
                            end=end,
                            text=piece,
                            mtime=st.st_mtime,
                            content_hash=chash,
                        )
                    )
                if chunks:
                    indexed_chunks += store.upsert_chunks(chunks)
                    indexed_files += 1

            removed = store.prune_missing(existing_now)
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


def cmd_search(args: argparse.Namespace) -> int:
    cfg = load_config(db_path=args.db, rrf_k=args.rrf_k)

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
    pi.set_defaults(func=cmd_index)

    ps = sub.add_parser("search", help="语义 + 关键词混合检索")
    ps.add_argument("query", help="查询语句")
    ps.add_argument(
        "-k", type=_positive_int, default=10, help="返回条数（默认 10）"
    )
    ps.add_argument(
        "--rrf-k", type=_positive_int, default=60, help="RRF 常数（默认 60）"
    )
    ps.set_defaults(func=cmd_search)

    pt = sub.add_parser("stats", help="查看库统计")
    pt.set_defaults(func=cmd_stats)

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
