# Logseq Housekeeper

Auto-scan Logseq graphs for unlinked wiki mentions and insert sensible `[[wikilinks]]`.

Given a Logseq graph with hundreds of source pages and a curated wiki, there are always plain-text mentions of existing pages that should be linked but aren't. This tool finds them, scores them by confidence, and lets you review and apply with one command.

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Workflow](#workflow)
- [Walkthrough](#walkthrough)
- [CLI Reference](#cli-reference)
- [Configuration](#configuration)
- [Confidence Scoring](#confidence-scoring)
- [Safety Guarantees](#safety-guarantees)
- [Plain Mode](#plain-mode)
- [FAQ / Troubleshooting](#faq--troubleshooting)

## Installation

```bash
python -m venv venv
source venv/bin/activate    # or venv\Scripts\activate on Windows
pip install rich
```

`rich` is the only dependency. Without it the app falls back to `print()`/`input()` via `--plain`.

## Quick Start

```bash
python logseq_housekeeper.py --graph-path /path/to/your/logseq/graph
```

This builds a page index from the graph, then opens an interactive menu:

```
  [1] Scan graph for unlinked mentions
  [2] Review suggestions (0 pending)
  [3] Apply approved suggestions
  [4] Auto-apply high-confidence suggestions
  [5] Show rejected / ambiguous
  [6] Export report
  [q] Quit
```

Typical session: **1 (scan)** → **4 (auto-apply high)** → **2 (review remaining)** → **3 (apply)**.

## Workflow

### 1. Scan

Reads every `.md` file in `pages/`, `journals/`, and `wiki/` to build two things:

- **Page index** — all page titles from filenames (URL-decoded), plus `title::`, `alias::`, and `aliases::` properties. Contaminated words (`A`, `OR`, `time`, `people`, etc.) are flagged and excluded from auto-linking.
- **Suggestion list** — every plain-text mention of an existing page title found outside protected zones.

### 2. Review

Shows suggestions grouped by file. For each group you can:

- **a** — accept all suggestions in this file
- **r** — reject all
- **n** — skip to next file
- **`<n>`** — toggle an individual suggestion by number
- **q** — quit review

### 3. Apply

Writes approved suggestions to disk. Each edit wraps the matched text in `[[ ]]` brackets. Writes are atomic: temp file → `os.replace()`. No file is truncated in place.

### 4. Auto-apply (high confidence)

Batch-approves all HIGH-confidence suggestions without per-item review. Still requires a confirmation prompt before writing.

### 5. Show rejected / ambiguous

Lists suggestions you rejected and LOW-confidence suggestions still pending — useful for spotting missed links or false positives.

### 6. Export report

Writes `housekeeping/link-suggestions.json` in the graph root with every suggestion, its confidence, and whether it was accepted.

## Walkthrough

```bash
$ python logseq_housekeeper.py --graph-path ~/Logseq/my-graph

=== Logseq Housekeeper ===

[1] Scan graph for unlinked mentions
[2] Review suggestions (0 pending)
[3] Apply approved suggestions
[4] Auto-apply high-confidence suggestions
[5] Show rejected / ambiguous
[6] Export report
[q] Quit

> 1
  Indexing pages...     1950 pages indexed
  Scanning files...      883 unlinked mentions found
                          236 high | 276 med | 371 low

> 4
  236 high-confidence suggestions across 31 files.
  Auto-apply these 236 high-confidence links? [y/N]: y
  Applied 236 links across 31 files.

> 2
  pages/Some Article.md
  14 unlinked mentions

    [1] HIGH  -> [[Warren Buffett]]
         ...mentioned [[Warren Buffett]] in his annual...
    [2] MEDIUM -> [[Moat]]
         ...a durable [[Moat]] is the key to...
    ...

  [a] Accept all  [r] Reject  [n] Next  [q] Quit  [<n>] Toggle
  > a

  pages/Another Page.md
  ...

> 3
  142 links across 12 files ready to apply.
  This will modify your Logseq files. Continue? [y/N]: y
  Dry-run: 12 files, 142 links
  Proceed with actual write? [y/N]: y
  Applied 142 links across 12 files.
```

## CLI Reference

| Flag | Default | Description |
|------|---------|-------------|
| `--graph-path` | `""` (required) | Path to Logseq graph root (dir containing `pages/`) |
| `--config` | `housekeeper.config.json` | Path to JSON config file |
| `--plain` | `false` | Force plain terminal UI (no rich formatting). Use when the Windows console can't render Unicode characters. |

The `--graph-path` value can also be set in the config file so you don't need to pass it every time.

## Configuration

`housekeeper.config.json` in the project root:

```json
{
  "graph_path": "",
  "max_links_per_file": 20,
  "include_journals": true,
  "include_wiki": true
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `graph_path` | `""` | Absolute path to the Logseq graph directory. Overridden by `--graph-path` CLI flag. |
| `max_links_per_file` | `20` | Maximum number of new wikilinks to add per file in a single run. |
| `include_journals` | `true` | Scan journal files in `journals/` for unlinked mentions. Set to `false` if you only want to scan `pages/` and `wiki/`. |
| `include_wiki` | `true` | Scan wiki files in `wiki/` for unlinked mentions. |

## Confidence Scoring

**HIGH** — applied when you run auto-apply.

- Multi-word page title match (2+ words)
- Single-word wiki page (person, company, book, concept)
- Page has `type:: person|company|book` in frontmatter

**MEDIUM** — reviewed manually.

- Single-word proper noun (starts with uppercase)
- Alias that resolves to exactly one page

**LOW** — never auto-applied, shown in review.

- Single-word generic match
- Ambiguous alias (resolves to multiple pages)
- Same lowercase form as a contaminated word

## Safety Guarantees

- **Dry-run by default.** The app never modifies files without explicit confirmation.
- **Never links inside protected zones.** Existing `[[wikilinks]]`, `((block-refs))`, code fences, `#+BEGIN_QUERY` blocks, HTML comments, URLs, markdown links, and `#tags` are all skipped.
- **Contaminated-word blocklist.** Short/garbage words (`A`, `OR`, `what`, `time`, `people`, etc.) from the old linkifier are permanently blocked from auto-linking.
- **First-mention only.** A page title is linked at most once per file, preventing over-linking.
- **Per-file cap.** At most 20 new links per file (configurable).
- **Self-link skip.** A page never links to itself.
- **Atomic writes.** Temp file → `os.replace()`. No truncation-in-place.
- **Full report.** Every suggestion, decision, and applied link is written to `housekeeping/link-suggestions.json`.

## Plain Mode

Pass `--plain` when the terminal can't render rich formatting (common on Windows cmd/PowerShell with certain Unicode characters):

```bash
python logseq_housekeeper.py --graph-path /path --plain
```

This replaces the rich TUI with a simple `print()`/`input()` menu. All functionality is identical.

## FAQ / Troubleshooting

**Q: I see garbled characters in the menu.**  
A: The Windows console (`cp1252`) cannot print all Unicode characters. Use `--plain` to force ASCII-only output.

**Q: The app found links that already exist.**  
A: The scanner skips text inside existing `[[wikilinks]]`, but a mention like `Charlie Munger` that appears twice in a file — once already linked and once not — will suggest linking the second one. This is intentional: the first is already linked, the second is a legitimate miss.

**Q: How do I undo applied links?**  
A: Your graph is a git repo. Run `git diff` to see changes and `git checkout -- .` to revert. The app doesn't commit for you.

**Q: Can I scan only pages and not journals?**  
A: Set `"include_journals": false` in `housekeeper.config.json`.

**Q: I don't want the default blocklist.**  
A: The blocklist is hardcoded in `logseq_housekeeper.py` (the `CONTAMINATED_WORDS` set). You can edit it there.
