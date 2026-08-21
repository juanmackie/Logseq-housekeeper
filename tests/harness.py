#!/usr/bin/env python3
"""Benchmark harness for logseq_housekeeper.

For each fixture graph (small/medium/large):
  - imports PageIndex/Scanner/Applier directly (CLI is interactive-only)
  - times index.build() + scanner.scan_all() (median of N runs)
  - computes precision/recall vs ground_truth.json positives
  - zero-corruption check on a temp copy:
      * hash every .md before
      * dry-run apply -> hashes must be byte-identical
      * real apply -> changed files must differ ONLY by inserted valid
        wikilinks at the recorded columns; unchanged files byte-identical

Usage: python tests/harness.py [--runs N] [--sizes small,medium,large] [--json]
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import logseq_housekeeper as lh  # noqa: E402

WIKILINK_RE = re.compile(r'^\[\[[^\[\]|]+(\|[^\[\]]+)?\]\]$')
CONFIG = {"max_links_per_file": 20, "include_journals": True, "include_wiki": True}


def md_hashes(root: Path) -> dict[str, bytes]:
    out = {}
    for p in sorted(root.rglob("*.md")):
        out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).digest()
    return out


def run_scan(graph: Path):
    index = lh.PageIndex(graph)
    t0 = time.perf_counter()
    index.build()
    scanner = lh.Scanner(index, graph, CONFIG)
    results = scanner.scan_all()
    dt = (time.perf_counter() - t0) * 1000.0
    preds = set()
    n_sug = 0
    for fpath, sugs in results:
        rel = str(fpath.relative_to(graph))
        for s in sugs:
            preds.add((rel, s.target_title))
            n_sug += 1
    return dt, preds, n_sug, len(index.by_lower)


def pr(preds, positives):
    gt = {tuple(x) for x in positives}
    tp = len(preds & gt)
    fp = len(preds - gt)
    fn = len(gt - preds)
    prec = tp / (tp + fp) if tp + fp else 1.0
    rec = tp / len(gt) if gt else 1.0
    return prec, rec, tp, fp, fn


def expected_apply(original_bytes: bytes, sugs) -> bytes:
    """Independently recompute what Applier should produce for one file."""
    has_bom = original_bytes.startswith(b"\xef\xbb\xbf")
    enc = "utf-8-sig" if has_bom else "utf-8"
    content = original_bytes.decode(enc)
    lines = content.splitlines(keepends=True)
    per_line = {}
    for s in sugs:
        per_line.setdefault(s.line_index, []).append(s)
    for li, ls in per_line.items():
        line = lines[li]
        for s in sorted(ls, key=lambda x: -x.column):
            if s.matched_text != line[s.column:s.end_column]:
                raise AssertionError(
                    f"stale match {s.matched_text!r} at col {s.column}")
            inserted_seg = line[s.column:s.end_column]
            if inserted_seg.lower() == s.target_title.lower():
                inserted = "[[" + inserted_seg + "]]"
            else:
                inserted = "[[" + s.target_title + "|" + inserted_seg + "]]"
            if not WIKILINK_RE.match(inserted):
                raise AssertionError(f"invalid wikilink {inserted!r}")
            line = line[:s.column] + inserted + line[s.end_column:]
        lines[li] = line
    out = "".join(lines)
    # BOM handling: utf-8-sig decode strips BOM; re-add for comparison.
    if has_bom and not out.startswith("\ufeff"):
        out = "\ufeff" + out
    return out.encode("utf-8-sig" if has_bom else "utf-8",
                      errors="strict") if has_bom else out.encode("utf-8")


def corruption_check(fx_dir: Path) -> tuple[bool, str]:
    src_graph = fx_dir / "graph"
    tmp = Path(tempfile.mkdtemp(prefix="lh_corruption_"))
    try:
        graph = tmp / "graph"
        shutil.copytree(src_graph, graph)

        index = lh.PageIndex(graph)
        index.build()
        scanner = lh.Scanner(index, graph, CONFIG)
        results = scanner.scan_all()
        sugs = []
        for fpath, ss in results:
            for s in ss:
                s.accepted = True
                s.filepath = fpath
            sugs.extend(ss)

        before = md_hashes(graph)

        # 1) dry-run must not touch a single byte
        applier = lh.Applier(graph)
        applier.apply(sugs, dry_run=True)
        if md_hashes(graph) != before:
            return False, "dry-run modified files"

        # 2) real apply: only expected insertions, everything else identical
        by_file = {}
        for s in sugs:
            by_file.setdefault(s.filepath, []).append(s)
        applier.apply(sugs, dry_run=False)
        after = md_hashes(graph)

        if set(before) != set(after):
            return False, "file set changed (extra/missing .md)"
        for rel, hb in before.items():
            ha = after[rel]
            if hb == ha:
                continue
            p = graph / rel
            orig = (src_graph / rel).read_bytes()
            new = p.read_bytes()
            file_sugs = by_file.get(p, [])
            if not file_sugs:
                return False, f"{rel} modified without suggestions"
            expected = expected_apply(orig, file_sugs)
            if new != expected:
                return False, f"{rel} differs from expected insertion-only edit"
        return True, f"ok ({len(sugs)} links applied across {len(by_file)} files)"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--sizes", default="small,medium,large")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    fixtures = REPO / "tests" / "fixtures"
    report = {"sizes": {}, "corruption_ok": True, "corruption_detail": ""}

    for size in args.sizes.split(","):
        fx = fixtures / size
        gt = json.loads((fx / "ground_truth.json").read_text())
        graph = fx / "graph"
        timings, preds = [], None
        for _ in range(args.runs):
            dt, preds, n_sug, n_pages = run_scan(graph)
            timings.append(dt)
        prec, rec, tp, fp, fn = pr(preds, gt["positives"])
        report["sizes"][size] = {
            "scan_ms": round(min(timings), 1),  # min = least-load-distorted

            "pages_indexed": n_pages,
            "suggestions": n_sug,
            "precision": round(prec, 4), "recall": round(rec, 4),
            "tp": tp, "fp": fp, "fn": fn,
        }
        if not args.json:
            print(f"[{size:6s}] scan={report['sizes'][size]['scan_ms']:>8.1f}ms "
                  f"pages={n_pages:>5} sug={n_sug:>5} "
                  f"P={prec:.4f} R={rec:.4f} (tp={tp} fp={fp} fn={fn})")

    ok, detail = corruption_check(fixtures / "medium")
    report["corruption_ok"] = ok
    report["corruption_detail"] = detail
    if not args.json:
        print(f"[corrupt] {'PASS' if ok else 'FAIL'}: {detail}")
        print(f"LARGE_SCAN_MS={report['sizes']['large']['scan_ms']} "
              f"P={report['sizes']['large']['precision']} "
              f"R={report['sizes']['large']['recall']} "
              f"CORRUPTION={'ok' if ok else 'fail'}")
    else:
        print(json.dumps(report, indent=1))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
