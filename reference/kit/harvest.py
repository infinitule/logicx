#!/usr/bin/env python3
"""Harvest Apple's four Logic Pro guides into a parameterized, deterministic corpus.

Nothing here is crawled from the web. The four PDFs Apple publishes for Logic Pro
for Mac (User Guide, Effects, Instruments, Control Surfaces Support) are read off
this machine with pypdf, split into topics along the PDF outline (bookmarks), and
every `• Name control: description` bullet and every control-surface table row is
lifted into its own parameter record.

    python3 harvest.py                       # write ../data/logic.jsonl + parameters.jsonl
    python3 harvest.py --dry-run             # report what it would harvest
    python3 harvest.py --pdf-dir ~/Downloads # where the logic-pro-mac-*.pdf files live
    python3 harvest.py --source effects      # one guide only
    python3 harvest.py --no-cache            # re-extract page text instead of using data/cache

Page text is cached per source in data/cache/<source>.pages.json so re-runs of the
parsers are instant. Re-run after Apple updates the guides; the manifest records the
file name and size each record came from.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
CACHE = DATA / "cache"
TOPICS_OUT = DATA / "logic.jsonl"
PARAMS_OUT = DATA / "parameters.jsonl"
MANIFEST_OUT = DATA / "manifest.json"
DEFAULT_PDF_DIR = Path.home() / "Downloads"

# source id -> (file name, running-header guide name)
SOURCES = {
    "user-guide": ("logic-pro-mac-user-guide.pdf", "Logic Pro for Mac"),
    "effects": ("logic-pro-mac-effects-user-guide.pdf", "Logic Pro Effects for Mac"),
    "instruments": ("logic-pro-mac-instruments-user-guide.pdf", "Logic Pro Instruments for Mac"),
    "control-surfaces": (
        "logic-pro-mac-control-surfaces-support-guide.pdf",
        "Control Surfaces Support Guide for Logic Pro",
    ),
}

SKIP_TITLES = re.compile(r"^(contents|copyright.*)$", re.I)

IMPERATIVE = (
    "use add create set record edit choose move apply configure work open save delete "
    "remove adjust change select import export assign copy paste play show hide view "
    "navigate connect install enable disable turn switch control convert bounce share "
    "mix master automate route load insert replace rename resize split join merge drag "
    "zoom scroll mute solo arm monitor trim quantize transpose tune reset customize "
    "define print make get start stop position display find search manage organize "
    "prepare send receive sync synchronize name snap lock unlock group nudge loop "
    "repeat reverse fade pan fix detect analyze analyse generate build browse preview "
    "audition arrange compose program draw erase color colour align extend shorten cut "
    "trigger capture freeze flatten unpack pack sample layer modulate process render "
    "write read undo redo learn map activate deactivate check test try restore revert "
    "update upgrade"
).split()

CONTROL_WORDS = [
    "knob", "knobs", "slider", "sliders", "field", "fields", "button", "buttons", "switch",
    "switches", "menu", "menus", "pop-up menu", "pop-up menus", "shortcut menu",
    "checkbox", "checkboxes", "display", "displays", "dial", "dials", "pad", "pads",
    "keyboard", "value field", "value fields", "text field", "text fields", "meter",
    "meters", "graph", "graphs", "meter/graph", "indicator", "indicators", "led", "leds",
    "fader", "faders", "wheel", "wheels", "encoder", "encoders", "selector", "selectors",
    "handle", "handles", "icon", "icons", "bar", "bars", "grid", "grids", "list", "lists",
    "tab", "tabs", "key", "keys", "control", "controls", "stepper", "steppers", "slot",
    "slots", "ring", "rings", "box", "boxes", "matrix", "area", "areas", "panel", "panels",
    "pane", "panes", "section", "sections", "envelope", "envelopes", "screen", "v-pot",
    "v-pots", "xy pad", "x/y pad", "joystick", "joysticks", "number field", "numeric field",
    "radio buttons", "mode buttons", "parameter", "parameters", "value", "values", "lamp",
    "lamps", "arrow", "arrows", "arrow buttons", "label", "labels", "readout", "readouts",
    "popup menu", "drop-down menu", "dropdown menu", "strip", "strips", "trigger",
    "triggers", "curve", "curves", "touch strip", "ribbon", "toggle", "toggles", "pedal",
    "pedals",
]
_CW = "|".join(sorted((re.escape(w) for w in CONTROL_WORDS), key=len, reverse=True))
# "Threshold knob and field", "Gain Reduction meter/graph", "Soft/Hard buttons"
CONTROL_RE = re.compile(
    rf"^(?P<name>.+?)\s+(?P<control>(?:{_CW})(?:\s*(?:and|or|/|,)\s*(?:{_CW}))*)$",
    re.I,
)
# "• To reset a parameter: Option-click it." is a how-to step, not a parameter
TASK_BULLET_RE = re.compile(r"^(?:To|If|When|While|For|In|On|Do|Use|Click|Choose|Press|Drag|Select|Hold|Turn)\b.*\s\S+\s\S+", re.I)
BULLET_RE = re.compile(r"^•\s*(?P<head>[^:]{1,90}?)\s*:\s*(?P<desc>.*)$")

# Control-surface tables: "Control Action", "Control Modifier Assignment", ...
TABLE_HEADER_RE = re.compile(
    r"^(?P<left>Control|Button|Pad|Display|Mode|Input|Key|Fader|Knob|Switch|Encoder|Control Label)"
    r"(?P<mod> Modifier)? (?P<right>Action|Assignment|Function(?:/Comments)?|Description|Comments)$"
)
MODIFIERS = {"SHIFT", "OPTION", "CONTROL", "CMD/ALT", "CMD", "ALT", "COMMAND", "CTRL"}
LEFT_WORDS = {
    "v-pot", "v-pots", "fader", "faders", "pad", "pads", "knob", "knobs", "rotary", "pot",
    "pots", "encoder", "encoders", "jog", "wheel", "foot", "switch", "counter", "cursor",
    "left", "right", "up", "down", "zoom", "scrub", "button", "buttons", "bank", "channel",
    "strip", "main", "master", "transport", "arrow", "keys", "key", "segment", "display",
    "or", "and", "+", "—", "–", "-", "to", "select", "mute", "solo", "rec", "record",
    "track", "send", "pan", "eq", "plug-in", "instrument", "automation", "group", "marker",
    "nudge", "cycle", "drop", "replace", "click", "save", "undo", "global", "user", "mode",
    "modes", "touch", "write", "latch", "read", "trim", "off", "on", "function", "ring",
    "led", "leds", "lcd", "meter", "meters", "data", "entry", "rotate", "press", "turn",
    "hold", "slider", "sliders", "dial", "dials", "scene", "dialog", "shuttle", "page",
    "output", "input", "volume", "assign", "assignment", "parameter", "control", "pedal",
    "exp", "sustain", "modulation", "pitch", "bend", "mod", "time", "code", "beats",
    "name", "value", "smpte", "flip", "edit", "pots", "level", "number", "numeric", "keypad",
    "shift", "option", "cmd/alt", "cmd", "alt", "command", "ctrl",
}


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def norm(text):
    """Whitespace/quote-normalised, lower-cased form used for heading matching."""
    text = text.replace("\xa0", " ").replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text.rstrip(".").strip()


SUFFIX_RE = re.compile(r"\s+in logic pro(?: for mac)?$")
PREFIX_RE = re.compile(r"^logic pro(?: for mac)?\s+")
KERNED_T_RE = re.compile(r"(?<![A-Za-z])T ([a-z])")
GLUED_HEADING_RE = re.compile(r"^(.*?[.!?)])([A-Z][^.]{3,110} in Logic Pro(?: for Mac)?)$")


def heading_equals(line_norm, title_norm):
    """A printed heading equals a bookmark title bare, as `<title> in Logic Pro [for Mac]`,
    or as `Logic Pro [for Mac] <title>` (the user guide's interface chapters)."""
    if line_norm == title_norm:
        return True
    if SUFFIX_RE.sub("", line_norm) == title_norm:
        return True
    return PREFIX_RE.sub("", line_norm) == title_norm


def slug(text):
    text = norm(text)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "untitled"


def strip_plugin_suffix(title):
    title = title.replace("\xa0", " ").strip()
    for _ in range(2):
        title = re.sub(r"\s+(main |global |output |side chain )?(controls|parameters|overview|interface)$", "", title, flags=re.I)
    return title


# ---------------------------------------------------------------------------
# PDF access: page text (cached) and outline
# ---------------------------------------------------------------------------

def extract_pages(pdf_path):
    from pypdf import PdfReader  # imported lazily so the parsers work without pypdf

    import pypdf

    reader = PdfReader(str(pdf_path))
    text = [page.extract_text() or "" for page in reader.pages]
    return {
        "file": pdf_path.name,
        "size": pdf_path.stat().st_size,
        "pages": len(text),
        "pypdf": pypdf.__version__,
        "text": text,
    }


def load_pages(source, pdf_path, use_cache=True, cache_dir=CACHE):
    """Per-page text for a source, from data/cache when it matches the PDF on disk."""
    cache_file = cache_dir / f"{source}.pages.json"
    if use_cache and cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cached = None
        if (
            cached
            and isinstance(cached.get("text"), list)
            and cached.get("file") == pdf_path.name
            and cached.get("size") == pdf_path.stat().st_size
            and cached.get("pages") == len(cached["text"])
            and all(isinstance(p, str) for p in cached["text"])
        ):
            return cached
    pages = extract_pages(pdf_path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    return pages


def load_outline(source, pdf_path, use_cache=True, cache_dir=CACHE):
    """Flat outline: [{title, page, depth, parent(index or None)}], cached beside the pages."""
    cache_file = cache_dir / f"{source}.outline.json"
    size = pdf_path.stat().st_size
    if use_cache and cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cached = None
        if cached and cached.get("file") == pdf_path.name and cached.get("size") == size:
            return cached["entries"]

    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    entries = []

    def walk(items, depth, parent):
        previous = None
        for item in items:
            if isinstance(item, list):
                if previous is not None:
                    walk(item, depth + 1, previous)
                continue
            try:
                page = reader.get_destination_page_number(item)
            except Exception:  # noqa: BLE001 - a broken bookmark is not worth a crash
                page = None
            if page is None:
                continue
            entries.append(
                {
                    "title": re.sub(r"\s+", " ", str(item.title).replace("\xa0", " ")).strip(),
                    "page": int(page),
                    "depth": depth,
                    "parent": parent,
                }
            )
            previous = len(entries) - 1

    walk(reader.outline, 0, None)
    entries = dedupe_outline_trees(entries)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps({"file": pdf_path.name, "size": size, "entries": entries}, ensure_ascii=False),
        encoding="utf-8",
    )
    return entries


def dedupe_outline_trees(entries):
    """Some guides carry two bookmark trees (a shallow one and the full one). Keep the largest."""
    tops = [i for i, e in enumerate(entries) if e["depth"] == 0]
    blocks = [[]]
    block_key = None  # first real chapter of the current block; its repeat starts a new tree
    for i in tops:
        key = (entries[i]["title"], entries[i]["page"])
        if key[0].lower() == "contents":
            blocks[-1].append(i)
            continue
        if block_key is None:
            block_key = key
        elif key == block_key:
            blocks.append([])
        blocks[-1].append(i)
    if len(blocks) == 1:
        return entries

    def block_range(block):
        start = block[0]
        nxt = [i for i in tops if i > block[-1]]
        end = nxt[0] if nxt else len(entries)
        return start, end

    ranges = [block_range(b) for b in blocks]
    best = max(ranges, key=lambda r: r[1] - r[0])
    keep = list(range(best[0], best[1]))
    remap = {old: new for new, old in enumerate(keep)}
    out = []
    for old in keep:
        e = dict(entries[old])
        e["parent"] = remap.get(e["parent"]) if e["parent"] is not None else None
        out.append(e)
    return out


# ---------------------------------------------------------------------------
# Page lines
# ---------------------------------------------------------------------------

def page_lines(text, printed_page, guide):
    """Physical lines of one page minus the running header. Each is (core, wrapped)."""
    text = text.replace("\xa0", " ").replace("\t", " ")
    text = KERNED_T_RE.sub(r"T\1", text)  # pypdf splits a kerned capital T: "T o", "T empo", "T ouch"
    raw = text.split("\n")
    lines = []
    for line in raw:
        wrapped = line.endswith(" ")
        # runs of 2+ blanks survive as a double space: the glossary separates `term  Definition` that way
        core = re.sub(r"\s{2,}", "  ", line.strip())
        glued = GLUED_HEADING_RE.match(core)
        if glued:  # "…tonal quality.Modulation pedals in Logic Pro for Mac" (missing line break)
            lines.append((glued.group(1).strip(), False))
            core = glued.group(2).strip()
        lines.append((core, wrapped))
    # a hyphenated word split across lines carries no trailing space: "plug-" / "ins"
    for i in range(len(lines) - 1):
        if lines[i][0].endswith("-") and lines[i + 1][0][:1].islower():
            lines[i] = (lines[i][0], True)
    # running header: page number on one line, guide name on the next
    non_empty = [i for i, (core, _) in enumerate(lines) if core]
    if len(non_empty) >= 2:
        a, b = non_empty[0], non_empty[1]
        if lines[a][0] == str(printed_page) and norm(lines[b][0]) == norm(guide):
            lines = lines[b + 1 :]
    return lines


def build_lines(pages, guide):
    """All pages -> flat list of dicts {core, wrapped, page}, plus page -> first index."""
    flat = []
    first = {}
    for idx, text in enumerate(pages["text"]):
        printed = idx + 1
        first[printed] = len(flat)
        for core, wrapped in page_lines(text, printed, guide):
            flat.append({"core": core, "wrapped": wrapped, "page": printed})
    first[len(pages["text"]) + 1] = len(flat)
    return flat, first


def join_wrapped(lines):
    """Physical lines -> logical lines (wrapped lines joined), keeping the page of each."""
    out = []
    buf = None
    for line in lines:
        core = line["core"]
        if buf is None:
            if not core:
                continue
            buf = {"text": core, "page": line["page"], "open": line["wrapped"]}
        elif buf["open"] and core:
            if buf["text"].endswith("-") and core[0].islower():
                buf["text"] = buf["text"] + core  # "plug-" + "in" -> "plug-in"
            else:
                buf["text"] = (buf["text"] + " " + core).strip()
            buf["open"] = line["wrapped"]
        else:
            out.append(buf)
            buf = {"text": core, "page": line["page"], "open": line["wrapped"]} if core else None
    if buf is not None:
        out.append(buf)
    return [{"text": b["text"], "page": b["page"]} for b in out if b["text"]]


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------

LP_RE = re.compile(r"\blogic pro\b")


def heading_shaped(core, text_norm=None):
    """Printed topic headings always name Logic Pro somewhere: `X in Logic Pro for Mac`,
    `Logic Pro for Mac X`, `Create Logic Pro for Mac projects`, `Set up your HUI with Logic Pro`…"""
    text_norm = text_norm if text_norm is not None else norm(core)
    words = core.split()
    if len(core) >= 120 or len(words) < 3 or not LP_RE.search(text_norm):
        return False
    if " see " in f" {text_norm} " or " > " in core or "(or " in core:
        return False
    if text_norm.startswith(("to ", "if ", "when ", "note:", "tip:", "important:", "you ", "this ", "a ", "an ")):
        return False
    first = words[0]
    if first.isupper() and first in MODIFIERS:
        return False
    return not re.match(r"^(v-pot|f\d)", text_norm)


TITLE_STOP = set("in for of the a an and to with on your logic pro mac use using overview".split())


def title_word_set(text):
    words = re.findall(r"[a-z0-9]+", norm(text))
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words} - TITLE_STOP


