#!/usr/bin/env python3
"""Generate synthetic Logseq graphs with known ground-truth unlinked mentions.

Creates fixtures/<size>/graph/ (pages/, journals/) and fixtures/<size>/ground_truth.json
where positives is a list of [relpath, target_title] pairs the scanner SHOULD suggest.

Mention categories planted:
  exact        - exact title match            (positive)
  case         - case variant                  (positive)
  plural       - plural/suffix variant         (positive)
  alias        - alias:: mention               (positive)
Traps (must NOT be suggested):
  linked       - already inside [[...]]
  fence        - inside ``` code fence
  orgblock     - inside #+BEGIN_SRC block
  comment      - inside <!-- HTML comment -->
  url          - inside a URL
  mdlink       - markdown link text/URL
  blockref     - inside ((ref))
  prop         - on a property:: line
  inlinecode   - inside `backticks` (not yet protected by scanner)
  contaminated - mention of a common-word page (excluded from candidates)
"""
import json
import random
import shutil
import sys
from pathlib import Path

SEED = 20240821

ADJ = ["Quantum", "Neural", "Silent", "Crimson", "Ancient", "Cosmic", "Digital",
       "Frozen", "Golden", "Hidden", "Ivory", "Jade", "Lunar", "Mystic",
       "Northern", "Opal", "Purple", "Quiet", "Rustic", "Silver", "Thunder",
       "Umbral", "Velvet", "Woven", "Zephyr", "Amber", "Boreal", "Cedar",
       "Drift", "Ember"]
NOUN = ["Computing", "Cathedral", "Harbor", "Lantern", "Meadow", "Orchard",
        "Prism", "Quarry", "River", "Summit", "Tundra", "Valley", "Willow",
        "Archive", "Beacon", "Cipher", "Domain", "Engine", "Forest", "Glacier",
        "Atlas", "Mirage", "Foundry", "Garden", "Labyrinth"]
PERSON = ["Einstein", "Newton", "Curie", "Tesla", "Darwin", "Kepler",
          "Galileo", "Hopper", "Turing", "Lovelace", "Aristotle", "Plato",
          "Socrates", "Archimedes", "Faraday", "Maxwell", "Bohr",
          "Heisenberg", "Fermi", "Feynman", "Noether", "Cantor", "Euler",
          "Gauss", "Ramanujan"]
ACRO = ["AI", "LLM", "RAG", "GPT", "KPI", "ROI", "CRM", "ERP", "IPO", "OCR"]
CONTAMINATED = ["market", "time", "system", "value", "growth", "risk"]

# Filler vocabulary: disjoint from every title word above.
FILLER = ["note", "draft", "sketch", "fragment", "outline", "summary", "log",
          "entry", "memo", "thread", "spark", "review", "check", "point",
          "list", "batch", "round", "pass", "step", "phase", "slot", "mode"]

ALIAS_NAMES = ["the Old Regime", "Grand Survey", "First Light", "Deep Field",
               "Long Count", "Open Ledger"]


ROMANS = ["II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
          "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX"]


def build_titles(n_pages):
    """Return list of dicts describing pages: kind, title, alias?, type?

    Titles always have enough unique names for n_pages (pools are expanded
    with suffixes rather than silently exhausting).
    """
    rng = random.Random(SEED)
    used = set()

    def take(t):
        if t.lower() not in used:
            used.add(t.lower())
            return True
        return False

    titles = []
    counts = {"multi": int(n_pages * 0.45), "person": int(n_pages * 0.30),
              "acro": int(n_pages * 0.15), "contam": int(n_pages * 0.10)}
    counts["multi"] += n_pages - sum(counts.values())

    # multi-word: all adjective+noun pairs, then adjective+noun+noun triples
    pairs = [(a, b) for a in ADJ for b in NOUN]
    triples = [(a, b, c) for a in ADJ for b in NOUN for c in NOUN]
    mi = 0
    for _ in range(counts["multi"]):
        if mi < len(pairs):
            t = f"{pairs[mi][0]} {pairs[mi][1]}"
        else:
            tr = triples[mi - len(pairs)]
            t = f"{tr[0]} {tr[1]} {tr[2]}"
        mi += 1
        assert take(t), f"dup multi {t}"
        titles.append({"kind": "multi", "title": t})

    # persons: base names, then Roman-numeral suffixed versions
    pi = 0
    for _ in range(counts["person"]):
        base = PERSON[pi % len(PERSON)]
        gen = pi // len(PERSON)
        t = base if gen == 0 else (f"{base} {ROMANS[(gen - 1) % len(ROMANS)]}"
                                    if gen <= len(ROMANS) else f"{base} {gen + 1}")
        pi += 1
        assert take(t), f"dup person {t}"
        titles.append({"kind": "person", "title": t})

    # acronyms: bare, then "XX n"
    ki = 0
    for _ in range(counts["acro"]):
        base = ACRO[ki % len(ACRO)]
        gen = ki // len(ACRO)
        t = base if gen == 0 else f"{base} {gen + 1}"
        ki += 1
        assert take(t), f"dup acro {t}"
        titles.append({"kind": "acro", "title": t})

    # contaminated: single common words, then 2- and 3-word common phrases
    ci = 0
    combos2 = [(a, b) for a in CONTAMINATED for b in CONTAMINATED if a != b]
    combos3 = [(a, b, c) for a in CONTAMINATED for b in CONTAMINATED
               for c in CONTAMINATED if len({a, b, c}) == 3]
    for _ in range(counts["contam"]):
        if ci < len(CONTAMINATED):
            t = CONTAMINATED[ci]
        elif ci < len(CONTAMINATED) + len(combos2):
            c = combos2[ci - len(CONTAMINATED)]
            t = f"{c[0]} {c[1]}"
        else:
            c = combos3[(ci - len(CONTAMINATED) - len(combos2)) % len(combos3)]
            base = f"{c[0]} {c[1]} {c[2]}"
            t = base if ci < len(CONTAMINATED) + len(combos2) + len(combos3) \
                else f"{base} {ci}"
        ci += 1
        assert take(t), f"dup contam {t}"
        titles.append({"kind": "contam", "title": t})

    rng.shuffle(titles)
    return titles


