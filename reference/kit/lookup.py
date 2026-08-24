#!/usr/bin/env python3
"""Query the harvested Logic Pro corpus offline.

    python3 lookup.py "plug-in edit view"              # rank by title match, then full text
    python3 lookup.py "automation modes" --source user-guide
    python3 lookup.py Compressor --kind parameters --full
    python3 lookup.py --kind task --source control-surfaces
    python3 lookup.py --stats                          # what is in the corpus

Output is kept compact on purpose: it is read in context. Narrow with --source / --kind
or raise -n rather than dumping the corpus.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
TOPICS = DATA / "logic.jsonl"
PARAMS = DATA / "parameters.jsonl"
SNIPPET = 300
KINDS = "section | overview | parameters | task | topic | glossary"
SOURCES = "user-guide | effects | instruments | control-surfaces"


def load(path=TOPICS):
    if not path.exists():
        sys.exit(f"No corpus at {path}. Run: python3 {Path(__file__).with_name('harvest.py')}")
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def score(record, pattern, exact):
    """Rank title hits above body hits; more body hits beats fewer."""
    title = record["title"]
    if exact:
        if pattern.fullmatch(title.strip()):
            return 1000
        return 100 if pattern.search(title) else 0

    points = 0
    if pattern.fullmatch(title.strip()):
        points += 800
    elif pattern.search(title):
        points += 500 - min(len(title), 200)
    points += min(len(pattern.findall(record["text"])), 20) * 5
    return points


def context(text, pattern):
    """The text around the first match, padded to a readable snippet."""
    match = pattern.search(text)
    if not match:
        return text[:SNIPPET].replace("\n", " ") + ("…" if len(text) > SNIPPET else "")
    start = max(0, match.start() - SNIPPET // 3)
    end = min(len(text), match.start() + (SNIPPET * 2) // 3)
    lead = "… " if start else ""
    tail = "…" if end < len(text) else ""
    return lead + text[start:end].replace("\n", " ") + tail


def pages(record):
    if record["page_start"] == record["page_end"]:
        return f"p.{record['page_start']}"
    return f"p.{record['page_start']}–{record['page_end']}"


def show(record, pattern, full):
    print(f"\n{record['title']}  [{record['kind']}]")
    print(f"  {' › '.join(record['path']) or record['source']}")
    if full:
        for line in record["text"].splitlines():
            print(f"  {line}")
    else:
        print(f"  {context(record['text'], pattern)}")
    print(f"  → {record['source']} {pages(record)}  ({record['file']})  id={record['id']}")


def stats(records):
    print(f"{len(records)} topics, {sum(len(r['text']) for r in records) / 1000:.0f} KB of text\n")
    for key in ("source", "kind"):
        print(f"by {key}:")
        for name, count in Counter(r[key] for r in records).most_common():
            print(f"  {count:5}  {name}")
        print()
    if PARAMS.exists():
        params = load(PARAMS)
        print(f"{len(params)} parameters, by plug-in / device / chapter (top 20):")
        for (plugin, source), count in Counter((p["plugin"], p["source"]) for p in params).most_common(20):
            print(f"  {count:5}  {plugin}  ({source})")
        print(f"\nList every plug-in with: python3 {Path(__file__).with_name('params.py')} --list")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("query", nargs="?", help="text to search for")
    parser.add_argument("--exact", action="store_true", help="match the title only")
    parser.add_argument("--full", action="store_true", help="print whole records, not snippets")
    parser.add_argument("--kind", help=KINDS)
    parser.add_argument("--source", help=SOURCES)
    parser.add_argument("-n", "--limit", type=int, default=None, help="max results (default 10, 25 for bare filters)")
    parser.add_argument("--stats", action="store_true", help="corpus summary")
    args = parser.parse_args()

    records = load()

    if args.stats:
        stats(records)
        return

    pool = records
    if args.kind:
        pool = [r for r in pool if r["kind"] == args.kind]
    if args.source:
        pool = [r for r in pool if r["source"] == args.source]

    if not args.query:
        if not (args.kind or args.source):
            parser.error("give a query, a --kind/--source filter, or --stats")
        limit = args.limit or 25
        for record in pool[:limit]:
            print(f"{record['title']}  [{record['kind']}]  → {record['source']} {pages(record)}  id={record['id']}")
        if len(pool) > limit:
            print(f"\n{len(pool) - limit} more — narrow with --kind/--source or raise -n.")
        else:
            print(f"\n{len(pool)} records match the filter.")
        return

    limit = args.limit or 10
    pattern = re.compile(re.escape(args.query), re.I)
    ranked = sorted(((score(r, pattern, args.exact), r) for r in pool), key=lambda pair: -pair[0])
    hits = [record for points, record in ranked if points > 0]

    if not hits:
        print(f"No match for {args.query!r}. Try --stats, a broader term, or params.py --search.")
        return

    for record in hits[:limit]:
        show(record, pattern, args.full)

    if len(hits) > limit:
        print(f"\n{len(hits) - limit} more — narrow with --source/--kind/--exact or raise -n.")


if __name__ == "__main__":
    main()
