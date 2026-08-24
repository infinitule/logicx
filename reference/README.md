# Documentation as a queryable corpus

An agent that recalls an application's behaviour from training is guessing. This
turns the vendor's own documentation into records you can query in milliseconds.

**The harvested corpora are not in this repository.** They are derived from
Apple's copyrighted PDFs; build them locally from your own copies.

## Build it

Download Apple's four Logic Pro for Mac guides (User Guide, Effects,
Instruments, Control Surfaces Support) and run:

```bash
pip install pypdf
python3 reference/kit/harvest.py --pdf-dir ~/Downloads --dry-run
python3 reference/kit/harvest.py --pdf-dir ~/Downloads      # ~2 minutes
```

Output lands in `reference/data/` (gitignored):

| File | Records | One record is |
|---|---|---|
| `logic.jsonl` | 3,048 | a topic, split along the PDF's own outline |
| `parameters.jsonl` | 7,447 | a single documented control |
| `manifest.json` | — | when, from which files, and the counts |

Topic record:

```json
{"source": "effects", "id": "effects/compressor/compressor-main-parameters",
 "title": "Compressor main parameters", "path": ["Dynamics processors", "Compressor"],
 "kind": "parameters", "page_start": 90, "page_end": 91,
 "text": "…\n• Circuit Type buttons: Choose the type of circuit…",
 "file": "logic-pro-mac-effects-user-guide.pdf"}
```

Parameter record:

```json
{"source": "effects", "plugin": "Compressor", "topic_id": "effects/compressor/…",
 "page": 90, "name": "Circuit Type", "control": "buttons",
 "description": "Choose the type of circuit emulated by Compressor…",
 "choices": ["Platinum Digital", "Studio VCA and FET", "Classic VCA", "Vintage VCA, FET, and Opto"],
 "range": null}
```

Records carry no timestamps, so two harvests of the same PDFs are byte-identical
— the corpus can live in a hash-checked cache.

## Query it

```bash
L=reference/kit/lookup.py
python3 $L "plug-in edit view"                 # title match first, then full text
python3 $L "automation modes" --source user-guide
python3 $L Compressor --kind parameters --full
python3 $L --stats

P=reference/kit/params.py
python3 $P Compressor                 # every parameter: name | control | description
python3 $P ES2 --grep oscillator
python3 $P "Mackie Control" --grep "V-POT"
python3 $P --list                     # every plug-in with its parameter count
python3 $P --search threshold         # one parameter across all plug-ins
```

Queries return in well under a second and are deliberately compact — the output
is read in an agent's context, so it is capped with a "N more, narrow with…"
line rather than dumping.

## Why it is built this way

- **Never load the corpus.** `logic.jsonl` is 5.9 MB. Querying costs a few
  hundred tokens; loading it costs the session.
- **Split on the PDF outline, not on headings.** Bookmarks give exact topic
  boundaries and real page numbers, so every answer can cite a page a human can
  open.
- **`pypdf`, not `pdftotext -layout`.** The latter renders the "fi"/"ff"
  ligatures as form-feed bytes and silently corrupts words — `filter` arrives as
  `<FF>ilter`.
- **Parameters get their own table.** "What are the Compressor's controls" is a
  different question from "find me a topic about compression", and only the
  first one can be answered exhaustively.

## Extending it

The harvester is Logic-specific in its heading heuristics but not in shape. For
another vendor's documentation the parts to change are: the source list, the
running-header stripper, and the bullet grammar for parameters. The record
schema and both CLIs are reusable as-is.
