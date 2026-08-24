#!/usr/bin/env python3
"""Every parameter Apple documents for a Logic Pro plug-in, instrument, control surface
or user-guide chapter — one line each, straight from the harvested corpus.

    python3 params.py Compressor                 # name | control | description, grouped by topic
    python3 params.py Compressor --json          # the same records as JSON
    python3 params.py ES2 --grep oscillator      # filter by name / description
    python3 params.py --list                     # every plug-in with its parameter count
    python3 params.py --search threshold         # one parameter name across all plug-ins

Plug-in names match case-insensitively; an exact name wins over a substring match
("Compressor" is Compressor, not Surround Compressor).
"""

import argparse
import json
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "parameters.jsonl"
WIDTH = 120
SEARCH_LIMIT = 25


def load():
    if not DATA.exists():
        sys.exit(f"No parameters at {DATA}. Run: python3 {Path(__file__).with_name('harvest.py')}")
    with DATA.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def plugins(records):
    counts = Counter()
    sources = {}
    for r in records:
        counts[r["plugin"]] += 1
        sources.setdefault(r["plugin"], r["source"])
    return counts, sources


def resolve(name, counts):
    """Exact (case-insensitive) plug-in name, else the unique substring match."""
    wanted = name.strip().lower()
    exact = [p for p in counts if p.lower() == wanted]
    if exact:
        return exact, []
    partial = sorted(p for p in counts if wanted in p.lower())
    if len(partial) == 1:
        return partial, []
    return [], partial


def extras(record, full):
    bits = []
    if record.get("choices"):
        bits.append("choices: " + " | ".join(record["choices"]))
    rng = record.get("range")
    if rng:
        bits.append(f"range: {rng['min']}…{rng['max']} {rng['unit']}")
    return "  {" + "; ".join(bits) + "}" if bits else ""


def line(record, full, with_plugin=False):
    desc = record["description"]
    if not full and len(desc) > WIDTH:
        desc = desc[: WIDTH - 1].rstrip() + "…"
    head = f"{record['plugin']} › " if with_plugin else ""
    control = record["control"] or "–"
    return f"{head}{record['name']} | {control} | {desc}{extras(record, full)}"


def print_plugin(name, rows, full):
    source = rows[0]["source"]
    print(f"{name} — {len(rows)} parameters  ({source}, {rows[0]['topic_id'].split('/')[0]} guide)")
    groups = OrderedDict()
    for r in rows:
        groups.setdefault((r["topic_title"], r["topic_id"]), []).append(r)
    for (title, topic_id), members in groups.items():
        pages = sorted({m["page"] for m in members})
        span = f"p.{pages[0]}" if len(pages) == 1 else f"p.{pages[0]}–{pages[-1]}"
        print(f"\n# {title}  ({span}, id={topic_id})")
        for r in members:
            print("  " + line(r, full))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("plugin", nargs="?", help="plug-in, instrument, control surface or user-guide chapter")
    parser.add_argument("--grep", help="only parameters whose name or description matches (regex, case-insensitive)")
    parser.add_argument("--json", action="store_true", help="emit JSON records instead of lines")
    parser.add_argument("--full", action="store_true", help="do not truncate descriptions")
    parser.add_argument("--list", action="store_true", help="every plug-in with its parameter count")
    parser.add_argument("--search", metavar="TEXT", help="find a parameter across all plug-ins (name first, then description)")
    parser.add_argument("--source", help="user-guide | effects | instruments | control-surfaces")
    parser.add_argument("-n", "--limit", type=int, default=SEARCH_LIMIT, help=f"max --search hits (default {SEARCH_LIMIT})")
    args = parser.parse_args()

    records = load()
    if args.source:
        records = [r for r in records if r["source"] == args.source]
    counts, sources = plugins(records)

    if args.list:
        if args.json:
            print(json.dumps([{"plugin": p, "source": sources[p], "parameters": c} for p, c in counts.most_common()], ensure_ascii=False, indent=1))
            return
        print(f"{len(counts)} plug-ins / devices / chapters, {len(records)} parameters\n")
        for plugin, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {count:5}  {plugin}  ({sources[plugin]})")
        return

    if args.search:
        pattern = re.compile(args.search, re.I)
        name_hits = [r for r in records if pattern.search(r["name"])]
        desc_hits = [r for r in records if not pattern.search(r["name"]) and pattern.search(r["description"])]
        hits = name_hits + desc_hits
        if args.plugin:
            chosen, _ = resolve(args.plugin, counts)
            hits = [r for r in hits if r["plugin"] in chosen]
        if not hits:
            print(f"No parameter matches {args.search!r}. Try --list, or lookup.py for prose.")
            return
        if args.json:
            print(json.dumps(hits[: args.limit], ensure_ascii=False, indent=1))
            return
        print(f"{len(hits)} parameters match {args.search!r} ({len(name_hits)} by name)\n")
        for r in hits[: args.limit]:
            print(line(r, args.full, with_plugin=True) + f"  (p.{r['page']})")
        if len(hits) > args.limit:
            print(f"\n{len(hits) - args.limit} more — narrow with a plug-in name, --source, or raise -n.")
        return

    if not args.plugin:
        parser.error("give a plug-in name, --list, or --search TEXT")

    chosen, candidates = resolve(args.plugin, counts)
    if not chosen:
        if candidates:
            print(f"{args.plugin!r} is ambiguous. Did you mean: " + ", ".join(candidates[:12]) + ("…" if len(candidates) > 12 else ""))
        else:
            print(f"No plug-in named {args.plugin!r}. Try --list, or --search {args.plugin!r}.")
        return

    rows = [r for r in records if r["plugin"] in chosen]
    if args.grep:
        pattern = re.compile(args.grep, re.I)
        rows = [r for r in rows if pattern.search(r["name"]) or pattern.search(r["description"])]
        if not rows:
            print(f"No parameter of {chosen[0]} matches {args.grep!r}.")
            return

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    print_plugin(chosen[0], rows, args.full)


if __name__ == "__main__":
    main()