def is_structural_heading(lines, i, end):
    """Is physical line i the start of a printed topic heading? Returns its span in lines (0 = no)."""
    core = lines[i]["core"]
    if not core or core[0] in "•-(" or core[0].islower() or core[-1] in ".:;,!?":
        return 0
    if re.match(r"^\d+[.)]\s", core):
        return 0
    prev = lines[i - 1] if i > 0 else None
    if prev and prev["wrapped"] and prev["core"] and prev["core"][-1] not in ".!?:)\"”":
        return 0  # continuation of a wrapped body line
    ln = norm(core)
    if not lines[i]["wrapped"]:
        return 1 if heading_shaped(core, ln) else 0
    nxt = lines[i + 1] if i + 1 < end else None
    if nxt is None or not nxt["core"]:
        return 0
    fresh_start = nxt["core"][0].isupper() or nxt["core"][0] in "•\"“(0123456789"
    # a complete heading that merely carries a trailing space
    if fresh_start and heading_shaped(core, ln) and (SUFFIX_RE.search(ln) or len(core) < 70):
        return 1
    # a heading wrapped over two or three lines
    if not nxt["wrapped"] and len(nxt["core"]) <= 60 and nxt["core"][-1] not in ".:;,!?":
        joined = core + " " + nxt["core"]
        if len(joined) < 140 and heading_shaped(joined):
            return 2
    third = lines[i + 2] if i + 2 < end else None
    if nxt["wrapped"] and third and third["core"] and not third["wrapped"] and len(third["core"]) <= 40 and third["core"][-1] not in ".:;,!?":
        joined = core + " " + nxt["core"] + " " + third["core"]
        if len(joined) < 160 and heading_shaped(joined):
            return 3
    return 0