def filler_line(rng):
    n = rng.randint(5, 9)
    return " ".join(rng.choice(FILLER) for _ in range(n)).capitalize() + "."


class Planter:
    """Plants one mention per (file, target) pair; records positives."""

    def __init__(self, relpath, rng, positives):
        self.relpath = relpath
        self.rng = rng
        self.positives = positives
        self.used_pairs = set()
        self.lines = []

    def _pair_ok(self, target):
        return (self.relpath, target) not in self.used_pairs

    def _mark(self, target, positive):
        self.used_pairs.add((self.relpath, target))
        if positive:
            self.positives.append([self.relpath, target])

    def add(self, category, page):
        """page: dict with title/kind/alias. Returns True if planted."""
        rng = self.rng
        title = page["title"]
        if category == "exact":
            if not self._pair_ok(title):
                return False
            text = title
            self._mark(title, page["kind"] != "contam")
        elif category == "case":
            low = title.lower()
            if low == title or not self._pair_ok(title):
                return False
            text = low
            self._mark(title, page["kind"] != "contam")
        elif category == "plural":
            if (page["kind"] not in ("person",) or title.endswith("s")
                    or not self._pair_ok(title)):
                return False
            text = title + "s"
            self._mark(title, True)
        elif category == "alias":
            if not page.get("alias") or not self._pair_ok(page["alias"]):
                return False
            text = page["alias"]
            # Scanner resolves the alias to the page title; GT records the title.
            self._mark(title, True)
        elif category == "linked":
            if not self._pair_ok("trap:" + title):
                return False
            text = f"[[{title}]]"
            self._mark("trap:" + title, False)
        elif category == "inlinecode":
            if not self._pair_ok("trap:`" + title):
                return False
            text = f"`see {title} here`"
            self._mark("trap:`" + title, False)
        elif category == "url":
            if not self._pair_ok("trap:url:" + title):
                return False
            slug = title.replace(" ", "-")
            text = f"https://example.com/docs/{slug}/index.html"
            self._mark("trap:url:" + title, False)
        elif category == "mdlink":
            if not self._pair_ok("trap:md:" + title):
                return False
            text = f"[{title} manual](https://example.com/{title.replace(' ', '_')})"
            self._mark("trap:md:" + title, False)
        elif category == "blockref":
            if not self._pair_ok("trap:ref:" + title):
                return False
            text = f"((6a2b-{title.replace(' ', '-')}-c3d4))"
            self._mark("trap:ref:" + title, False)
        elif category == "tag":
            if not self._pair_ok("trap:tag:" + title):
                return False
            text = f"#{title.replace(' ', '')}"
            self._mark("trap:tag:" + title, False)
        elif category == "prop":
            if not self._pair_ok("trap:prop:" + title):
                return False
            self.lines.append(f"status:: reviewing {title} notes")
            self._mark("trap:prop:" + title, False)
            return True
        elif category == "contaminated":
            cont = rng.choice(CONTAMINATED)
            if not self._pair_ok("contam:" + cont):
                return False
            text = f"the {cont} of practice"
            self._mark("contam:" + cont, False)
        else:
            raise ValueError(category)
        lead = rng.choice(FILLER)
        tail = rng.choice(FILLER)
        style = rng.random()
        if style < 0.3:
            line = f"{text} — {lead} {tail}."
        elif style < 0.6:
            line = f"{lead.capitalize()} {tail}, per {text}."
        else:
            line = f"{lead} {text} {tail};"
        self.lines.append(line)
        return True

    def add_fence_block(self, page):
        title = page["title"]
        if not self._pair_ok("trap:fence:" + title):
            return
        self.lines.append("```")
        self.lines.append(f"const x = '{title}';")
        self.lines.append("render(x);")
        self.lines.append("```")
        self._mark("trap:fence:" + title, False)

    def add_org_block(self, page):
        title = page["title"]
        if not self._pair_ok("trap:org:" + title):
            return
        self.lines.append("#+BEGIN_SRC text")
        self.lines.append(f"discussion of {title} continues")
        self.lines.append("#+END_SRC")
        self._mark("trap:org:" + title, False)

    def add_comment(self, page):
        title = page["title"]
        if not self._pair_ok("trap:cmt:" + title):
            return
        self.lines.append(f"<!-- drafted by {title} editor -->")
        self._mark("trap:cmt:" + title, False)


