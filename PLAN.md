# Plan: Fix Logseq Housekeeper so it can be used

## Context

`logseq_housekeeper.py` scans Logseq graphs for unlinked wiki mentions and suggests/inserts `[[wikilinks]]`. It's a single-file CLI app (rich TUI + plain fallback). Review + scripted testing against a fake graph revealed that several core features are silently broken and the source file itself is textually corrupted:

1. **Property extraction never works** — `PageIndex._extract_properties` captures `m.group(1)` which is the *value*, not the key. Result: `type::`, `alias::`, `aliases::` are ignored everywhere. `page_type` is always `""` and alias tables are always empty (verified: fake graph indexes 4 pages with zero aliases/types).
2. **Alias dedup drops every alias** — `Scanner._scan_file` dedups candidate entries by `e[1]` (target title) instead of `e[0]` (match string). Since a target page's title is always in the list, every alias resolving to it is discarded (verified: alias test produced 0 suggestions).
3. **`TUI._export_report` crashes** — references `self.graph_path`, which doesn't exist on `TUI` (only `self.index.graph_path`). Menu option 6 raises `AttributeError` (verified).
4. **File-wide mojibake** — the .py source is UTF-8 whose bytes were at some point decoded as cp1252: `â”€` (box-drawing `─`), `Â·` (`·`), `Ã—` (`×`), `â€”` (`—`). User-visible strings in the TUI print garbage on UTF-8 terminals. Verified the exact inverse: `read utf-8-sig → encode cp1252 → decode utf-8` restores all characters correctly and still compiles.
5. **Windows robustness** — `load_config` uses locale encoding (`open(config, "r")`): on Windows/cp1252 a UTF-8 config with non-ASCII silently fails and defaults are used. `.md` files are read with `utf-8` (BOM-unsafe) and an unreadable file aborts the whole scan.
6. **Performance** — the combined match regex (all candidate titles) is rebuilt per file inside `_scan_file`; with a ~2000-page graph × hundreds of files this dominates runtime. Also `main()` builds the index twice (startup + first scan).

Minor: `unique_id` never set in `PlainTUI`, dead `if t.endswith(".md")` code, `#+BEGIN_SRC` blocks not treated as protected zones (only `#+BEGIN_QUERY`).

## Approach

Single-file change set in `logseq_housekeeper.py` (the only code file). No API/CLI changes; README and config stay valid.

## Files to modify

- `logseq_housekeeper.py` — all fixes
- (no other files)

## Steps

1. **Repair the file encoding.** Read with `utf-8-sig`, apply `raw.encode('cp1252').decode('utf-8')`, strip the BOM, write back as clean UTF-8 with LF line endings. Verified result: only `─` (comments), `·` ×7, `×` ×1, `—` ×2 remain — all printed strings are cp1252-safe so Windows consoles keep working. Confirm with `git diff` that only mojibake/whitespace changed on unaffected lines.
2. **Fix property extraction** (`PageIndex._extract_properties`): capture key and value in two groups (`(title|type|alias|aliases)\s*::\s*(.+)`), leading `\s*` to allow `  - type:: x`, use `m.group(1)` as key / `m.group(2)` as value. Strip `[[ ]]` from `type::` values. Register `title::` values as aliases (Logseq resolves links to the display title). Keep filename as the canonical title.
3. **Fix alias dedup** (`Scanner._scan_file`): dedup candidate entries on `e[0]` (lowercase match string) instead of `e[1]` (target title).
4. **Fix `TUI._export_report`**: `self.graph_path` → `self.index.graph_path` (matches `PlainTUI._export_report`).
5. **Build the match regex once per scan**: move candidate/alias collection + pattern compile from `_scan_file` into `scan_all()`, pass the compiled pattern into `_scan_file`. Drop the redundant `index.build()` + print in `main()` (TUI/PlainTUI already build on first scan).
6. **Robustness**:
   - `load_config`: `open(config_path, "r", encoding="utf-8-sig")`.
   - Read `.md` files with `utf-8-sig` in `_index_file` and `_scan_file`.
   - `scan_all`: wrap per-file scan in try/except; record errors in `Scanner.errors`, print a warning in both TUIs after scan instead of aborting.
   - Treat `#+BEGIN_SRC`…`#+END_SRC` (and other `#+BEGIN_*`/`#+END_*` block pairs) as protected zones alongside `#+BEGIN_QUERY`.
7. **Cleanup**:
   - Set `unique_id` properly in `Scanner` (`f"{path}:{line}:{col}"`); drop the redundant re-assignment in `TUI._do_scan`; add the same in `PlainTUI._do_scan`.
   - Remove dead `if t.lower().endswith(".md")` branch in `_decode_title`.

## Verification

1. `python -m py_compile logseq_housekeeper.py` and `python logseq_housekeeper.py --help`.
2. Scripted fake-graph test (temp dir, removed afterwards):
   - pages with `type::`/`alias::`/`title::` properties → `PageIndex` populates `page_type` and aliases; `title::` becomes an alias.
   - Scan finds: multi-word title mentions (HIGH), alias mentions (`Apple`, `AAPL`, `WB`) resolving to the right target, `type:: person|company` boosting confidence to HIGH, self-mentions skipped, mentions inside `[[...]]`, `#tags`, code fences and `#+BEGIN_SRC` blocks skipped, per-file/per-target caps respected.
   - `Applier.apply` wraps text correctly and is idempotent (re-apply makes no change).
   - `TUI._export_report` completes without `AttributeError`; report JSON written.
3. Full scripted end-to-end run of both TUIs via piped stdin (scan → review/accept → apply with dry-run confirm → export → quit), on the fake graph; assert file contents contain the `[[links]]`.
4. Confirm `git diff` shows only intended changes (mojibake repair + logic fixes), and no non-ASCII garbage remains in any printed string.
