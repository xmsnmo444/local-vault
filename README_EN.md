# local-vault — Your Files, Stay on Your Machine, Searchable in One Sentence

> **Privacy first: data never leaves your machine, zero upload, offline by default.** No code path sends your files to the network. `cfg.offline=True` throws `OfflineViolation` on any network attempt. Works fully offline.
> **v0.2 with real semantics**: `fastembed + bge-small-zh-v1.5` (512d), e.g. "how to braise pork" finds "red-braised pork recipe" — no longer keyword-only.

## In one sentence

Scan scattered files (`txt/md/py/json/csv/log/html` etc., 17 types) → filter binaries → chunk (512/64 overlap) → embed locally (hash or fastembed) → single SQLite file (`vault.db`) → `search` your words back. Portable, backup-friendly.

---

## Installation

### Path A: Zero deps (hash backend, clone & run)

```bash
pip install -r requirements.txt   # pytest only
python -m src.cli --db vault.db index demo --backend hash
python -m src.cli --db vault.db search "reverse" --backend hash
```

### Path B: Real semantics (v0.2 recommended)

```bash
# 1. Isolated venv (important: avoids onnxruntime conflicts)
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 2. Install (pinned onnxruntime==1.18.1)
pip install -r requirements-v0.2.txt

# 3. Download weights once (the ONLY online step, ~90MB)
python -m src.cli fetch-model
# → done: 512d, 0.1s if already cached

# 4. Fully offline from now on
python -m src.cli --db vault.db index demo --backend fastembed
python -m src.cli --db vault.db search "how to make braised pork tasty" --backend fastembed -k 5
# → [hybrid] cook.txt ✅ (zero word overlap still hits)
```

> **Why isolated .venv?** Some `onnxruntime 1.31.0` builds segfault with `fastembed`. We pin `onnxruntime==1.18.1 + numpy==1.26.4 + fastembed==0.9.0` inside `.venv`; your global env (`numpy 2.5.4 / onnxruntime 1.31.0`) is untouched. See `docs/CONTRACTS.md` v0.2.

Requires: Python ≥ 3.10, SQLite (builtin).

---

## Quick Start

```bash
# 1. Index a folder (recursive, skips .git/node_modules/__pycache__ etc.)
python -m src.cli --db vault.db index demo --backend hash
# → indexed 4 files / 4 chunks

# 2. Search (Top-K, semantic + BM25 RRF)
python -m src.cli --db vault.db search "reverse" -k 3 --backend hash
# → 1. [hybrid] demo/re.md:0-52  Reverse notes: APK unpack, smali, Frida hook...

# 3. Stats
python -m src.cli --db vault.db stats

# 4. Incremental / orphan GC is automatic
python -m src.cli --db vault.db index demo --backend hash
# → indexed 0 files / 0 chunks, unchanged 4

python -m src.cli --db vault.db index demo --force --backend hash  # force full rebuild
```

Programmatic:

```python
from src.config import load_config
from src.store import Store
from src.search import search

cfg = load_config(db_path="vault.db", embed_backend="fastembed")
with Store(cfg) as store:
    store.init_schema()
    hits = search("how to write Frida hook", cfg, store, top_k=5)
    for h in hits:
        print(h.source, h.path, h.text[:80])
```

---

## Commands

```
python -m src.cli --db <path> index <dir> [--force] [--backend hash|fastembed] [--model NAME]
python -m src.cli --db <path> search "<query>" [-k 10] [--rrf-k 60] [--backend ...] [--model NAME]
python -m src.cli --db <path> stats
python -m src.cli fetch-model [--model NAME] [--cache-dir DIR]   # the ONLY online command
```

* `fetch-model`: downloads to `~/.cache/local-vault/models` (no upload), then `index/search` load with `HF_HUB_OFFLINE=1` (0.1s).
* Missing weights + `offline=True` → clear error asking for `fetch-model`, **never silently falls back** to hash.
* `bge-small-zh-v1.5` is **512d** (hash is 256); changing model without re-index throws `RuntimeError`.

Exit codes: `0` ok / `1` bad args / `2` index failed / `130` Ctrl+C.

Env vars (optional): `LV_DB_PATH`, `LV_OFFLINE` (0/false/no to disable), `LV_EXTS` (comma list).

---

## Privacy & Offline

* **Zero-upload invariant**: `rg -n "requests\.post|urllib\.request|http\.client|telemetry" src/` → zero hits. CI fails on hit.
* **Runtime guard**: `Store.__init__` patches `socket.connect/getaddrinfo/create_connection`; refcounted, removed on close.
* **Offline semantics**: `offline=True` **allows loading local weights offline**, only blocks downloading.
* **Single file DB**: `vault.db` SQLite, float32 BLOB, cosine in-process.

See `docs/CONTRACTS.md` checklist, `docs/REVIEW.md` `[CRITICAL]` veto.

---

## Platform Support

| Platform | Status | Activate venv |
|---|---|---|
| Windows | ✅ primary | `.venv\Scripts\activate` |
| macOS (Intel / Apple Silicon) | ✅ macOS 11.0+ | `source .venv/bin/activate` |
| Linux | ✅ | `source .venv/bin/activate` |

CI tests on `ubuntu-24.04`, `macos-15-intel` (Intel), `macos-15` (Apple Silicon). All deps have prebuilt wheels.

---

## FAQ

**How much better is v0.2?**
Zero-overlap query `how to cook pork deliciously`: hash ranks correct doc #2 at `0.03 ≈ noise`, fastembed ranks #1 at `0.62` (next `0.18`).

**Re-index after changing model?** Yes. `meta` stores `vec_dim/embed_backend/model_name`; mismatch → `RuntimeError`.

**How to verify offline?** `.venv\Scripts\python -m pytest tests -q` → `24 passed` (7 offline guards + 4 semantic). Or disconnect and run `index/search`.

---

## Development

```bash
python -m pytest tests -q          # 24 passed (semantic tests auto-skip without weights)
.venv\Scripts\python -m pytest tests -q   # 24 passed with weights

rg -n "requests|urllib|socket" src/  # zero hits
```

CI: GitHub Actions `test` (unshare -rn offline) + `privacy` (ripgrep).

---

## License

MIT — see `LICENSE`.

---

## One-click setup

Windows: `powershell -ExecutionPolicy Bypass -File setup.ps1`
macOS/Linux: `chmod +x setup.sh && ./setup.sh`
