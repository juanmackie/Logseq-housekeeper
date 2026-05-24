#!/usr/bin/env python3
"""
Logseq Housekeeper â€” auto-scan Logseq graphs for unlinked wiki mentions.
Lean V1: dry-run default, CLI TUI, manual review + auto-apply high confidence.

Usage:
    python logseq_housekeeper.py --graph-path <path_to_logseq_graph>
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import textwrap
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskID
    from rich.syntax import Syntax
    from rich import box
    from rich.text import Text as RichText

    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# â”€â”€ Constants â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

EXCLUDE_DIRS: set[str] = {
    ".git", "logseq", "node_modules", ".recycle", ".bak", "__pycache__",
}

CONTAMINATED_WORDS: set[str] = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "should", "could", "may", "might", "must", "can", "it", "its",
    "this", "that", "these", "those", "i", "you", "he", "she", "we", "they",
    "my", "your", "his", "her", "our", "their", "me", "him", "us", "them",
    "what", "when", "where", "why", "how", "which", "who", "whom",
    "time", "people", "life", "work", "value", "good", "best", "system",
    "or", "as", "be", "if", "so", "no", "up", "out", "about", "just",
    "also", "very", "too", "much", "more", "most", "some", "any", "all",
    "each", "every", "both", "few", "many", "own", "same", "such",
    "not", "only", "than", "then", "now", "here", "there", "well",
    "back", "over", "still", "even", "because", "while", "though",
    "value", "investing", "business", "market", "company",
    "capital", "management", "growth", "risk", "price",
}

SHORT_ALLOWLIST: set[str] = {
    "ai", "agp", "bfi", "cfm", "crm", "csr", "dcf", "ebit", "ebitda",
    "erp", "fba", "fpaas", "gpt", "hr", "ipo", "kpi", "llm", "llms",
    "mcp", "moc", "noc", "ocr", "oi", "pb", "pe", "ps", "rag", "rlhf",
    "roi", "rpa", "sde", "sec", "soc", "sox", "uk", "us", "ui", "ux",
    "vc", "vms", "xr", "agi", "s&p",
}

DEFAULT_MAX_LINKS_PER_FILE = 20
DEFAULT_MIN_LINK_LENGTH = 3

# â”€â”€ Data Structures â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@dataclass
class PageDef:
    """One page in the Logseq graph."""
    title: str
    source_dir: str  # "pages", "journals", "wiki"
    filepath: Path
    page_type: str = ""  # "person", "company", "concept", "topic", etc.
    aliases: list[str] = field(default_factory=list)
    contaminated: bool = False


@dataclass
class Suggestion:
    """One suggested wikilink insertion."""
    filepath: Path
    line_index: int
    column: int
    end_column: int
    target_title: str
    matched_text: str
    confidence: str  # "HIGH", "MEDIUM", "LOW"
    reason: str
    context_before: str = ""
    context_after: str = ""
    accepted: Optional[bool] = None
    unique_id: str = ""


# â”€â”€ Page Index â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class PageIndex:
    """Builds and queries an index of all pages and aliases in the graph."""

    def __init__(self, graph_path: Path):
        self.graph_path = graph_path
        self.by_lower: dict[str, PageDef] = {}
        self.alias_targets: dict[str, list[str]] = {}
        self.errors: list[str] = []

    def build(self) -> int:
        self.by_lower.clear()
        self.alias_targets.clear()
        self.errors.clear()

        for source_dir in ("pages", "journals", "wiki"):
            root = self.graph_path / source_dir
            if not root.is_dir():
                continue
            for md in sorted(root.rglob("*.md")):
                rel = md.relative_to(self.graph_path)
                parts = rel.parts
                if any(p in EXCLUDE_DIRS or p.startswith(".") for p in parts):
                    continue
                self._index_file(md, source_dir)

        return len(self.by_lower)

    def _index_file(self, path: Path, source_dir: str):
        stem = path.stem
        # Some Logseq files use percent encoding in names
        title = self._decode_title(stem)
        if not title:
            return

        is_wiki = source_dir == "wiki"
        page = PageDef(title=title, source_dir=source_dir,
                       filepath=path, page_type="")

        try:
            content = path.read_text("utf-8")
            self._extract_properties(content, page)
        except Exception:
            pass

        lower = title.lower()
        # Mark if contaminated
        words = lower.split()
        if len(words) == 1 and lower in CONTAMINATED_WORDS:
            page.contaminated = True
        if len(words) == 1 and len(lower) < DEFAULT_MIN_LINK_LENGTH and lower not in SHORT_ALLOWLIST:
            page.contaminated = True
        # Multi-word contaminated check
        if all(w in CONTAMINATED_WORDS for w in words if len(w) > 1):
            page.contaminated = True

        self.by_lower[lower] = page

        for alias in page.aliases:
            al = alias.lower()
            self.alias_targets.setdefault(al, []).append(title)

    def _decode_title(self, stem: str) -> str:
        t = unquote(stem)
        # Strip trailing .md if present
        if t.lower().endswith(".md"):
            t = t[:-3]
        return t

    def _extract_properties(self, content: str, page: PageDef):
        for line in content.splitlines():
            s = line.strip()
            m = re.match(
                r'^(?:-\s+)?(?:title|type|alias|aliases)\s*::\s*(.+)$',
                s, re.IGNORECASE
            )
            if not m:
                continue
            key = m.group(1).lower().lstrip()
            value = m.group(1).strip()
            if key.startswith("title"):
                continue
            if key.startswith("type"):
                page.page_type = value.strip()
                continue
            if key.startswith("alias"):
                parts = re.split(r'[,;]', value)
                for p in parts:
                    p = p.strip()
                    wls = re.findall(r'\[\[([^\]]+)\]\]', p)
                    if wls:
                        for w in wls:
                            tgt = w.split("|")[0].strip()
                            if tgt and len(tgt) > 1:
                                page.aliases.append(tgt)
                    else:
                        if p and len(p) > 1:
                            page.aliases.append(p)

    def candidates(self) -> list[PageDef]:
        """Return non-contaminated candidate titles for linking."""
        return [p for p in self.by_lower.values() if not p.contaminated]

    def has_page(self, lower_title: str) -> bool:
        return lower_title in self.by_lower

    def resolve_alias(self, alias_lower: str) -> Optional[str]:
        t = self.alias_targets.get(alias_lower, [])
        if len(t) == 1:
            return t[0]
        return None


# â”€â”€ Scanner â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class Scanner:
    """Find unlinked mentions of known pages in the graph."""

    FENCE_PAT = re.compile(r'^(?:`{3,}|~{3,})')
    QUERY_START = re.compile(r'^#\+BEGIN_QUERY')
    QUERY_END = re.compile(r'^#\+END_QUERY')
    COMMENT_START = re.compile(r'<!--')
    COMMENT_END = re.compile(r'-->')
    PROPERTY_LINE = re.compile(r'^\s*(?:-\s+)?\w[\w-]*\s*::\s')
    TAG_PAT = re.compile(r'(?<!\w)#\w[\w-]*\b')
    LINK_PAT = re.compile(r'\[\[([^\]]+)\]\]')
    REF_PAT = re.compile(r'\(\(([^)]+)\)\)')
    URL_PAT = re.compile(r'https?://\S+')
    MD_LINK_PAT = re.compile(r'\[([^\]]*)\]\(([^)]*)\)')

    def __init__(self, index: PageIndex, graph_path: Path, config: dict):
        self.index = index
        self.graph_path = graph_path
        self.config = config
        self.max_per_file = config.get("max_links_per_file", DEFAULT_MAX_LINKS_PER_FILE)

    def scan_all(self) -> list[tuple[Path, list[Suggestion]]]:
        """Scan all source files and return (filepath, suggestions) pairs."""
        results: list[tuple[Path, list[Suggestion]]] = []
        source_dirs = ["pages"]
        if self.config.get("include_journals", True):
            source_dirs.append("journals")
        if self.config.get("include_wiki", True):
            source_dirs.append("wiki")

        for sd in source_dirs:
            root = self.graph_path / sd
            if not root.is_dir():
                continue
            for md in sorted(root.rglob("*.md")):
                rel = md.relative_to(self.graph_path)
                if any(p in EXCLUDE_DIRS or p.startswith(".") for p in rel.parts):
                    continue
                sug = self._scan_file(md)
                if sug:
                    results.append((md, sug))
        return results

    def _scan_file(self, path: Path) -> list[Suggestion]:
        content = path.read_text("utf-8")
        lines = content.splitlines()

        # Build the combined regex for all candidate titles
        candidates = self.index.candidates()
        entries: list[tuple[str, str, int, int]] = []  # (lower, original, word_count, length)
        for p in candidates:
            wc = len(p.title.split())
            entries.append((p.title.lower(), p.title, wc, len(p.title)))

        # Add aliases that resolve uniquely
        for alias_lower, titles in self.index.alias_targets.items():
            if len(titles) == 1:
                tgt = titles[0]
                tgt_page = self.index.by_lower.get(tgt.lower())
                if tgt_page and not tgt_page.contaminated:
                    # Check not already covered by a title match
                    if not any(e[0] == alias_lower for e in entries):
                        entries.append((alias_lower, tgt, len(alias_lower.split()), len(alias_lower)))

        # Deduplicate and sort by length descending (longest match first)
        seen_titles: set[str] = set()
        deduped: list[tuple[str, str, int, int]] = []
        for e in entries:
            if e[1] not in seen_titles:
                seen_titles.add(e[1])
                deduped.append(e)
        deduped.sort(key=lambda x: -x[2])

        # Build combined pattern
        if not deduped:
            return []

        pieces = []
        for lower, original, wc, ll in deduped:
            pieces.append(re.escape(lower))
        alt = re.compile(
            r'(?<!\w)(' + "|".join(pieces) + r')(?!\w)',
            re.IGNORECASE
        )

        file_suggestions: list[Suggestion] = []
        file_title = self._decode_file_title(path)
        links_this_file: dict[str, int] = defaultdict(int)

        self._track_protected_zones(lines, file_suggestions, alt, links_this_file, file_title.lower() if file_title else "")

        return file_suggestions

    def _decode_file_title(self, path: Path) -> str:
        stem = path.stem
        return unquote(stem)

    def _track_protected_zones(
        self, lines: list[str],
        suggestions: list,
        alt_pattern: re.Pattern,
        links_this_file: dict,
        file_title_lower: str,
    ):
        in_fence = False
        in_query = False
        in_comment = False

        for li, line in enumerate(lines):
            stripped = line.strip()

            # â”€â”€ Zone transitions â”€â”€
            if self.FENCE_PAT.match(stripped):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if self.QUERY_START.match(stripped):
                in_query = True
                continue
            if in_query:
                if self.QUERY_END.match(stripped):
                    in_query = False
                continue
            if self.COMMENT_START.search(stripped):
                in_comment = True
            if in_comment:
                if self.COMMENT_END.search(stripped):
                    in_comment = False
                continue

            # Skip property lines
            if self.PROPERTY_LINE.match(line):
                continue

            # â”€â”€ Find unprotected text ranges â”€â”€
            text = line
            protected: list[tuple[int, int]] = []

            # Mark positions of existing wikilinks
            for m in self.LINK_PAT.finditer(line):
                protected.append((m.start(), m.end()))
            for m in self.REF_PAT.finditer(line):
                protected.append((m.start(), m.end()))
            for m in self.URL_PAT.finditer(line):
                protected.append((m.start(), m.end()))
            for m in self.MD_LINK_PAT.finditer(line):
                protected.append((m.start(), m.end()))
            for m in self.TAG_PAT.finditer(line):
                protected.append((m.start(), m.end()))
            # Block ref markers ((uuid)) is already covered by REF_PAT

            # Merge protected ranges
            if protected:
                protected.sort()
                merged: list[tuple[int, int]] = [protected[0]]
                for st, en in protected[1:]:
                    if st <= merged[-1][1]:
                        merged[-1] = (merged[-1][0], max(merged[-1][1], en))
                    else:
                        merged.append((st, en))
                protected = merged

            def is_protected(pos: int) -> bool:
                return any(st <= pos < en for st, en in protected)

            # â”€â”€ Find mentions in unprotected segments â”€â”€
            for m in alt_pattern.finditer(line):
                matched = m.group(0)
                start = m.start()
                end = m.end()

                # Skip if in protected zone
                if is_protected(start):
                    continue

                matched_lower = m.group(1).lower() if m.lastindex else matched.lower()

                # Resolve the target title
                target_title = ""
                pg = self.index.by_lower.get(matched_lower)
                if pg:
                    target_title = pg.title
                else:
                    resolved = self.index.resolve_alias(matched_lower)
                    if resolved:
                        target_title = resolved
                    else:
                        continue

                if not target_title:
                    continue

                # Skip self-links
                if target_title.lower() == file_title_lower:
                    continue

                # Check per-target-per-file limit
                tgt_lower = target_title.lower()
                if links_this_file.get(tgt_lower, 0) >= 1:
                    continue

                # Check total per-file limit
                if len(links_this_file) >= self.max_per_file:
                    continue

                # â”€â”€ Determine confidence â”€â”€
                wc = len(target_title.split())
                pg2 = self.index.by_lower.get(target_title.lower())
                is_wiki_page = pg2 and pg2.source_dir == "wiki"
                page_type = pg2.page_type if pg2 else ""

                if wc >= 2 or is_wiki_page or page_type in ("person", "company", "book"):
                    confidence = "HIGH"
                    reason = "multi-word title" if wc >= 2 else "wiki page"
                    if page_type:
                        reason = f"wiki {page_type}"
                elif wc == 1 and matched[0].isupper():
                    confidence = "MEDIUM"
                    reason = "proper noun"
                    if is_wiki_page:
                        confidence = "HIGH"
                        reason = "wiki concept"
                else:
                    confidence = "LOW"
                    reason = "ambiguous"

                links_this_file[tgt_lower] += 1

                # Context
                ctx_start = max(0, start - 40)
                ctx_end = min(len(line), end + 40)
                ctx_before = line[ctx_start:start].strip()
                ctx_after = line[end:ctx_end].strip()

                sug = Suggestion(
                    filepath=Path(),
                    line_index=li,
                    column=start,
                    end_column=end,
                    target_title=target_title,
                    matched_text=matched,
                    confidence=confidence,
                    reason=reason,
                    context_before=ctx_before,
                    context_after=ctx_after,
                    unique_id=f"{Path()}:{li}:{start}",
                )
                suggestions.append(sug)


# â”€â”€ Applier â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class Applier:
    """Apply approved suggestions to the graph."""

    def __init__(self, graph_path: Path):
        self.graph_path = graph_path

    def apply(self, suggestions: list[Suggestion], dry_run: bool = False) -> dict:
        """Apply accepted suggestions. Returns summary dict."""
        by_file: dict[Path, list[Suggestion]] = defaultdict(list)
        for s in suggestions:
            if s.accepted:
                by_file[s.filepath].append(s)
        # Deduplicate per line (only keep highest confidence)
        filtered: dict[Path, list[Suggestion]] = {}
        for fpath, sugs in by_file.items():
            # Sort by column descending to avoid position shift issues
            sugs.sort(key=lambda x: -x.column)
            filtered[fpath] = sugs

        modified_files = 0
        total_links = 0
        backup_manifest = []
        errors: list[str] = []

        for fpath, sugs in filtered.items():
            try:
                content = fpath.read_text("utf-8")
                lines = content.splitlines(keepends=True)
                changes_made = 0

                for sug in sugs:
                    if sug.line_index >= len(lines):
                        continue
                    line = lines[sug.line_index]
                    # Verify the matched text is still there
                    if sug.matched_text not in line[sug.column:sug.end_column]:
                        continue
                    # Insert [[ ]] around the match
                    new_line = (
                        line[:sug.column]
                        + "[["
                        + line[sug.column:sug.end_column]
                        + "]]"
                        + line[sug.end_column:]
                    )
                    lines[sug.line_index] = new_line
                    changes_made += 1
                    total_links += 1

                if changes_made == 0:
                    continue
                if dry_run:
                    modified_files += 1
                    backup_manifest.append({
                        "file": str(fpath.relative_to(self.graph_path)),
                        "changes": changes_made,
                        "targets": [s.target_title for s in sugs],
                    })
                else:
                    # Atomic write
                    fd, tmp_path = tempfile.mkstemp(
                        dir=fpath.parent,
                        prefix=f".{fpath.stem}.",
                        suffix=".tmp"
                    )
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as f:
                            f.writelines(lines)
                        os.replace(tmp_path, fpath)
                    except Exception as e:
                        Path(tmp_path).unlink(missing_ok=True)
                        errors.append(f"{fpath.relative_to(self.graph_path)}: {e}")
                        continue

                    modified_files += 1
                    backup_manifest.append({
                        "file": str(fpath.relative_to(self.graph_path)),
                        "changes": changes_made,
                        "targets": [s.target_title for s in sugs],
                        "backup": None,  # TODO: optional backup tracking
                    })

            except Exception as e:
                errors.append(f"{fpath.relative_to(self.graph_path)}: {e}")

        result = {
            "modified_files": modified_files,
            "total_links": total_links,
            "manifest": backup_manifest,
        }

        if errors:
            raise RuntimeError("Apply failed for:\n- " + "\n- ".join(errors))

        return result


# â”€â”€ TUI (Rich) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class TUI:
    """Terminal UI for the housekeeper."""

    def __init__(self, console: Console, index: PageIndex,
                 scanner: Scanner, applier: Applier, config: dict):
        self.console = console
        self.index = index
        self.scanner = scanner
        self.applier = applier
        self.config = config
        self.suggestions: list[Suggestion] = []

    def run(self):
        """Main interactive loop."""
        self._header()
        while True:
            action = self._menu()
            if action == "q":
                self.console.print("[bold green]Goodbye. Your graph is safe.[/]")
                break
            elif action == "1":
                self._do_scan()
            elif action == "2":
                self._do_review()
            elif action == "3":
                self._do_apply()
            elif action == "4":
                self._do_auto_apply()
            elif action == "5":
                self._show_rejected()
            elif action == "6":
                self._export_report()

    def _header(self):
        self.console.clear()
        self.console.print(Panel.fit(
            "[bold cyan]Logseq Housekeeper by Juan |[/] Lean wiki-link suggestion engine\n"
            "[dim]v1 | dry-run by default | targeted linking[/]",
            border_style="cyan"
        ))

    def _menu(self) -> str:
        self.console.print()
        menu = Table.grid(padding=(0, 2))
        menu.add_column("Option", style="bold cyan", width=6)
        menu.add_column("Action", style="white")
        menu.add_row("[1]", "Scan graph for unlinked mentions")
        menu.add_row("[2]", f"Review suggestions [dim]({len(self.suggestions)} pending)[/]")
        menu.add_row("[3]", "Apply approved suggestions")
        menu.add_row("[4]", "Auto-apply high-confidence suggestions")
        menu.add_row("[5]", "Show rejected / ambiguous")
        menu.add_row("[6]", "Export report")
        menu.add_row("[q]", "Quit")
        self.console.print(menu)
        return Prompt.ask("  Choose", choices=["1", "2", "3", "4", "5", "6", "q"])

    def _do_scan(self):
        self._header()
        self.console.print("[bold]Scanning graph for unlinked mentions...[/]")

        t_start = datetime.now()
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            console=self.console,
        ) as progress:
            task = progress.add_task("Building page index...", total=1)
            self.index.build()
            progress.update(task, completed=1)

            task2 = progress.add_task("Scanning files...", total=1)
            results = self.scanner.scan_all()
            progress.update(task2, completed=1)

        all_suggestions = []
        for fpath, sugs in results:
            for s in sugs:
                s.filepath = fpath
                s.unique_id = f"{fpath}:{s.line_index}:{s.column}"
            all_suggestions.extend(sugs)

        self.suggestions = all_suggestions
        elapsed = (datetime.now() - t_start).total_seconds()

        high = sum(1 for s in self.suggestions if s.confidence == "HIGH")
        med = sum(1 for s in self.suggestions if s.confidence == "MEDIUM")
        low = sum(1 for s in self.suggestions if s.confidence == "LOW")

        self.console.print()
        self.console.print(Panel(
            f"[bold]Scan complete in {elapsed:.1f}s[/]\n"
            f"[green]{len(self.index.by_lower)}[/] pages indexed\n"
            f"[bold]{len(self.suggestions)}[/] unlinked mentions found\n"
            f"  [green]{high} high[/] Â· [yellow]{med} medium[/] Â· [red]{low} low[/] confidence",
            border_style="green"
        ))

    def _do_review(self):
        self._header()
        if not self.suggestions:
            self.console.print("[yellow]No suggestions to review. Run scan first.[/]")
            return

        by_file: dict[str, list[Suggestion]] = defaultdict(list)
        for s in self.suggestions:
            if s.accepted is None:
                rel = s.filepath.relative_to(self.index.graph_path)
                by_file[str(rel)].append(s)

        if not by_file:
            self.console.print("[yellow]All suggestions already reviewed. Run scan to refresh.[/]")
            return

        accepted = 0
        rejected = 0
        skipped = 0

        file_keys = sorted(by_file.keys())
        current_file_idx = 0

        while current_file_idx < len(file_keys):
            fkey = file_keys[current_file_idx]
            sugs = by_file[fkey]
            sugs.sort(key=lambda x: (x.line_index, x.column))
            pending = [s for s in sugs if s.accepted is None]
            if not pending:
                current_file_idx += 1
                continue

            self.console.clear()
            self.console.print(f"[bold cyan]{fkey}[/]")
            self.console.print(f"[dim]{len(pending)} unlinked mentions Â· file {file_keys.index(fkey) + 1}/{len(file_keys)}[/]")
            self.console.print()

            for i, sug in enumerate(pending):
                label = sug.confidence
                style = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(label, "white")
                self.console.print(
                    f"  [bold]{i + 1}[/] [bold {style}]{label}[/] -> "
                    f"[[{sug.target_title}]]"
                )
                ctx = (
                    f"...{sug.context_before}"
                    f"[underline]{sug.matched_text}[/underline]"
                    f"{sug.context_after}..."
                )
                self.console.print(f"      {ctx}")
                self.console.print(f"      [dim]line {sug.line_index + 1} Â· {sug.reason}[/]")
                self.console.print()

            # Batch actions for this file
            self.console.print("[dim i]Actions for this file:[/]")
            self.console.print("  [bold]a[/]  Accept all")
            self.console.print("  [bold]r[/]  Reject all")
            self.console.print("  [bold]n[/]  Next file (skip)")
            self.console.print("  [bold]q[/]  Quit review")
            self.console.print("  [bold]<n>[/] Toggle individual (e.g. [italic]1[/], [italic]2[/])")
            action = Prompt.ask("  Action", default="n")

            if action == "q":
                break
            elif action == "a":
                for s in pending:
                    s.accepted = True
                    accepted += 1
                current_file_idx += 1
            elif action == "r":
                for s in pending:
                    s.accepted = False
                    rejected += 1
                current_file_idx += 1
            elif action == "n":
                skipped += len(pending)
                current_file_idx += 1
            elif action.isdigit():
                idx = int(action) - 1
                if 0 <= idx < len(pending):
                    s = pending[idx]
                    if s.accepted is None:
                        s.accepted = True
                        accepted += 1
                    elif s.accepted:
                        s.accepted = False
                        accepted -= 1
                        rejected += 1
                    else:
                        s.accepted = None
                        rejected -= 1
                # Stay on same file for more review
            else:
                self.console.print("[red]Invalid option[/]")

        total_undecided = sum(1 for s in self.suggestions if s.accepted is None)
        self.console.print()
        self.console.print(Panel(
            f"[bold]Review summary[/]\n"
            f"[green]{accepted} accepted[/] Â· [red]{rejected} rejected[/] Â· "
            f"[dim]{total_undecided} still undecided[/]",
            border_style="blue"
        ))

    def _do_apply(self):
        self._header()
        pending = [s for s in self.suggestions if s.accepted is True]
        if not pending:
            self.console.print("[yellow]No accepted suggestions to apply.[/]")
            return

        by_file = set(str(s.filepath.relative_to(self.index.graph_path)) for s in pending)
        self.console.print(
            f"[bold]{len(pending)}[/] links across [bold]{len(by_file)}[/] files ready to apply."
        )

        confirm = Confirm.ask(
            "[yellow]This will modify your Logseq files. Continue?[/]",
            default=False
        )
        if not confirm:
            self.console.print("[yellow]Cancelled.[/]")
            return

        # Dry-run first
        dry = self.applier.apply(pending, dry_run=True)
        self.console.print(f"[dim]Dry-run: {dry['modified_files']} files, {dry['total_links']} links[/]")

        confirm2 = Confirm.ask(
            "[yellow]Proceed with actual write?[/]",
            default=False
        )
        if not confirm2:
            self.console.print("[yellow]Write cancelled. Suggestions preserved.[/]")
            return

        try:
            result = self.applier.apply(pending, dry_run=False)
        except Exception as e:
            self.console.print(f"[red]Apply failed:[/] {e}")
            return

        self.console.print(Panel(
            f"[green]Applied [bold]{result['total_links']}[/] links "
            f"across [bold]{result['modified_files']}[/] files.[/]\n"
            f"[dim]Review with git diff to verify.[/]",
            border_style="green"
        ))

    def _do_auto_apply(self):
        self._header()
        high_conf = [
            s for s in self.suggestions
            if s.confidence == "HIGH" and s.accepted is None
        ]
        if not high_conf:
            self.console.print("[yellow]No un-reviewed high-confidence suggestions.[/]")
            return

        by_file = set(str(s.filepath.relative_to(self.index.graph_path)) for s in high_conf)
        self.console.print(Panel(
            f"[bold green]{len(high_conf)} high-confidence[/] suggestions across "
            f"[bold]{len(by_file)}[/] files.",
            border_style="green"
        ))

        # Preview
        for s in high_conf[:10]:
            rel = s.filepath.relative_to(self.index.graph_path)
            self.console.print(
                f"  -> [[{s.target_title}]] [dim]in {rel}:{s.line_index + 1}[/]"
            )
        if len(high_conf) > 10:
            self.console.print(f"  [dim]...and {len(high_conf) - 10} more[/]")

        confirm = Confirm.ask(
            f"[yellow]Auto-apply these {len(high_conf)} high-confidence links?[/]",
            default=False
        )
        if not confirm:
            self.console.print("[yellow]Cancelled.[/]")
            return

        for s in high_conf:
            s.accepted = True

        # Apply
        try:
            result = self.applier.apply(high_conf, dry_run=False)
        except Exception as e:
            self.console.print(f"[red]Auto-apply failed:[/] {e}")
            return

        self.console.print(Panel(
            f"[green]Auto-applied [bold]{result['total_links']}[/] high-confidence links "
            f"across [bold]{result['modified_files']}[/] files.[/]\n"
            f"[dim]{len([s for s in self.suggestions if s.accepted is None])} "
            f"suggestions remain for manual review.[/]",
            border_style="green"
        ))

    def _show_rejected(self):
        self._header()
        rejected = [s for s in self.suggestions if s.accepted is False]
        ambiguous = [s for s in self.suggestions if s.confidence == "LOW" and s.accepted is None]

        if not rejected and not ambiguous:
            self.console.print("[yellow]No rejected or ambiguous suggestions.[/]")
            return

        if rejected:
            self.console.print(f"[bold red]{len(rejected)} Rejected suggestions:[/]")
            for s in reversed(rejected[-20:]):
                rel = s.filepath.relative_to(self.index.graph_path)
                self.console.print(f"  Ã— [[{s.target_title}]] [dim]{rel}:{s.line_index + 1}[/]")
            if len(rejected) > 20:
                self.console.print(f"  [dim]...and {len(rejected) - 20} more[/]")

        if ambiguous:
            self.console.print(f"\n[bold yellow]{len(ambiguous)} Ambiguous/low-confidence suggestions:[/]")
            for s in ambiguous[:15]:
                rel = s.filepath.relative_to(self.index.graph_path)
                self.console.print(
                    f"  ? [[{s.target_title}]] [dim]{rel}:{s.line_index + 1} Â· {s.reason}[/]"
                )
            if len(ambiguous) > 15:
                self.console.print(f"  [dim]...and {len(ambiguous) - 15} more[/]")

    def _export_report(self):
        self._header()
        out_dir = self.graph_path / "housekeeping"
        out_dir.mkdir(parents=True, exist_ok=True)
        report_path = out_dir / "link-suggestions.json"
        data = []
        for s in self.suggestions:
            rel = str(s.filepath.relative_to(self.index.graph_path))
            data.append({
                "file": rel,
                "line": s.line_index + 1,
                "target": s.target_title,
                "matched": s.matched_text,
                "confidence": s.confidence,
                "reason": s.reason,
                "accepted": s.accepted,
            })
        report_path.write_text(
            json.dumps(data, indent=2),
            encoding="utf-8"
        )
        self.console.print(f"[green]Report written to {report_path}[/]")


# â”€â”€ Plain Fallback â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class PlainTUI:
    """Fallback TUI without rich."""

    def __init__(self, index, scanner, applier, config):
        self.index = index
        self.scanner = scanner
        self.applier = applier
        self.config = config
        self.suggestions = []

    def run(self):
        print("=== Logseq Housekeeper ===")
        print()
        while True:
            print("1. Scan graph")
            print(f"2. Review suggestions ({len(self.suggestions)} pending)")
            print("3. Apply approved")
            print("4. Auto-apply high confidence")
            print("5. Show rejected/ambiguous")
            print("6. Export report")
            print("q. Quit")
            action = input("Choose: ").strip().lower()
            if action == "q":
                break
            elif action == "1":
                self._do_scan()
            elif action == "2":
                self._do_review()
            elif action == "3":
                self._do_apply()
            elif action == "4":
                self._do_auto_apply()
            elif action == "5":
                self._show_rejected()
            elif action == "6":
                self._export_report()

    def _do_scan(self):
        print("Building index...")
        self.index.build()
        print(f"Indexed {len(self.index.by_lower)} pages")
        print("Scanning files...")
        results = self.scanner.scan_all()
        all_s = []
        for fpath, sugs in results:
            for s in sugs:
                s.filepath = fpath
            all_s.extend(sugs)
        self.suggestions = all_s
        high = sum(1 for s in self.suggestions if s.confidence == "HIGH")
        med = sum(1 for s in self.suggestions if s.confidence == "MEDIUM")
        low = sum(1 for s in self.suggestions if s.confidence == "LOW")
        print(f"Found {len(self.suggestions)} mentions: {high} high, {med} med, {low} low")

    def _do_review(self):
        if not self.suggestions:
            print("No suggestions. Run scan first.")
            return
        pending = [s for s in self.suggestions if s.accepted is None]
        if not pending:
            print("All reviewed.")
            return
        for s in pending:
            rel = s.filepath.relative_to(self.index.graph_path)
            print(f"\n{rel}:{s.line_index + 1}")
            print(f"  [{s.confidence}] [[{s.target_title}]] -> \"{s.matched_text}\"")
            act = input("  Accept (y/N/s/q)? ").strip().lower()
            if act == "q":
                break
            elif act == "y":
                s.accepted = True
            elif act == "s":
                continue
            else:
                s.accepted = False

    def _do_apply(self):
        pending = [s for s in self.suggestions if s.accepted is True]
        if not pending:
            print("No accepted suggestions.")
            return
        print(f"{len(pending)} links to apply.")
        ok = input("Proceed? (y/N): ").strip().lower()
        if ok != "y":
            return
        try:
            result = self.applier.apply(pending, dry_run=False)
        except Exception as e:
            print(f"Apply failed: {e}")
            return
        print(f"Applied {result['total_links']} links in {result['modified_files']} files.")

    def _do_auto_apply(self):
        high = [s for s in self.suggestions if s.confidence == "HIGH" and s.accepted is None]
        if not high:
            print(f"No high-confidence suggestions available.")
            return
        print(f"{len(high)} high-confidence suggestions:")
        for s in high[:5]:
            rel = s.filepath.relative_to(self.index.graph_path)
            print(f"  -> [[{s.target_title}]] in {rel}:{s.line_index + 1}")
        ok = input("Apply? (y/N): ").strip().lower()
        if ok != "y":
            return
        for s in high:
            s.accepted = True
        try:
            result = self.applier.apply(high, dry_run=False)
        except Exception as e:
            print(f"Apply failed: {e}")
            return
        print(f"Applied {result['total_links']} links.")

    def _show_rejected(self):
        rejected = [s for s in self.suggestions if s.accepted is False]
        ambiguous = [s for s in self.suggestions if s.confidence == "LOW" and s.accepted is None]
        if rejected:
            print(f"\n== Rejected ({len(rejected)}) ==")
            for s in rejected[:10]:
                rel = s.filepath.relative_to(self.index.graph_path)
                print(f"  [[{s.target_title}]] in {rel}:{s.line_index + 1}")
        if ambiguous:
            print(f"\n== Ambiguous ({len(ambiguous)}) ==")
            for s in ambiguous[:10]:
                rel = s.filepath.relative_to(self.index.graph_path)
                print(f"  [[{s.target_title}]] in {rel}:{s.line_index + 1}")

    def _export_report(self):
        out_dir = self.index.graph_path / "housekeeping"
        out_dir.mkdir(parents=True, exist_ok=True)
        report_path = out_dir / "link-suggestions.json"
        data = []
        for s in self.suggestions:
            rel = str(s.filepath.relative_to(self.index.graph_path))
            data.append({
                "file": rel,
                "line": s.line_index + 1,
                "target": s.target_title,
                "matched": s.matched_text,
                "confidence": s.confidence,
                "reason": s.reason,
                "accepted": s.accepted,
            })
        report_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"Report written to {report_path}")


# â”€â”€ CLI Entry Point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def load_config(config_path: Path) -> dict:
    defaults = {
        "graph_path": "",
        "max_links_per_file": 20,
        "include_journals": True,
        "include_wiki": True,
    }
    if config_path.exists():
        try:
            with open(config_path, "r") as f:
                user = json.load(f)
            defaults.update(user)
        except Exception:
            pass
    return defaults


def main():
    parser = argparse.ArgumentParser(
        description="Logseq Housekeeper â€” find and suggest missing wikilinks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python logseq_housekeeper.py --graph-path /path/to/logseq/graph
              python logseq_housekeeper.py --graph-path /path --config custom.json
              python logseq_housekeeper.py --graph-path /path --plain
        """),
    )
    parser.add_argument(
        "--graph-path",
        type=str,
        default="",
        help="Path to Logseq graph root (folder containing pages/, journals/)",
    )
    parser.add_argument("--config", type=str, default="housekeeper.config.json",
                        help="Config file path (default: housekeeper.config.json)")
    parser.add_argument("--plain", action="store_true",
                        help="Force plain terminal UI (no rich)")
    args = parser.parse_args()

    config_file = Path(args.config)
    config = load_config(config_file)

    graph_path_str = args.graph_path or config.get("graph_path", "")
    if not graph_path_str:
        print("ERROR: --graph-path is required (or set in config)")
        sys.exit(1)

    graph_path = Path(graph_path_str).resolve()
    if not graph_path.is_dir():
        print(f"ERROR: Graph path does not exist: {graph_path}")
        sys.exit(1)

    use_rich = HAS_RICH and not args.plain

    print(f"Building index for: {graph_path}")
    index = PageIndex(graph_path)
    index.build()
    print(f"Indexed {len(index.by_lower)} pages")

    scanner = Scanner(index, graph_path, config)
    applier = Applier(graph_path)

    if use_rich:
        console = Console()
        ui = TUI(console, index, scanner, applier, config)
    else:
        ui = PlainTUI(index, scanner, applier, config)

    ui.run()


if __name__ == "__main__":
    main()