def locate_headings(entries, lines, first, children=None):
    """For each outline entry find (line index, span) of its heading, in document order.

    Exact title (bare or suffixed, over one or two lines) wins; a leaf whose printed heading
    differs from its bookmark title takes the next structural heading on its page.
    """
    children = children or {}
    last = 0
    positions = []
    for k, entry in enumerate(entries):
        page = entry["page"] + 1
        start = max(first.get(page, 0), last)
        end = first.get(page + 1, len(lines))
        title = norm(entry["title"])
        is_section = k in children

        def find_exact(target):
            for i in range(start, end):
                core = lines[i]["core"]
                if not core:
                    continue
                if heading_equals(norm(core), target):
                    return i, 1
                if lines[i]["wrapped"] and i + 1 < end and lines[i + 1]["core"]:
                    joined = core + " " + lines[i + 1]["core"]
                    if heading_equals(norm(joined), target):
                        return i, 2
                    if lines[i + 1]["wrapped"] and i + 2 < end and lines[i + 2]["core"]:
                        if heading_equals(norm(joined + " " + lines[i + 2]["core"]), target):
                            return i, 3
            return None

        def find_fuzzy(target):
            """A short standalone line containing every word of the title, in order
            ("Novation Launchpad modes overview" for the bookmark "Launchpad modes overview")."""
            words = target.split()
            if len(words) < 2:
                return None
            pattern = re.compile(r"(?:^|\s)" + r"\s+(?:\S+\s+){0,2}?".join(re.escape(w) for w in words) + r"(?:\s|$)")
            for i in range(start, end):
                core = lines[i]["core"]
                if not core or lines[i]["wrapped"] or core[0] in "•-(" or core[0].islower() or core[-1] in ".:;,!?":
                    continue
                if len(core) > min(90, 2 * len(target) + 25) or re.match(r"^\d+[.)]\s", core):
                    continue
                prev = lines[i - 1] if i > 0 else None
                if prev and prev["wrapped"] and prev["core"] and prev["core"][-1] not in ".!?:)\"”":
                    continue
                if pattern.search(SUFFIX_RE.sub("", norm(core))):
                    return i, 1
            return None

        def find_structural():
            """The next printed heading on the page — unless it shares no words with the
            bookmark title while a later heading on the page shares at least half of them
            (a task sub-heading such as "Display and insert legacy plug-ins" can precede
            the topic's real heading)."""
            candidates = []
            i = start
            while i < end:
                span = is_structural_heading(lines, i, end)
                if span:
                    candidates.append((i, span))
                    i += span
                else:
                    i += 1
            if not candidates:
                return None
            if len(candidates) == 1:
                return candidates[0]
            title_words = title_word_set(title)
            if not title_words:
                return candidates[0]

            def overlap(cand):
                text = " ".join(lines[cand[0] + j]["core"] for j in range(cand[1]))
                return len(title_words & title_word_set(text)) / len(title_words)

            if overlap(candidates[0]) > 0:
                return candidates[0]
            best = max(candidates, key=lambda c: (overlap(c), -c[0]))
            return best if overlap(best) >= 0.5 else candidates[0]

        hit = find_exact(title)
        found = hit is not None
        if hit is None and not is_section:
            hit = find_fuzzy(title) or find_structural()
        if hit is None:
            hit = (start, 0)
        positions.append({"index": hit[0], "span": hit[1], "found": found, "structural": hit[1] > 0})
        last = hit[0] + hit[1]
    return positions


