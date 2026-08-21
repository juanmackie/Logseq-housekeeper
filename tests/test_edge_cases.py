#!/usr/bin/env python3
"""Edge-case robustness tests for logseq_housekeeper.

Builds a tiny adversarial graph and asserts:
  - scanning never crashes (errors list empty)
  - zero-corruption holds on dry-run AND real apply
  - zone handling is correct for tricky constructs

Run: python tests/test_edge_cases.py  (exit 0 = all pass)
"""
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import harness  # noqa: E402
import logseq_housekeeper as lh  # noqa: E402


def build_edge_graph(root: Path):
    pages = root / "pages"
    journals = root / "journals"
    pages.mkdir(parents=True)
    journals.mkdir(parents=True)

    # BOM file with an unlinked mention of another page
    (pages / "Alpha Page.md").write_bytes(
        b"\xef\xbb\xbftype:: person\nMention of Zeta Grid here.\n")
    # CRLF file
    (pages / "Beta Source.md").write_bytes(
        b"notes\r\nsee Alpha Page now\r\n")
    # no trailing newline
    (pages / "Gamma Node.md").write_text("ref to Alpha Page", encoding="utf-8")
    # empty file
    (pages / "Empty Page.md").write_text("", encoding="utf-8")
    # percent-encoded filename (Logseq style) + unicode title
    (pages / "Zeta%20Grid.md").write_text("unicode ünïcøde page\n",
                                          encoding="utf-8")
    (pages / "Ünicode Tïtle.md").write_text("content\n",
                                            encoding="utf-8")
    # alias with pipe form + ambiguous alias shared with another page
    (pages / "Delta One.md").write_text(
        "alias:: [[The Delta|delta display]]\ntype:: book\nbody\n",
        encoding="utf-8")
    (pages / "Delta Two.md").write_text(
        "alias:: The Delta\nbody\n",
        encoding="utf-8")
    # title:: property only
    (pages / "Epsilon Base.md").write_text(
        "title:: [[Epsilon Prime]]\nbody\n",
        encoding="utf-8")

    # Each construct gets its own journal so expectations are unambiguous
    # under the scanner's flat fence/org-block state machine.
    def jr(name, lines):
        (journals / name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    jr("01-closed-fence.md", [
        "before Alpha Page",
        "```",
        "fenced Alpha Page must be ignored",
        "```",
        "after fence Beta Source visible",
    ])
    jr("02-unclosed-fence.md", [
        "visible Gamma Node here",
        "```",
        "fenced Alpha Page never suggested",
        "trailing Beta Source also fenced",
    ])
    jr("03-org-block.md", [
        "#+BEGIN_QUOTE",
        "org Alpha Page ignored",
        "#+END_QUOTE",
        "after org Gamma Node visible",
    ])
    jr("04-unclosed-org.md", [
        "#+BEGIN_SRC text",
        "Alpha Page inside unclosed org",
        "Beta Source still inside",
    ])
    jr("05-comment.md", [
        "prefix <!-- Alpha Page hidden --> suffix Beta Source visible",
        "<!-- unclosed hides Gamma Node",
        "and Alpha Page too",
    ])
    jr("06-indented-fence.md", [
        "text Alpha Page",
        "    ```",
        "    fenced Gamma Node ignored",
        "    ```",
        "tail Zeta Grid visible",
    ])


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="lh_edge_"))
    try:
        graph = tmp / "graph"
        build_edge_graph(graph)

        index = lh.PageIndex(graph)
        index.build()
        scanner = lh.Scanner(index, graph,
                             {"max_links_per_file": 20})
        results = scanner.scan_all()

        if scanner.errors:
            print("FAIL: scanner errors:", scanner.errors)
            return 1

        sugs = [(str(f.relative_to(graph)), s.target_title, s.line_index)
                for f, ss in results for s in ss]
        print(f"scan ok: {len(sugs)} suggestions")
        for rel, tgt, li in sorted(sugs):
            print(f"  {rel}:{li} -> [[{tgt}]]")

        targets = {(rel, tgt) for rel, tgt, _ in sugs}
        expected = {
            ("pages/Beta Source.md", "Alpha Page"),
            ("pages/Gamma Node.md", "Alpha Page"),
            ("pages/Alpha Page.md", "Zeta Grid"),
            ("journals/01-closed-fence.md", "Alpha Page"),      # line 0
            ("journals/01-closed-fence.md", "Beta Source"),     # after fence
            ("journals/02-unclosed-fence.md", "Gamma Node"),    # line 0 only
            ("journals/03-org-block.md", "Gamma Node"),         # after END_
            ("journals/05-comment.md", "Beta Source"),          # outside <!-- -->
            ("journals/06-indented-fence.md", "Alpha Page"),
            ("journals/06-indented-fence.md", "Zeta Grid"),     # tail
        }
        missing = expected - targets
        if missing:
            print("FAIL: missing expected suggestions:", missing)
            return 1
        extra = targets - expected
        if extra:
            print("FAIL: unexpected suggestions:", extra)
            return 1
        # Nothing may point at ambiguous alias 'The Delta' or fenced content
        bad = [t for t in targets if t[1] in ("The Delta", "delta display",
                                              "Epsilon Prime")]
        if bad:
            print("FAIL: unexpected alias/title suggestions:", bad)
            return 1

        # Zero-corruption on the edge graph (dry-run + apply)
        class FX:  # minimal stand-in for a fixture dir
            pass
        fx = tmp
        (fx / "graph").exists()  # graph already at tmp/graph
        ok, detail = True, ""
        # reuse harness.corruption_check by pointing it at our tmp layout
        orig_fixtures = None
        ok, detail = harness.corruption_check(tmp)
        if not ok:
            print("FAIL: corruption:", detail)
            return 1
        print("corruption:", detail)

        # Idempotence: really apply, then re-scan — inserted wikilinks protect
        # their text, so no applied target may be suggested again.
        all_sugs = [s for _, ss in results for s in ss]
        for s in all_sugs:
            s.accepted = True
        lh.Applier(graph).apply(all_sugs, dry_run=False)
        index2 = lh.PageIndex(graph)
        index2.build()
        scanner2 = lh.Scanner(index2, graph, {"max_links_per_file": 20})
        rescan = [(str(f.relative_to(graph)), s.target_title)
                  for f, ss in scanner2.scan_all() for s in ss]
        applied = {(rel, tgt) for rel, tgt, _ in sugs}
        dupes = [x for x in rescan if x in applied]
        if dupes:
            print("FAIL: re-scan re-suggested applied links:", dupes)
            return 1
        print(f"idempotence: re-scan found {len(rescan)} suggestions "
              f"(0 duplicates of {len(applied)} applied) — ok")
        print("ALL EDGE CASES PASS")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