def write_page(path, page, rng):
    lines = []
    if page["kind"] == "person":
        lines.append("type:: person")
    if page.get("alias"):
        lines.append(f"alias:: {page['alias']}")
    lines.append(f"Notes about {page['title']}." if page["kind"] != "contam"
                 else "Miscellaneous notes.")
    for _ in range(rng.randint(2, 5)):
        lines.append(filler_line(rng))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate(size_name, n_pages, n_journals, mentions_per_journal):
    root = Path(__file__).resolve().parent
    fx = root / "fixtures" / size_name
    graph = fx / "graph"
    if graph.exists():
        shutil.rmtree(graph)
    (graph / "pages").mkdir(parents=True)
    (graph / "journals").mkdir(parents=True)

    rng = random.Random(SEED + len(size_name))
    titles = build_titles(n_pages)

    linkable = [t for t in titles if t["kind"] != "contam"]
    # Give ~20% of multi-word/person pages an alias.
    alias_pool = list(ALIAS_NAMES)
    ai = 0
    for t in titles:
        t["alias"] = None
    for t in titles:
        if t["kind"] in ("multi", "person") and rng.random() < 0.2 and ai < len(alias_pool) * 40:
            alias = alias_pool[ai % len(alias_pool)] + ("" if ai < len(alias_pool) else f" {ai // len(alias_pool)}")
            ai += 1
            t["alias"] = alias

    for t in titles:
        # Real Logseq graphs keep spaces in page filenames.
        write_page(graph / "pages" / f"{t['title']}.md", t, rng)

    positives = []
    cats_pos = ["exact", "case", "plural", "alias"]
    cats_trap = ["linked", "inlinecode", "url", "mdlink", "blockref", "tag",
                 "prop", "contaminated"]

    for ji in range(n_journals):
        rel = f"journals/journal-{ji:04d}.md"
        planter = Planter(rel, rng, positives)
        local_pages = rng.sample(linkable, min(len(linkable), mentions_per_journal * 3))
        pi = 0
        planted = 0
        guard = 0
        while planted < mentions_per_journal and guard < 500 and pi < len(local_pages):
            guard += 1
            page = local_pages[pi % len(local_pages)]
            pi += 1
            r = rng.random()
            if r < 0.55:
                cat = rng.choice(cats_pos)
                ok = planter.add(cat, page)
            elif r < 0.92:
                ok = planter.add(rng.choice(cats_trap), page)
            elif r < 0.96:
                before = len(planter.lines)
                planter.add_fence_block(page)
                ok = len(planter.lines) > before
            elif r < 0.98:
                before = len(planter.lines)
                planter.add_org_block(page)
                ok = len(planter.lines) > before
            else:
                before = len(planter.lines)
                planter.add_comment(page)
                ok = len(planter.lines) > before
            if ok:
                planted += 1
        for _ in range(rng.randint(3, 8)):
            fl = filler_line(rng)
            planter.lines.append(rng.choice(["- ", "", ""] ) + fl)
        body = "\n".join(planter.lines) + "\n"
        (graph / "journals" / f"journal-{ji:04d}.md").write_text(body,
                                                                 encoding="utf-8")

    gt = {"size": size_name, "n_pages": n_pages,
          "positives": sorted(positives)}
    (fx / "ground_truth.json").write_text(json.dumps(gt, indent=1), encoding="utf-8")
    print(f"[gen] {size_name}: {n_pages} pages, {n_journals} journals, "
          f"{len(positives)} positive mentions")


if __name__ == "__main__":
    sizes = sys.argv[1:] or ["small", "medium", "large"]
    cfg = {"small": (50, 10, 10), "medium": (500, 100, 12),
           "large": (2000, 400, 14)}
    for s in sizes:
        generate(s, *cfg[s])