def kind_for(title, is_section):
    if is_section:
        return "section"
    t = norm(title)
    if re.search(r"\b(parameters|controls)\b", t):
        return "parameters"
    first = re.split(r"[\s,:]+", t, maxsplit=1)[0]
    if first in IMPERATIVE:
        return "task"
    if t.endswith("overview") or t.endswith("introduction"):
        return "overview"
    return "topic"


def ancestors(entries, k):
    chain = []
    p = entries[k]["parent"]
    while p is not None:
        chain.append(p)
        p = entries[p]["parent"]
    return list(reversed(chain))


EFFECT_PLUGIN_CHAPTERS = {"Mastering Assistant": "Mastering Assistant", "Space Designer convolution reverb": "Space Designer"}
INSTRUMENT_GROUP_CHAPTERS = {"Instruments overview", "Legacy instruments", "Synthesizer basics", "Utilities"}


def plugin_for(source, entries, k):
    chain = ancestors(entries, k) + [k]
    chapter = entries[chain[0]]["title"]
    if source in ("user-guide", "control-surfaces"):
        return chapter
    if source == "effects":
        if chapter in EFFECT_PLUGIN_CHAPTERS:
            return EFFECT_PLUGIN_CHAPTERS[chapter]
        cand = entries[chain[1]]["title"] if len(chain) > 1 else chapter
        return strip_plugin_suffix(cand)
    # instruments
    if chapter not in INSTRUMENT_GROUP_CHAPTERS:
        return chapter
    cand = entries[chain[1]]["title"] if len(chain) > 1 else chapter
    return strip_plugin_suffix(cand)


