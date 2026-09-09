# Logseq housekeeper agent contract

## Operating Standard
- Apply `C:\Users\juanm\Documents\GitHub\Vibe Coding Rules 10.md` (V10) as the repository operating standard; read it in full before substantive work.
- This file is the nearest-owning contract. It refines the parent policy with repository-specific facts and cannot weaken a mandatory parent rule; conflicts resolve to the parent.

## Scope and Ownership
- `logseq_housekeeper.py` — the whole app in one module (stdlib + optional `rich`). Owns page indexing, scanning, confidence scoring, the interactive review/apply menus, and atomic writes. Entry point is `main()` (~line 1190); flags are `--graph-path`, `--config`, `--plain`.
- Interactive menu has six actions: scan, review suggestions, apply approved, auto-apply HIGH, show rejected/ambiguous, export report.
- Flow semantics: scan builds the index and mention list; review works per file with a (accept all), r (reject all), n (skip), `<n>` (toggle one), q (quit); apply writes only approved suggestions; auto-apply batch-approves every HIGH suggestion without per-item review but still requires a confirmation prompt; show rejected/ambiguous lists rejected and pending-LOW mentions for spotting missed links or false positives.
- Scanning skips text already inside `[[wikilinks]]`, but a title that appears twice in a file — once linked and once plain — still gets a suggestion for the second mention; this is intentional.
- No source module or helper owns behavior outside this file; keep the single-module, no-build design. Scan reads every `.md` file in `pages/`, `journals/` (when enabled), and `wiki/` (when enabled), where the graph root is the directory containing `pages/`.
- Apply wraps each matched text in `[[ ]]` brackets and is idempotent: re-applying an already-applied suggestion is a safe no-op.
- Export (menu 6) writes `housekeeping/link-suggestions.json` into the graph root with every suggestion, its confidence, and its accepted/rejected decision; it is the audit record for missed links and false positives.
- Page index is built from `.md` filenames (URL-decoded) plus `type::`, `alias::`, `aliases::`, and `title::` properties; `title::` values are registered as aliases (Logseq display-title semantics). Alias mentions resolve to their target page and are wrapped as `[[alias]]` so Logseq resolves them via the target's alias declaration.
- Confidence scoring (durable behavior): HIGH applies on multi-word title match, any match on a `wiki/` page, or a target with `type:: person|company|book` (also `[[person]]`-style values); MEDIUM on a single-word uppercase proper noun; LOW on single-word lowercase. Confidence reflects the resolved target page, not the match type.
- Never suggested at all: ambiguous aliases resolving to more than one page, contaminated blocklisted words, mentions in protected zones, and self-links.
- `housekeeper.config.json` — runtime config read at startup: `graph_path`, `max_links_per_file` (20), `include_journals` (true), `include_wiki` (true). `--graph-path` overrides the config value.
- `requirements.txt` — declares the sole runtime dependency `rich>=13.0`; it is optional. Without rich, or with `--plain`, the app degrades to plain `print()`/`input()` menus with identical functionality.
- `README.md` — authoritative user documentation: install, CLI reference, config fields, confidence scoring, safety guarantees.
- `PLAN.md` — a planning document, not a contract or spec; do not treat it as authority.
- `__pycache__` — generated artifact; never edit or commit.

## Constraints
- The tool edits the user's Logseq graph (its `pages/`, `journals/`, `wiki/` directories), which lives outside this repo. Its only writes are `[[...]]` edits to selected `.md` files and `housekeeping/link-suggestions.json` in the graph root.
- Never link inside protected zones: existing `[[wikilinks]]`, `((block-refs))`, code fences, all `#+BEGIN_*` / `#+END_*` blocks (e.g. `#+BEGIN_QUERY`, `#+BEGIN_SRC`), HTML comments, URLs, markdown links, property lines, and `#tags` are skipped.
- Dry-run by default: the app never modifies a file without explicit confirmation, including auto-apply. Writes are atomic (temp file → `os.replace()`); never truncate a file in place.
- Do not auto-link contaminated/short blocklisted words (e.g. `A`, `OR`, `what`, `time`, `people`). A page title is linked at most once per file; max 20 new links per file per run; a page never links to itself.
- Keep the no-build, minimal-dependency design. Do not add frameworks or new dependencies without need; `rich` must stay optional.
- Unreadable graph files (bad encoding) are skipped with a warning rather than aborting the scan. `--graph-path` is required and must be an existing directory, or the tool prints an error and exits 1.
- The app never stages, commits, or pushes: the target graph is the user's own git repository and reverts are the user's job (`git diff` / `git checkout`). Do not add any git or commit behavior to the tool.

## Verification
- No test harness exists (no test directory, no Makefile, no test framework in `requirements.txt`). Smallest runnable checks:
  - `python -m py_compile logseq_housekeeper.py` — verifies syntax without executing.
  - `python logseq_housekeeper.py --help` — confirms the argparse CLI is live with `--graph-path`, `--config`, `--plain`.
  - `python logseq_housekeeper.py --graph-path <missing>` prints an error and exits 1 — a fail-closed check that path validation is enforced.
  - Smoke run: point at a scratch copy of a graph (`python logseq_housekeeper.py --graph-path <copy>`), run scan (menu 1), then apply (menu 3 or 4); it prints a "Dry-run: N files, M links" summary and requires confirmation before any write.
- For UI work in the rich or plain menus, exercise an actual interactive session against a scratch graph and confirm prompts, per-file review commands (a/r/n/`<n>`/q), exit paths, and that no file changes without confirmation.
- If rich menus garble in an unusual terminal or under redirected output, `--plain` forces an ASCII-only menu; prefer it for scripted runs.

## Documentation index
- `README.md` — the only written documentation: installation, quick start, workflow, CLI reference, configuration, confidence scoring, safety guarantees, plain mode, FAQ. Contract facts above distill from `README.md` and `logseq_housekeeper.py`.

## Known gaps
- No automated test suite exists; the contaminated-word blocklist lives in the hardcoded `CONTAMINATED_WORDS` set inside `logseq_housekeeper.py` and confidence thresholds are code-level, neither configurable via `housekeeper.config.json`.