def parse_glossary(logical, source, file_name, parent_id, path):
    """`term  Definition…` lines -> one glossary record per term."""
    records = []
    term_re = re.compile(r"^(?P<term>[^\s][^:]{0,60}?)  (?P<definition>[A-Z(\"“].{10,})$")
    for line in logical:
        m = term_re.match(line["text"])
        if not m:
            continue
        term = m.group("term").strip()
        if len(term.split()) > 7:
            continue
        records.append(
            {
                "source": source,
                "id": f"{parent_id}/{slug(term)}",
                "title": term,
                "path": path,
                "kind": "glossary",
                "page_start": line["page"],
                "page_end": line["page"],
                "text": re.sub(r"\s{2,}", " ", m.group("definition")).strip(),
                "file": file_name,
            }
        )
    return records


def harvest_source(source, pdf_dir=DEFAULT_PDF_DIR, use_cache=True, cache_dir=CACHE):
    """One guide -> (topic records, parameter records, stats)."""
    file_name, guide = SOURCES[source]
    pdf_path = Path(pdf_dir).expanduser() / file_name
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    pages = load_pages(source, pdf_path, use_cache, cache_dir)
    entries = load_outline(source, pdf_path, use_cache, cache_dir)
    lines, first = build_lines(pages, guide)
    children = {}
    for k, e in enumerate(entries):
        if e["parent"] is not None:
            children.setdefault(e["parent"], []).append(k)
    positions = locate_headings(entries, lines, first, children)

    topics = []
    params = []
    ids = {}
    stats = {"entries": len(entries), "headings_found": 0, "headings_missing": 0, "sections": 0}
    glossary_parent = None

    for k, entry in enumerate(entries):
        if SKIP_TITLES.match(entry["title"].strip()):
            continue
        pos = positions[k]
        stats["headings_found" if pos["structural"] else "headings_missing"] += 1
        body_start = pos["index"] + pos["span"]
        body_end = positions[k + 1]["index"] if k + 1 < len(entries) else len(lines)
        logical = join_wrapped(lines[body_start:body_end])

        chain = ancestors(entries, k)
        path = [entries[a]["title"] for a in chain]
        parent_slug = slug(entries[chain[-1]]["title"]) if chain else None
        base = f"{source}/{parent_slug}/{slug(entry['title'])}" if parent_slug else f"{source}/{slug(entry['title'])}"
        ident = base
        n = 1
        while ident in ids:
            n += 1
            ident = f"{base}-{n}"
        ids[ident] = True

        kids = children.get(k, [])
        is_section = bool(kids) and not logical
        text = "\n".join(re.sub(r"\s{2,}", " ", l["text"]) for l in logical)
        if is_section:
            stats["sections"] += 1
            text = "Topics: " + " · ".join(entries[c]["title"] for c in kids)
        elif kids and entries[kids[0]]["page"] == entry["page"]:
            text = text + "\nTopics: " + " · ".join(entries[c]["title"] for c in kids)

        page_start = lines[pos["index"]]["page"] if pos["index"] < len(lines) else entry["page"] + 1
        page_end = logical[-1]["page"] if logical else page_start
        kind = kind_for(entry["title"], is_section)
        if norm(entry["title"]) == "glossary":
            kind = "glossary"
            glossary_parent = (ident, path + [entry["title"]])
            gloss = parse_glossary(logical, source, file_name, ident, glossary_parent[1])
            if len(gloss) >= 20:
                text = f"{len(gloss)} terms. " + "Terms: " + " · ".join(g["title"] for g in gloss)
            else:
                gloss = []
        else:
            gloss = []

        record = {
            "source": source,
            "id": ident,
            "title": entry["title"],
            "path": path,
            "kind": kind,
            "page_start": page_start,
            "page_end": page_end,
            "text": text,
            "file": file_name,
        }
        topics.append(record)
        topics.extend(gloss)

        if not is_section and kind != "glossary":
            params.extend(
                parse_parameters(
                    logical,
                    source=source,
                    plugin=plugin_for(source, entries, k),
                    topic=record,
                    titled_parameters=(kind == "parameters") or bool(re.search(r"\bsettings?\b", norm(entry["title"]))),
                )
            )

    stats["topics"] = len(topics)
    stats["parameters"] = len(params)
    stats["file"] = file_name
    stats["size"] = pdf_path.stat().st_size
    stats["pages"] = pages["pages"]
    return topics, params, stats


# ---------------------------------------------------------------------------
# Parameters: bullets and control-surface tables
# ---------------------------------------------------------------------------

CHOICE_LEAD_RE = re.compile(
    r"(?:The choices are|Choices are|The options are|Options are|Available options are|"
    r"Possible values are|Choose from|Choose between|Options include|Choices include|"
    r"Settings are|The settings are|Available modes are|The modes are|Modes are|"
    r"Select from|Select between|Switch between)\s*:?\s*(?P<list>[^.;]+?)\.(?:\s|$)",
    re.I,
)
COLON_LIST_RE = re.compile(r":\s+(?P<list>[^.:;]+?)\.(?:\s|$)")
UNIT = r"(?:%|dB|Hz|kHz|ms|s|sec|seconds?|st|cents?|semitones?|octaves?|samples?|bpm|BPM|degrees?|°|x|beats?|bars?|ticks?|steps?|voices?|notes?)"
NUM = r"[−–\-+]?\d+(?:\.\d+)?"
RANGE_RE = re.compile(
    rf"(?:\bfrom|\bbetween|\brange of|\branges? (?:from|between))\s+(?P<a>{NUM})\s*(?P<ua>{UNIT})?\s*(?:to|–|—|and|down to|up to)\s*(?P<b>{NUM})\s*(?P<ub>{UNIT})(?![A-Za-z])",
    re.I,
)


def _num(text):
    text = text.replace("−", "-").replace("–", "-").replace("+", "")
    value = float(text)
    return int(value) if value.is_integer() else value


def split_enumeration(text):
    """'A, B and C, D, or E, F, and G' -> ['A', 'B and C', 'D', 'E, F, and G'] (best effort)."""
    text = text.strip().rstrip(".")
    head, tail = None, None
    for sep in (", or ", ", and "):
        if sep in text:
            idx = text.rfind(sep)
            head, tail = text[:idx], text[idx + len(sep) :]
            break
    if head is None:
        for sep in (" or ", " and "):
            if sep in text and "," not in text:
                head, tail = text.split(sep, 1)
                break
    if head is None:
        return None
    items = [i.strip() for i in head.split(", ")] + [tail.strip()]
    items = [re.sub(r"^(?:or|and)\s+", "", i).strip() for i in items if i.strip()]
    if not 2 <= len(items) <= 12:
        return None
    for item in items:
        if len(item) > 40 or len(item.split()) > 6 or "—" in item:
            return None
        if re.search(r"\b(?:you|the|is|are|can|when|which|that|this|to)\b", item.lower()) and len(item.split()) > 3:
            return None
        if re.match(r"^(?:two|three|four|five|six|seven|eight|several|other|additional|more|many|all)\b", item.lower()):
            return None
    return items


def parse_choices(description):
    m = CHOICE_LEAD_RE.search(description)
    if m:
        items = split_enumeration(m.group("list"))
        if items:
            return items
    for m in COLON_LIST_RE.finditer(description):
        candidate = m.group("list")
        if re.search(r",\s*(?:or|and)\s+|\s(?:or|and)\s", candidate):
            items = split_enumeration(candidate)
            # without a lead phrase, only accept proper-noun style items (Off, Soft, 1/4 …)
            if items and all(i[0].isupper() or i[0].isdigit() for i in items):
                return items
    return None


def parse_range(description):
    for m in RANGE_RE.finditer(description):
        if "example" in description[: m.start()].lower():
            continue  # numbers inside a worked example are not the parameter's range
        ua, ub = m.group("ua"), m.group("ub")
        try:
            lo, hi = _num(m.group("a")), _num(m.group("b"))
        except ValueError:
            continue
        unit = ub
        if ua and ua.lower() != ub.lower():
            pair = (ua, ub)
            if pair == ("Hz", "kHz"):
                hi = hi * 1000
                unit = "Hz"
            elif pair == ("ms", "s"):
                hi = hi * 1000
                unit = "ms"
            else:
                continue
        if unit.lower() in ("sec", "second", "seconds"):
            unit = "s"
        if unit.lower() == "bpm":
            unit = "bpm"
        if lo > hi:
            lo, hi = hi, lo
        return {"min": lo, "max": hi, "unit": unit}
    return None


def split_head(head):
    """'Threshold knob and field' -> ('Threshold', 'knob and field'); 'Soft' -> ('Soft', None)."""
    head = head.strip().rstrip(":").strip()
    m = CONTROL_RE.match(head)
    if m and m.group("name").strip():
        control = re.sub(r"\s+", " ", m.group("control").lower()).strip()
        control = re.sub(r"\s*/\s*", "/", control)
        return m.group("name").strip(), control
    return head, None


def looks_like_heading(text):
    return bool(SUFFIX_RE.search(norm(text))) and len(text) < 120


def _row_split(text):
    """Control-surface table row -> (left column, right column) or None."""
    tokens = text.split()
    if len(tokens) < 2:
        return None
    n = 1  # the first token always belongs to the control column

    def leftish(tok):
        t = tok.strip("(),.:;")
        if not t:
            return False
        if t.lower() in LEFT_WORDS:
            return True
        if any(ch.isdigit() for ch in t):
            return True
        if t.isupper() and len(t) >= 2:
            return True
        return False

    while n < len(tokens) and leftish(tokens[n]):
        n += 1
    left = " ".join(tokens[:n]).strip()
    right = " ".join(tokens[n:]).strip()
    if not right or not left:
        return None
    if not (right[0].isupper() or right[0] in "“\"(—"):
        return None
    if left.endswith(",") or left.lower().startswith(("note", "tip", "important")):
        return None
    if len(left) > 40:
        return None
    return left, right


def parse_table(logical, i, header, source, plugin, topic):
    """Rows of a Control/Action style table starting after logical[i]. Returns (records, next index)."""
    three_col = bool(header.group("mod"))
    records = []
    current = None
    last_control = None
    j = i + 1
    while j < len(logical):
        text = logical[j]["text"]
        page = logical[j]["page"]
        if TABLE_HEADER_RE.match(text) or looks_like_heading(text):
            break
        if text.startswith("•"):
            if current is not None:
                current["description"] += " " + text
            j += 1
            continue
        split = _row_split(text)
        if split is None and re.fullmatch(r"[^a-z]{0,3}[\w /+-]{1,32} —", text):
            split = (text[:-2].strip(), "—")  # "V-Pot 5 —": a control with no action
        if split is None:
            if current is None:
                break
            if text.endswith(":"):
                break
            current["description"] += " " + text
            j += 1
            continue
        left, right = split
        name = left
        if three_col:
            toks = left.split(" ")
            mods = [t for t in toks if t.upper() in MODIFIERS or t == "—"]
            ctrl = " ".join(t for t in toks if t.upper() not in MODIFIERS and t != "—").strip()
            if ctrl:
                last_control = ctrl
            real_mods = [m for m in mods if m != "—"]
            if real_mods and last_control:
                name = f"{' + '.join(real_mods)} + {last_control}"
            elif ctrl:
                name = ctrl
            elif last_control:
                name = last_control
        if name.endswith(" —"):
            name = name[:-2].strip()
        current = {
            "source": source,
            "plugin": plugin,
            "topic_id": topic["id"],
            "topic_title": topic["title"],
            "page": page,
            "name": name,
            "control": "table",
            "description": right,
            "choices": None,
            "range": None,
        }
        records.append(current)
        j += 1
    for r in records:
        r["description"] = re.sub(r"\s+", " ", r["description"]).strip()
    return records, j


def parse_parameters(logical, source, plugin, topic, titled_parameters):
    """Bullets (`• Name control: description`) and control-surface tables -> parameter records."""
    records = []
    i = 0
    current = None
    while i < len(logical):
        text = logical[i]["text"]
        page = logical[i]["page"]
        header = TABLE_HEADER_RE.match(text)
        if header and source == "control-surfaces":
            current = None
            rows, i = parse_table(logical, i, header, source, plugin, topic)
            records.extend(rows)
            continue
        if text.startswith("•"):
            current = None
            m = BULLET_RE.match(text)
            if m:
                name, control = split_head(re.sub(r"\s+", " ", m.group("head")))
                desc = m.group("desc").strip()
                if name and desc and (titled_parameters or control) and not name.startswith(("•", "—")) and not TASK_BULLET_RE.match(name):
                    current = {
                        "source": source,
                        "plugin": plugin,
                        "topic_id": topic["id"],
                        "topic_title": topic["title"],
                        "page": page,
                        "name": name,
                        "control": control,
                        "description": desc,
                        "choices": None,
                        "range": None,
                    }
                    records.append(current)
            i += 1
            continue
        if current is not None:
            # continuation paragraph until next bullet / heading / table header / blank
            if looks_like_heading(text) or TABLE_HEADER_RE.match(text):
                current = None
            else:
                current["description"] += " " + text
        i += 1
    for r in records:
        r["description"] = re.sub(r"\s+", " ", r["description"]).strip()
        if r["control"] != "table":
            r["choices"] = parse_choices(r["description"])
            r["range"] = parse_range(r["description"])
    return records


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def dumps(record):
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(dumps(record) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--pdf-dir", default=str(DEFAULT_PDF_DIR), help="folder holding the logic-pro-mac-*.pdf files")
    parser.add_argument("--source", choices=sorted(SOURCES), action="append", help="harvest one guide only (repeatable)")
    parser.add_argument("--out-dir", default=str(DATA), help="where to write logic.jsonl / parameters.jsonl / manifest.json")
    parser.add_argument("--no-cache", action="store_true", help="re-extract page text with pypdf")
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = parser.parse_args(argv)

    pdf_dir = Path(args.pdf_dir).expanduser()
    out_dir = Path(args.out_dir).expanduser()
    sources = args.source or list(SOURCES)
    missing = [SOURCES[s][0] for s in sources if not (pdf_dir / SOURCES[s][0]).exists()]
    if missing:
        sys.exit(f"Missing in {pdf_dir}: {', '.join(missing)}. Download Apple's Logic Pro guides there or pass --pdf-dir.")

    started = time.time()
    topics, params, manifest = [], [], {"sources": {}}
    for source in sources:
        t0 = time.time()
        t, p, stats = harvest_source(source, pdf_dir, use_cache=not args.no_cache)
        topics.extend(t)
        params.extend(p)
        stats["seconds"] = round(time.time() - t0, 1)
        manifest["sources"][source] = stats
        print(
            f"{source:17} {stats['topics']:5} topics  {stats['parameters']:6} parameters  "
            f"{sum(len(r['text']) for r in t) / 1000:8.1f} KB text  "
            f"({stats['headings_missing']} headings not found)  {stats['seconds']}s"
        )
    print(f"{'total':17} {len(topics):5} topics  {len(params):6} parameters")

    if not topics:
        sys.exit("Harvested nothing — check the PDFs.")
    if args.dry_run:
        return

    topics_out = out_dir / "logic.jsonl"
    params_out = out_dir / "parameters.jsonl"
    write_jsonl(topics_out, topics)
    write_jsonl(params_out, params)
    manifest.update(
        {
            "harvested": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "seconds": round(time.time() - started, 1),
            "topics": len(topics),
            "parameters": len(params),
            "pdf_dir": str(pdf_dir),
        }
    )
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nwrote {topics_out}  ({topics_out.stat().st_size / 1e6:.1f} MB)")
    print(f"wrote {params_out}  ({params_out.stat().st_size / 1e6:.1f} MB)")
    print(f"wrote {out_dir / 'manifest.json'}")


if __name__ == "__main__":
    main()
