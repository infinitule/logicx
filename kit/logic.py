#!/usr/bin/env python3
"""Drive Logic Pro from the shell via its own menu tree. No dependencies.

Logic ships no AppleScript dictionary and its key-command file (`Default.logikcs`)
stores numeric command IDs with no names, so a hardcoded command list would be a
guess. This reads the menus out of the running app instead: whatever this Logic
version can do, `menus` finds and `do` invokes, by name.

    python3 logic.py status                       # running? which windows are open?
    python3 logic.py menus                        # top-level menus
    python3 logic.py menus File                   # items in one menu
    python3 logic.py find bounce                  # search the whole menu tree
    python3 logic.py do File Bounce "Project or Section…"
    python3 logic.py key b --mods cmd             # key command
    python3 logic.py windows                      # open window names

The `mixer` family reads and sets channel-strip state through the Mixer
window's accessibility tree — no screenshots, no keystrokes, only AX actions,
clicks and value reads. Strips are addressed by track name or 1-based index.

    python3 logic.py mixer window                 # which Mixer window is in use
    python3 logic.py mixer strips                 # table: every strip, one AX call
    python3 logic.py mixer get "Audio 3" [field]  # one strip, all fields or one
    python3 logic.py mixer set "Audio 3" volume -6.0   # closed loop on the dB readout
    python3 logic.py mixer set "Audio 3" pan -20       # closed loop on the pan slider
    python3 logic.py mixer mute "Audio 3" on|off|toggle
    python3 logic.py mixer solo "Audio 3" on|off|toggle
    python3 logic.py mixer rec "Audio 3" on --yes      # arming is destructive-class
    python3 logic.py mixer monitor "Audio 3" on|off|toggle
    python3 logic.py mixer auto "Audio 3" [read|touch|latch|write|trim|off]
    python3 logic.py mixer output "Audio 3" ["Bus 1"]
    python3 logic.py mixer peak [strip] [--watch N]    # peak meter value(s)
    python3 logic.py mixer tree [strip]                # raw AX subtree, for discovery

Every mixer setter re-reads the control and reports before/after; a change that
does not read back is reported as a failure, never as success. Faders: Logic
treats `set value` on the fader as a 1-unit nudge (≈0.1 dB) and AXIncrement /
AXDecrement as ±10 units (≈1 dB), and the dB text lives in the NAME of the
"volume fader level" text field — so `set volume` steps and re-reads the text
until the target is reached instead of computing dB from the raw value.

Menu items that destroy work (Delete, Clear, Replace, Erase, Remove) require --yes.

First run triggers a macOS Automation permission prompt for System Events. If it
is denied, everything here fails with a -1743 error until it is granted in
System Settings > Privacy & Security > Automation.
"""

import argparse
import json
import re
import subprocess
import sys
import time

APP = "Logic Pro"
DESTRUCTIVE = re.compile(r"\b(delete|clear|erase|remove|replace|reset|discard)\b", re.I)


def osascript(script):
    """Run AppleScript, returning stdout. Explains the common TCC failures."""
    result = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, timeout=90
    )
    if result.returncode != 0:
        err = result.stderr.strip()
        if "-1743" in err or "not allowed assistive" in err:
            sys.exit(
                "macOS blocked UI scripting.\n"
                "Grant it in System Settings > Privacy & Security > Automation,\n"
                f"enabling System Events for the app running this script, then retry.\n\n{err}"
            )
        if "-600" in err or "not running" in err:
            sys.exit(f"{APP} is not running. Start it first: open -a '{APP}'\n\n{err}")
        sys.exit(f"AppleScript failed:\n{err}")
    return result.stdout.strip()


def running():
    out = subprocess.run(["pgrep", "-x", APP], capture_output=True, text=True)
    return bool(out.stdout.strip())


def require_running():
    if not running():
        sys.exit(f"{APP} is not running. Start it first:  open -a '{APP}'")


def applescript_string(value):
    """Quote a Python string for embedding in AppleScript source."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def frontmost_app():
    """Name of the frontmost application process ("" if it cannot be read)."""
    try:
        return osascript(
            'tell application "System Events" to get name of first application process whose frontmost is true'
        )
    except SystemExit:
        return ""


def set_front(app_name):
    """Bring an app forward through System Events (no extra Automation permission needed)."""
    subprocess.run(
        ["osascript", "-e",
         f'tell application "System Events" to set frontmost of process {applescript_string(app_name)} to true'],
        capture_output=True, text=True, timeout=15,
    )


class logic_front:
    """Logic only exposes its windows and menu bar to accessibility while it is the
    frontmost app. Bring it forward for one operation, then give focus back to
    whatever had it, so this coexists with other work on the Mac (the focus
    flicker is the cost of the accessibility surface; the MCU surface needs none).
    """

    def __enter__(self):
        self.prev = frontmost_app()
        if self.prev != APP:
            set_front(APP)
            # The window list populates a moment after activation; wait for it.
            for _ in range(8):
                time.sleep(0.25)
                try:
                    n = osascript(
                        f'tell application "System Events" to tell process {applescript_string(APP)} '
                        'to get count of windows'
                    )
                except SystemExit:
                    n = "0"
                if n.strip() not in ("", "0"):
                    break
        return self

    def __exit__(self, *exc):
        if self.prev and self.prev != APP:
            set_front(self.prev)
        return False


def name_list(what):
    """Names of some UI element class, one per line.

    AppleScript's default list output is comma-separated, which is ambiguous the
    moment a project or menu name contains a comma — so the delimiter is forced
    to a linefeed instead.
    """
    script = f'''
    tell application "System Events" to tell process {applescript_string(APP)}
        set out to name of every {what}
    end tell
    set text item delimiters to linefeed
    return out as text'''
    with logic_front():
        out = osascript(script)
    return [line.strip() for line in out.splitlines() if line.strip()]


def top_menus():
    return name_list("menu bar item of menu bar 1")


def menu_items(menu):
    """Item names in one top-level menu, with submenu children as 'Parent > Child'."""
    script = f'''
    set out to {{}}
    tell application "System Events" to tell process {applescript_string(APP)}
        tell menu bar 1 to tell menu bar item {applescript_string(menu)} to tell menu 1
            repeat with anItem in every menu item
                set nm to name of anItem
                if nm is not missing value then
                    set end of out to nm
                    if (count of menus of anItem) > 0 then
                        repeat with subItem in every menu item of menu 1 of anItem
                            set sn to name of subItem
                            if sn is not missing value then set end of out to nm & " > " & sn
                        end repeat
                    end if
                end if
            end repeat
        end tell
    end tell
    set text item delimiters to linefeed
    return out as text'''
    with logic_front():
        out = osascript(script)
    return [line.strip() for line in out.splitlines() if line.strip()]


def click_path(path):
    """Invoke a menu item, 2 or 3 levels deep: menu, item[, subitem]."""
    quoted = [applescript_string(part) for part in path]
    if len(quoted) == 2:
        target = f"menu item {quoted[1]} of menu 1 of menu bar item {quoted[0]}"
    elif len(quoted) == 3:
        target = (
            f"menu item {quoted[2]} of menu 1 of menu item {quoted[1]} "
            f"of menu 1 of menu bar item {quoted[0]}"
        )
    else:
        sys.exit("give a menu path of 2 or 3 parts, e.g.  do File Bounce 'Project or Section…'")

    script = f'''
    tell application "System Events" to tell process {applescript_string(APP)}
        set frontmost to true
        tell menu bar 1 to click {target}
    end tell'''
    with logic_front():
        osascript(script)


def send_key(key, mods):
    """Send a key command. mods: cmd, shift, option/alt, control/ctrl."""
    names = {
        "cmd": "command down",
        "command": "command down",
        "shift": "shift down",
        "opt": "option down",
        "option": "option down",
        "alt": "option down",
        "ctrl": "control down",
        "control": "control down",
    }
    using = ", ".join(names[m.strip().lower()] for m in mods if m.strip())
    clause = f" using {{{using}}}" if using else ""

    special = {
        "space": 49, "return": 36, "enter": 76, "tab": 48, "escape": 53, "esc": 53,
        "delete": 51, "left": 123, "right": 124, "down": 125, "up": 126,
    }
    if key.lower() in special:
        action = f"key code {special[key.lower()]}{clause}"
    else:
        action = f"keystroke {applescript_string(key)}{clause}"

    with logic_front():
        osascript(f'''
    tell application "System Events" to tell process {applescript_string(APP)}
        set frontmost to true
        {action}
    end tell''')


def windows():
    return name_list("window")


# ---------------------------------------------------------------------------
# Mixer: channel strips through the Mixer window's accessibility tree
# ---------------------------------------------------------------------------
#
# Verified on Logic Pro 12.3. The Mixer window holds one AXLayoutArea
# (description "Mixer") whose children are AXLayoutItems, one per channel
# strip, described by the track name. System Events has no `layout area` /
# `layout item` classes, so everything below addresses them as `UI element …
# whose role is …` / `whose description is …`.
#
# Pitfalls this code works around (each one was hit live):
#   - `name of x as text` inside a tell block fails with -1700; always `(get name of x)`.
#   - `set text item delimiters` inside `tell process` fails with -10006; joins
#     happen in handlers that run outside the tell block.
#   - `before`, `after`, `line` are AppleScript reserved words.
#   - The dB readout is the NAME of the "volume fader level" text field, so a
#     by-name specifier for it goes stale the moment the fader moves; it is
#     re-resolved by description on every read.
#   - `whose` filters cost ~0.5 s each; reading a strip fetches one attribute of
#     every child at once (`get value of every UI element of s`) instead.
#   - `set value` on the fader/pan slider is a 1-unit nudge, not an absolute set.

MIXER_HINT = 'No Mixer window is open.  Open one:  python3 logic.py do Window "Open Mixer"'
MISSING = "<missing>"   # sentinel for `missing value`; cannot collide with a real readout
FIELD_SEP = "\x1f"      # between description / name / value of one child
CHILD_SEP = "\t"        # between children of one strip (tabs cannot be typed into track names)

AUTO_MODES = ("read", "touch", "latch", "write", "trim", "off")

# Children whose description is fixed. Anything else sitting right after
# "group" is the output button (its description IS the current output), and
# anything else right after "channel mode" is the input button.
# Logic 12.3.1 sometimes reports identifier-style descriptions ("volumeFader",
# "peak", "channelMode") instead of the human-readable ones; both are seen on the
# same machine in the same day. Canonicalise to the readable names.
DESC_ALIASES = {
    "volumeFader": "volume fader", "volume": "volume fader level", "peak": "peak level meter",
    "channelMode": "channel mode", "sendButton": "send button", "audioPlugIn": "audio plug-in",
    "equalizerThumbnail": "EQ", "gainReductionMeter": "gain reduction meter",
    "inputGainDisplay": "input gain", "inputGainKnob": "input gain knob",
}

FIXED_DESCS = {
    "name", "mute", "solo", "record", "monitoring", "volume fader",
    "volume fader level", "peak level meter", "pan", "group", "send button",
    "audio plug-in", "channel mode", "EQ", "gain reduction meter", "setting",
    "input gain", "bounce", "mastering assistant", "dim",
}

AS_HANDLERS = '''
on txt(x)
    if x is missing value then return "<missing>"
    try
        return x as text
    on error
        return "<missing>"
    end try
end txt

on joinWith(lst, sep)
    set AppleScript's text item delimiters to sep
    set r to lst as text
    set AppleScript's text item delimiters to ""
    return r
end joinWith

on parseDb(s)
    -- "volume fader level, -5.2 dB" -> -5.2 ; the bottom of the fader reads as infinity -> -999
    if s contains (character id 8734) or s contains "inf" then return -999
    set AppleScript's text item delimiters to ", "
    set parts to text items of s
    set AppleScript's text item delimiters to " "
    set numTxt to item 1 of (text items of (item -1 of parts))
    set AppleScript's text item delimiters to ""
    return numTxt as real
end parseDb

on levelOf(s)
    tell application "System Events" to tell process "Logic Pro"
        return my parseDb(get name of (first UI element of s whose (description is "volume fader level" or description is "volume")))
    end tell
end levelOf

on encodeStrip(s)
    -- one line: desc<US>name<US>value<TAB>desc<US>name<US>value ... for every child
    tell application "System Events" to tell process "Logic Pro"
        set ds to (get description of every UI element of s)
        set ns to (get name of every UI element of s)
        set vs to (get value of every UI element of s)
    end tell
    set recs to {}
    repeat with i from 1 to (count of ds)
        set end of recs to my txt(item i of ds) & (character id 31) & my txt(item i of ns) & (character id 31) & my txt(item i of vs)
    end repeat
    return my joinWith(recs, tab)
end encodeStrip

on seekRaw(fader, target)
    -- move a slider to an exact raw value: AXIncrement/AXDecrement are ±10 units, `set value` is a ±1 nudge
    tell application "System Events" to tell process "Logic Pro"
        set n to 0
        repeat while n < 80
            set v to (get value of fader)
            set d to target - v
            if d is 0 then exit repeat
            if d ≥ 10 then
                perform action "AXIncrement" of fader
            else if d ≤ -10 then
                perform action "AXDecrement" of fader
            else if d > 0 then
                set value of fader to (v + 1)
            else
                set value of fader to (v - 1)
            end if
            delay 0.05
            set n to n + 1
        end repeat
        return (get value of fader)
    end tell
end seekRaw

on dumpKids(e, indent)
    set out to {}
    tell application "System Events" to tell process "Logic Pro"
        set kids to every UI element of e
        set n to count of kids
        if n is 0 then return {}
        set rs to (get role of every UI element of e)
        set ds to (get description of every UI element of e)
        set ns to (get name of every UI element of e)
        set vs to (get value of every UI element of e)
    end tell
    repeat with i from 1 to n
        set end of out to indent & (item i of rs) & " | " & my txt(item i of ds) & " | " & my txt(item i of ns) & " | " & my txt(item i of vs)
        set out to out & my dumpKids(item i of kids, indent & "    ")
    end repeat
    return out
end dumpKids
'''


# Setters refuse to run while Logic shows a modal dialog: AX actions still reach
# the model (the fader's raw value moves) but the Mixer does not redraw, so the
# dB readout and the button states cannot be read back — and a change that
# cannot be read back must not be reported.
AS_MODAL_GUARD = '''
        set modals to {}
        repeat with win in windows
            try
                if ((get value of attribute "AXModal" of win) is true) and ((get subrole of win) is not "AXStandardWindow") then
                    set t to my txt(get description of win)
                    repeat with stx in (static texts of win)
                        set t to t & " / " & my txt(get value of stx)
                    end repeat
                    set end of modals to t
                end if
            end try
        end repeat
        if (count of modals) > 0 then return "ERR:MODAL" & tab & my joinWith(modals, " || ")'''


# Logic applies a mute/solo/fader edit to EVERY selected channel strip (fader
# moves are relative across the selection). A setter must therefore act on a
# strip that is not part of a multi-selection: raise the Mixer and deselect all
# first. (Verified 2026-08-23: one mute click muted all eight strips until this.)
AS_ISOLATE = '''
        perform action "AXRaise" of w
        delay 0.15
        try
            click menu item "Deselect All" of menu 1 of menu bar item "Edit" of menu bar 1
            delay 0.15
        end try'''


def mixer_script(body, strip=None, guard=False):
    """Wrap `body` in a tell block with `w`, `mixerArea` and (optionally) `strip` resolved.

    The body runs inside `tell application "System Events" to tell process
    "Logic Pro"`, so it can use `my` handlers from AS_HANDLERS and must `return`
    its result as text. Sentinel results "ERR:NOMIXER" / "ERR:NOSTRIP" /
    "ERR:MODAL" are turned into explained failures by run_mixer(). `guard`
    adds the modal-dialog refusal that every setter uses.
    """
    resolve = (AS_MODAL_GUARD + AS_ISOLATE) if guard else ""
    if strip is not None:
        if isinstance(strip, int):
            selector = f'item {strip} of (UI elements of mixerArea whose role is "AXLayoutItem")'
        else:
            selector = f"first UI element of mixerArea whose description is {applescript_string(strip)}"
        resolve += f'''
        set strip to missing value
        try
            set strip to {selector}
        end try
        if strip is missing value then return "ERR:NOSTRIP"'''
    return f'''{AS_HANDLERS}
    tell application "System Events" to tell process {applescript_string(APP)}
        -- Logic exposes its windows only while frontmost, and focus can change under us
        -- (another app activating) between two statements: assert it, then retry the lookup.
        set frontmost to true
        set w to missing value
        repeat 10 times
            try
                set w to first window whose name contains "Mixer"
                set mixerArea to first UI element of group 1 of w whose role is "AXLayoutArea"
                exit repeat
            on error
                set w to missing value
                delay 0.2
                set frontmost to true
            end try
        end repeat
        if w is missing value then
            -- Logic exposes only its key window to accessibility at times; raise the
            -- Mixer through Logic's own scripting interface, then look again.
            try
                tell application "Logic Pro"
                    set mw to first window whose name contains "Mixer"
                    set index of mw to 1
                end tell
            end try
            delay 0.6
            repeat 5 times
                try
                    set w to first window whose name contains "Mixer"
                    set mixerArea to first UI element of group 1 of w whose role is "AXLayoutArea"
                    exit repeat
                on error
                    set w to missing value
                    delay 0.3
                end try
            end repeat
        end if
        if w is missing value then
            -- No Mixer window: use the Mixer pane docked in the main window, if shown.
            try
                set tw to first window whose name ends with "- Tracks"
                set mixerArea to first UI element of (last group of tw) whose role is "AXLayoutArea"
                if (get description of mixerArea) is "Mixer" then set w to tw
            end try
        end if
        if w is missing value then return "ERR:NOMIXER"
        {resolve}
        {body}
    end tell'''


def strip_key(strip):
    """'3' -> 3 (1-based index), anything else -> the track name as given."""
    if strip is None:
        return None
    s = str(strip).strip()
    if s.isdigit() and int(s) > 0:
        return int(s)
    return s


def run_mixer(body, strip=None, guard=False):
    with logic_front():
        out = osascript(mixer_script(body, strip_key(strip), guard=guard))
    if out == "ERR:NOMIXER":
        sys.exit(MIXER_HINT)
    if out.startswith("ERR:MODAL"):
        _, _, dialogs = out.partition("\t")
        sys.exit(
            f"{APP} is showing a modal dialog: {dialogs}\n"
            "Nothing was changed. While it is open the Mixer does not redraw, so a fader or button\n"
            "change could not be read back; dismiss the dialog in Logic and retry."
        )
    if out == "ERR:NOSTRIP":
        names = strip_names()
        sys.exit(
            f"no strip {strip!r} in the Mixer. Strips (by name or 1-based index):\n  "
            + "\n  ".join(f"{i}. {n}" for i, n in enumerate(names, 1))
        )
    return out


def mixer_window():
    """Name of the Mixer window in use, or exit with the open-mixer hint."""
    script = f'''
    tell application "System Events" to tell process {applescript_string(APP)}
        set frontmost to true
        repeat 10 times
            set ws to windows whose name contains "Mixer"
            if (count of ws) > 0 then return (get name of item 1 of ws)
            delay 0.2
        end repeat
        return "ERR:NOMIXER"
    end tell'''
    with logic_front():
        out = osascript(script)
    if out == "ERR:NOMIXER":
        sys.exit(MIXER_HINT)
    return out


def modal_dialogs():
    """Descriptions + text of any modal dialog Logic is showing (empty list when none)."""
    with logic_front():
        out = osascript(mixer_script('return "OK"', guard=True))
    if out.startswith("ERR:MODAL"):
        return out.partition("\t")[2].split(" || ")
    return []


def strip_names():
    out = run_mixer('''
        set names to {}
        repeat with s in (UI elements of mixerArea whose role is "AXLayoutItem")
            set end of names to (get description of s)
        end repeat
        return my joinWith(names, linefeed)''')
    return [n for n in out.splitlines() if n]


def parse_db(text):
    """'volume fader level, -5.2 dB' -> -5.2 ; '-∞ dB' -> -inf ; unknown -> None."""
    if text is None:
        return None
    if "∞" in text or "inf" in text.lower():
        return float("-inf")
    m = re.search(r"(-?\+?\d+(?:\.\d+)?)\s*dB", text)
    return float(m.group(1)) if m else None


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _onoff(value):
    return value if value in ("on", "off") else None


def decode_strip(line):
    """Turn one encodeStrip() line into a dict of named fields."""
    children = []
    for rec in line.split(CHILD_SEP):
        parts = rec.split(FIELD_SEP)
        if len(parts) != 3:
            continue
        desc, name, value = (None if p == MISSING else p for p in parts)
        desc = DESC_ALIASES.get(desc, desc)
        children.append((desc, name, value))

    by_desc = {}
    for desc, name, value in children:
        if desc is not None and desc not in by_desc:
            by_desc[desc] = (name, value)

    def val(desc):
        return by_desc.get(desc, (None, None))[1]

    def nam(desc):
        return by_desc.get(desc, (None, None))[0]

    # Positional: the output button follows "group", the input button follows "channel mode".
    output = input_ = None
    auto_desc = None
    for i, (desc, name, value) in enumerate(children):
        if desc and ", automation " in desc:
            auto_desc = desc
        if desc not in FIXED_DESCS and desc is not None and i > 0:
            prev = children[i - 1][0]
            if prev == "group":
                output = desc
            elif prev == "channel mode":
                input_ = desc

    auto = auto_enabled = None
    if auto_desc:
        mode, _, rest = auto_desc.partition(", automation ")
        auto = mode
        auto_enabled = rest.strip() == "enabled"

    raw = _num(val("volume fader"))
    return {
        "name": val("name"),
        "db": parse_db(nam("volume fader level")),
        "raw": raw,
        "pan": (int(_num(val("pan"))) if _num(val("pan")) is not None else None),
        "mute": _onoff(val("mute")),
        "solo": _onoff(val("solo")),
        "rec": _onoff(val("record")),
        "mon": _onoff(val("monitoring")),
        "auto": auto,
        "auto_enabled": auto_enabled,
        "output": output,
        "input": input_,
        "mode": val("channel mode"),
        "peak": val("peak level meter"),
        "peak_db": parse_db(nam("peak level meter")) if nam("peak level meter") else None,
        "eq": _onoff(val("EQ")),
        "gain_reduction": val("gain reduction meter"),
        "input_gain": _num(val("input gain")),
        "group": val("group"),
    }


def read_strips():
    """Every strip's fields, read in ONE osascript call."""
    out = run_mixer('''
        set lines_ to {}
        repeat with s in (UI elements of mixerArea whose role is "AXLayoutItem")
            set end of lines_ to my encodeStrip(s)
        end repeat
        return my joinWith(lines_, linefeed)''')
    return [decode_strip(line) for line in out.splitlines() if line]


def read_strip(strip):
    out = run_mixer("return my encodeStrip(strip)", strip)
    return decode_strip(out)


def fmt_db(db):
    if db is None:
        return "-"
    if db == float("-inf"):
        return "-inf"
    return f"{db:.1f}"


def jsonable(info):
    """-inf is not JSON; emit it as the string "-inf"."""
    if isinstance(info, list):
        return [jsonable(i) for i in info]
    if isinstance(info, dict):
        return {k: jsonable(v) for k, v in info.items()}
    if isinstance(info, float) and info == float("-inf"):
        return "-inf"
    return info


STRIP_COLUMNS = ("name", "db", "raw", "pan", "mute", "solo", "rec", "mon", "auto", "output", "mode", "peak")


def print_strips_table(strips):
    headers = ("strip", "dB", "raw", "pan", "mute", "solo", "rec", "mon", "auto", "output", "mode", "peak")
    rows = []
    for s in strips:
        row = []
        for col in STRIP_COLUMNS:
            v = s.get(col)
            if col == "db":
                row.append(fmt_db(v))
            elif col == "auto" and v is not None and s.get("auto_enabled") is False:
                row.append(f"{v} (disabled)")
            else:
                row.append("-" if v is None else str(v))
        rows.append(row)
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    for r in rows:
        print("  ".join(c.ljust(widths[i]) for i, c in enumerate(r)))


def set_volume(strip, target, max_steps=120):
    """Closed loop on the dB readout. Returns dict(before, after, steps, raw, target).

    |diff| >= 0.95 dB: AXIncrement / AXDecrement (≈1 dB). Smaller: `set value`
    nudge (≈0.1 dB). Re-read the dB text after every step.
    """
    as_target = -999 if target == float("-inf") else target
    out = run_mixer(f'''
        set target to {as_target}
        set fader to first UI element of strip whose (description is "volume fader" or description is "volumeFader")
        set cur to my levelOf(strip)
        set startDb to cur
        set rawStart to (get value of fader)
        set lastRaw to rawStart
        set staleHits to 0
        set stale to false
        set steps to 0
        repeat while steps < {int(max_steps)}
            if target is -999 then
                if cur is -999 then exit repeat
                set diff to -100
            else if cur is -999 then
                set diff to 100
            else
                set diff to target - cur
                if diff < 0.05 and diff > -0.05 then exit repeat
            end if
            if diff ≥ 0.95 then
                perform action "AXIncrement" of fader
            else if diff ≤ -0.95 then
                perform action "AXDecrement" of fader
            else
                set v to (get value of fader)
                if diff > 0 then
                    set value of fader to (v + 1)
                else
                    set value of fader to (v - 1)
                end if
            end if
            delay 0.05
            set steps to steps + 1
            set newRaw to (get value of fader)
            set newCur to my levelOf(strip)
            -- the fader moved a full step twice but the readout never changed: Logic is not
            -- redrawing (modal dialog). Stop, and put the fader back where it started, by raw units.
            if (newRaw - lastRaw ≥ 10 or lastRaw - newRaw ≥ 10) and newCur is cur then
                set staleHits to staleHits + 1
                if staleHits ≥ 2 then
                    set stale to true
                    exit repeat
                end if
            else
                set staleHits to 0
            end if
            set lastRaw to newRaw
            set cur to newCur
        end repeat
        if stale then my seekRaw(fader, rawStart)
        return (startDb as text) & tab & (cur as text) & tab & (steps as text) & tab & ((get value of fader) as text) & tab & (stale as text)''', strip, guard=True)
    b, a, steps, raw, stale = out.split("\t")
    conv = lambda x: float("-inf") if float(x) == -999 else float(x)
    if stale == "true":
        sys.exit(
            f"the fader moved but the dB readout never changed — {APP} is not redrawing the Mixer "
            f"(a modal dialog?). Stopped after {steps} steps and put the fader back at raw {float(raw):.0f}, "
            f"where it started ({fmt_db(conv(b))} dB)."
        )
    return {"before": conv(b), "after": conv(a), "steps": int(steps), "raw": float(raw), "target": target}


def set_pan(strip, target, max_steps=120):
    """Closed loop on the pan slider value (-64..63). ±10 via AX actions, ±1 via nudge."""
    out = run_mixer(f'''
        set target to {int(target)}
        set pan to first UI element of strip whose description is "pan"
        set cur to (get value of pan)
        set startPan to cur
        set steps to 0
        repeat while steps < {int(max_steps)}
            set diff to target - cur
            if diff is 0 then exit repeat
            if diff ≥ 10 then
                perform action "AXIncrement" of pan
            else if diff ≤ -10 then
                perform action "AXDecrement" of pan
            else if diff > 0 then
                set value of pan to (cur + 1)
            else
                set value of pan to (cur - 1)
            end if
            delay 0.05
            set steps to steps + 1
            set cur to (get value of pan)
        end repeat
        return (startPan as text) & tab & (cur as text) & tab & (steps as text)''', strip, guard=True)
    b, a, steps = out.split("\t")
    return {"before": int(float(b)), "after": int(float(a)), "steps": int(steps), "target": int(target)}


TOGGLES = {"mute": "mute", "solo": "solo", "rec": "record", "monitor": "monitoring"}


def set_toggle(strip, desc, want):
    """on / off / toggle a strip button by clicking it only when needed. Returns (before, after)."""
    out = run_mixer(f'''
        set b to first UI element of strip whose description is {applescript_string(desc)}
        set v0 to (get value of b)
        set want to {applescript_string(want)}
        if want is "toggle" or (v0 is not want) then
            click b
            delay 0.15
        end if
        set v1 to (get value of (first UI element of strip whose description is {applescript_string(desc)}))
        return (v0 as text) & tab & (v1 as text)''', strip, guard=True)
    b, a = out.split("\t")
    return b, a


def read_peak(strip=None):
    """Peak meter value(s): {strip: value}."""
    if strip is not None:
        out = run_mixer('''
            return (get description of strip) & tab & my txt(get value of (first UI element of strip whose (description is "peak level meter" or description is "peak")))''', strip)
        name, value = out.split("\t")
        return {name: None if value == MISSING else value}
    return {s["name"]: s["peak"] for s in read_strips() if s["peak"] is not None}


def dump_tree(strip=None):
    if strip is None:
        body = '''
        set lines_ to {"AXLayoutArea | " & (get description of mixerArea) & " | " & my txt(get name of mixerArea) & " | " & my txt(get value of mixerArea)}
        set lines_ to lines_ & my dumpKids(mixerArea, "    ")
        return my joinWith(lines_, linefeed)'''
    else:
        body = '''
        set lines_ to {"AXLayoutItem | " & (get description of strip) & " | " & my txt(get name of strip) & " | " & my txt(get value of strip)}
        set lines_ to lines_ & my dumpKids(strip, "    ")
        return my joinWith(lines_, linefeed)'''
    return run_mixer(body, strip)


# --- pop-up menus (automation mode, output) --------------------------------
#
# The automation `list` button and the output button open Logic's own pop-up
# menus. AXPress on them does nothing observable; a synthesized mouse click at
# the button's centre is needed, and the menu must be found and read before it
# is dismissed with Escape — the one keystroke this file sends, and only once a
# menu is confirmed open.

def popup_button_selector(kind):
    if kind == "auto":
        return 'first UI element of (first UI element of strip whose role is "AXGroup") whose description is "list"'
    if kind == "output":
        # the child right after "group" is the output button, whatever its description
        return '''(my childAfter(strip, "group"))'''
    raise ValueError(kind)


AS_POPUP_HANDLERS = '''
on childAfter(s, afterDesc)
    tell application "System Events" to tell process "Logic Pro"
        set ds to (get description of every UI element of s)
        repeat with i from 1 to ((count of ds) - 1)
            if item i of ds is afterDesc then return UI element (i + 1) of s
        end repeat
    end tell
    error "no child after " & afterDesc
end childAfter

on findPopup(baseWindows)
    -- a pop-up shows up either as an AXMenu of the process or as an extra window
    tell application "System Events" to tell process "Logic Pro"
        if (count of menus) > 0 then return menu 1
        if (count of windows) > baseWindows then
            repeat with win in windows
                if (get subrole of win) is not "AXStandardWindow" and (get description of win) is not "alert" then return win
            end repeat
        end if
    end tell
    return missing value
end findPopup

on popupItems(p)
    tell application "System Events" to tell process "Logic Pro"
        set names to {}
        repeat with e in (every UI element of p)
            set r to (get role of e)
            if r is "AXMenuItem" or r is "AXButton" or r is "AXRadioButton" or r is "AXStaticText" or r is "AXCheckBox" then
                set end of names to r & (character id 31) & my txt(get name of e) & (character id 31) & my txt(get description of e) & (character id 31) & my txt(get value of e)
            else
                set end of names to r & (character id 31) & my txt(get name of e) & (character id 31) & my txt(get description of e) & (character id 31) & my txt(get value of e)
                repeat with c in (every UI element of e)
                    set end of names to (get role of c) & (character id 31) & my txt(get name of c) & (character id 31) & my txt(get description of c) & (character id 31) & my txt(get value of c)
                end repeat
            end if
        end repeat
        return names
    end tell
end popupItems
'''


def popup_explore(strip, kind, pick=None):
    """Open a strip pop-up by clicking its button, list the items, optionally pick one, then close it.

    Returns dict(items=[...], picked=name|None, opened=bool). Never leaves a menu
    open: Escape is sent only after the menu was found, and re-checked after.
    """
    selector = popup_button_selector(kind)
    pick_clause = ""
    if pick is not None:
        pick_clause = f'''
            set wanted to {applescript_string(pick)}
            set picked to ""
            repeat with e in (every UI element of p)
                set nm to my txt(get name of e)
                if nm is wanted then
                    click e
                    set picked to nm
                    exit repeat
                end if
                repeat with c in (every UI element of e)
                    set cn to my txt(get name of c)
                    if cn is wanted then
                        click c
                        set picked to cn
                        exit repeat
                    end if
                end repeat
                if picked is not "" then exit repeat
            end repeat
            delay 0.3'''
    body = f'''
        set baseWindows to (count of windows)
        set btn to {selector}
        set p0 to (get position of btn)
        set s0 to (get size of btn)
        set cx to (item 1 of p0) + ((item 1 of s0) div 2)
        set cy to (item 2 of p0) + ((item 2 of s0) div 2)
        click at {{cx, cy}}
        delay 0.4
        set p to my findPopup(baseWindows)
        if p is missing value then return "ERR:NOPOPUP"
        set items_ to my popupItems(p)
        set picked to ""
        {pick_clause}
        set stillOpen to (my findPopup(baseWindows)) is not missing value
        if stillOpen then
            key code 53
            delay 0.3
        end if
        set closed to (my findPopup(baseWindows)) is missing value
        return "OK" & tab & picked & tab & (closed as text) & linefeed & my joinWith(items_, linefeed)'''
    out = osascript(AS_POPUP_HANDLERS + mixer_script(body, strip_key(strip)))
    if out == "ERR:NOMIXER":
        sys.exit(MIXER_HINT)
    if out == "ERR:NOSTRIP":
        sys.exit(f"no strip {strip!r} in the Mixer. Strips: " + ", ".join(strip_names()))
    if out == "ERR:NOPOPUP":
        return {"opened": False, "items": [], "picked": None, "closed": True}
    head, _, rest = out.partition("\n")
    _, picked, closed = head.split("\t")
    items = []
    for line in rest.splitlines():
        parts = line.split(FIELD_SEP)
        if len(parts) == 4:
            items.append({k: (None if v == MISSING else v) for k, v in zip(("role", "name", "desc", "value"), parts)})
    return {"opened": True, "items": items, "picked": picked or None, "closed": closed == "true"}


def mixer_main(args):
    """Dispatch for `logic.py mixer …`."""
    cmd = args.mixer_command
    as_json = args.json

    def emit(info, text):
        if as_json:
            print(json.dumps(jsonable(info), indent=2))
        else:
            print(text)

    if cmd == "window":
        name = mixer_window()
        emit({"window": name}, name)
        return 0

    if cmd == "strips":
        strips = read_strips()
        if as_json:
            print(json.dumps(jsonable(strips), indent=2))
        else:
            print_strips_table(strips)
        return 0

    if cmd == "get":
        info = read_strip(args.strip)
        if args.field:
            if args.field not in info:
                sys.exit(f"unknown field {args.field!r}. Fields: " + ", ".join(info))
            value = info[args.field]
            emit({args.field: value}, fmt_db(value) if args.field == "db" else ("-" if value is None else str(value)))
        else:
            if as_json:
                print(json.dumps(jsonable(info), indent=2))
            else:
                for k, v in info.items():
                    print(f"{k:15} {fmt_db(v) if k == 'db' else ('-' if v is None else v)}")
        return 0

    if cmd == "tree":
        print(dump_tree(args.strip))
        return 0

    if cmd == "peak":
        readings = []
        polls = max(1, args.watch or 1)
        for i in range(polls):
            if i:
                time.sleep(args.interval)
            values = read_peak(args.strip)
            readings.append(values)
            if not as_json:
                stamp = f"[{i + 1}/{polls}] " if polls > 1 else ""
                print(stamp + "  ".join(f"{k}: {v}" for k, v in values.items()))
        if as_json:
            print(json.dumps(readings if polls > 1 else readings[0], indent=2))
        return 0

    if cmd == "set":
        param = args.param.lower()
        if param == "volume":
            target = float("-inf") if args.value.lower() in ("-inf", "-infinity", "inf") else float(args.value)
            if target != float("-inf") and not -100 <= target <= 6.0:
                sys.exit("volume must be between -inf and +6.0 dB")
            t0 = time.time()
            r = set_volume(args.strip, target)
            r["seconds"] = round(time.time() - t0, 2)
            ok = r["after"] == target if target == float("-inf") else abs(r["after"] - target) <= 0.051
            r["ok"] = ok
            r["strip"] = args.strip
            r["param"] = "volume"
            emit(r, f"{args.strip} volume: {fmt_db(r['before'])} dB -> {fmt_db(r['after'])} dB "
                    f"(target {fmt_db(target)}, {r['steps']} steps, raw {r['raw']:.0f}, {r['seconds']}s)")
            if not ok:
                sys.exit(f"did not reach {fmt_db(target)} dB: the fader reads {fmt_db(r['after'])} dB after "
                         f"{r['steps']} steps. If the steps count is 0 the AX actions were not applied "
                         f"(a modal dialog in Logic can block them).")
            return 0
        if param == "pan":
            target = int(float(args.value))
            if not -64 <= target <= 63:
                sys.exit("pan must be between -64 and +63 (0 = centre)")
            t0 = time.time()
            r = set_pan(args.strip, target)
            r["seconds"] = round(time.time() - t0, 2)
            r["ok"] = r["after"] == target
            r["strip"] = args.strip
            r["param"] = "pan"
            emit(r, f"{args.strip} pan: {r['before']:+d} -> {r['after']:+d} (target {target:+d}, {r['steps']} steps, {r['seconds']}s)")
            if not r["ok"]:
                sys.exit(f"did not reach pan {target:+d}: reads {r['after']:+d} after {r['steps']} steps.")
            return 0
        sys.exit(f"unknown parameter {args.param!r}: use volume or pan")

    if cmd in TOGGLES:
        want = args.state.lower()
        if want not in ("on", "off", "toggle"):
            sys.exit("state must be on, off or toggle")
        if cmd == "rec" and not args.yes:
            sys.exit("record-arming a track is destructive-class — pass --yes to confirm.")
        before, after = set_toggle(args.strip, TOGGLES[cmd], want)
        expected = want if want != "toggle" else ("off" if before == "on" else "on")
        ok = after == expected
        info = {"strip": args.strip, "control": cmd, "before": before, "after": after, "ok": ok}
        emit(info, f"{args.strip} {cmd}: {before} -> {after}")
        if not ok:
            sys.exit(f"{cmd} did not change: wanted {expected}, reads {after}. "
                     f"A modal dialog in Logic can swallow the click.")
        return 0

    if cmd == "auto":
        info = read_strip(args.strip)
        current = info["auto"]
        if not args.mode:
            emit({"strip": args.strip, "auto": current, "auto_enabled": info["auto_enabled"]},
                 f"{args.strip} automation: {current}" + ("" if info["auto_enabled"] else " (disabled)"))
            return 0
        mode = args.mode.lower()
        if mode not in AUTO_MODES:
            sys.exit(f"mode must be one of {', '.join(AUTO_MODES)}")
        if current and current.lower() == mode:
            emit({"strip": args.strip, "before": current, "after": current, "ok": True, "steps": 0},
                 f"{args.strip} automation already {current}")
            return 0
        result = popup_explore(args.strip, "auto", pick=None)
        names = [i["name"] for i in result["items"] if i["name"]]
        match = next((n for n in names if n.lower() == mode or n.lower().startswith(mode)), None)
        if not result["opened"] or match is None:
            sys.exit(
                "could not set the automation mode through the accessibility tree: "
                + ("the list button's pop-up menu did not appear (AXPress and a synthesized click both opened nothing"
                   " findable; a modal dialog in Logic also blocks synthesized clicks)." if not result["opened"]
                   else f"no menu item matches {mode!r}; items seen: {names}")
                + f"\n{args.strip} automation is still {current}."
            )
        picked = popup_explore(args.strip, "auto", pick=match)
        after = read_strip(args.strip)["auto"]
        ok = after and after.lower() == match.lower()
        emit({"strip": args.strip, "before": current, "after": after, "picked": picked["picked"], "ok": bool(ok)},
             f"{args.strip} automation: {current} -> {after}")
        if not ok:
            sys.exit(f"automation mode did not change to {match!r}; reads {after!r}.")
        return 0

    if cmd == "output":
        info = read_strip(args.strip)
        current = info["output"]
        if not args.target:
            emit({"strip": args.strip, "output": current}, f"{args.strip} output: {current or '-'}")
            return 0
        if current == args.target:
            emit({"strip": args.strip, "before": current, "after": current, "ok": True}, f"{args.strip} output already {current}")
            return 0
        result = popup_explore(args.strip, "output", pick=None)
        names = [i["name"] for i in result["items"] if i["name"]]
        if not result["opened"] or args.target not in names:
            sys.exit(
                "could not set the output through the accessibility tree: "
                + ("the output button's pop-up menu did not appear." if not result["opened"]
                   else f"no menu item named {args.target!r}; items seen: {names}")
                + f"\n{args.strip} output is still {current}."
            )
        picked = popup_explore(args.strip, "output", pick=args.target)
        after = read_strip(args.strip)["output"]
        ok = after == args.target
        emit({"strip": args.strip, "before": current, "after": after, "picked": picked["picked"], "ok": ok},
             f"{args.strip} output: {current} -> {after}")
        if not ok:
            sys.exit(f"output did not change to {args.target!r}; reads {after!r}.")
        return 0

    if cmd == "popup":
        result = popup_explore(args.strip, args.kind, pick=None)
        if as_json:
            print(json.dumps(result, indent=2))
        else:
            if not result["opened"]:
                print("no pop-up appeared")
            for i in result["items"]:
                print(f"{i['role']} | {i['name']} | {i['desc']} | {i['value']}")
            print(f"closed: {result['closed']}")
        return 0 if result["closed"] else 1

    sys.exit(f"unknown mixer command {cmd!r}")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")

    # --json is accepted on either side of the subcommand. SUPPRESS keeps the
    # subparser copy from overwriting a --json given before the subcommand with
    # its own default.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS,
        help="machine-readable output",
    )

    def with_common(**kw):
        return argparse.ArgumentParser(parents=[common], **kw)

    sub = parser.add_subparsers(dest="command", required=True, parser_class=with_common)

    sub.add_parser("status", help="is Logic running, and what windows are open")

    menus = sub.add_parser("menus", help="list menus, or the items in one menu")
    menus.add_argument("menu", nargs="?", help="a top-level menu name, e.g. File")

    find = sub.add_parser("find", help="search every menu for matching commands")
    find.add_argument("term")

    do = sub.add_parser("do", help="invoke a menu item by path")
    do.add_argument("path", nargs="+", help="Menu, Item[, Subitem]")
    do.add_argument("--yes", action="store_true", help="confirm a destructive command")

    key = sub.add_parser("key", help="send a key command")
    key.add_argument("key")
    key.add_argument("--mods", default="", help="comma-separated: cmd,shift,option,control")

    sub.add_parser("windows", help="list open window names")

    mixer = sub.add_parser("mixer", help="read and set channel strips in the Mixer window")
    msub = mixer.add_subparsers(dest="mixer_command", required=True, parser_class=with_common)

    msub.add_parser("window", help="which Mixer window is being used")
    msub.add_parser("strips", help="table of every strip (one AX call)")

    get = msub.add_parser("get", help="one strip: all fields, or one")
    get.add_argument("strip", help="track name or 1-based index")
    get.add_argument("field", nargs="?", help="db, raw, pan, mute, solo, rec, mon, auto, output, input, mode, peak, …")

    setp = msub.add_parser("set", help="closed-loop set of volume (dB) or pan (-64..63)")
    setp.add_argument("strip")
    setp.add_argument("param", help="volume | pan")
    setp.add_argument("value", help="dB for volume (or -inf), units for pan")

    for name, helptext in (
        ("mute", "mute on|off|toggle"),
        ("solo", "solo on|off|toggle"),
        ("rec", "record-arm on|off|toggle (needs --yes)"),
        ("monitor", "input monitoring on|off|toggle"),
    ):
        p = msub.add_parser(name, help=helptext)
        p.add_argument("strip")
        p.add_argument("state", help="on | off | toggle")
        p.add_argument("--yes", action="store_true", help="confirm a destructive-class change")

    auto = msub.add_parser("auto", help="read or set the automation mode")
    auto.add_argument("strip")
    auto.add_argument("mode", nargs="?", help="read | touch | latch | write | trim | off")

    output = msub.add_parser("output", help="read or set the strip's output")
    output.add_argument("strip")
    output.add_argument("target", nargs="?", help='e.g. "Bus 1"')

    peak = msub.add_parser("peak", help="peak level meter value(s)")
    peak.add_argument("strip", nargs="?")
    peak.add_argument("--watch", type=int, default=0, metavar="N", help="poll N times")
    peak.add_argument("--interval", type=float, default=0.5, help="seconds between polls")

    tree = msub.add_parser("tree", help="raw dump of a strip's AX subtree (or the whole Mixer area)")
    tree.add_argument("strip", nargs="?")

    popup = msub.add_parser("popup", help="open a strip pop-up (auto|output), list its items, close it")
    popup.add_argument("strip")
    popup.add_argument("kind", help="auto | output")

    args = parser.parse_args()

    if args.command == "status":
        up = running()
        info = {"running": up, "app": APP}
        if up:
            info["windows"] = windows()
        if args.json:
            print(json.dumps(info, indent=2))
        else:
            print(f"{APP}: {'running' if up else 'not running'}")
            if up:
                print("windows: " + ", ".join(info["windows"]))
        return 0 if up else 1

    require_running()

    if args.command == "menus":
        found = menu_items(args.menu) if args.menu else top_menus()
        print(json.dumps(found, indent=2) if args.json else "\n".join(found))

    elif args.command == "find":
        pattern = re.compile(re.escape(args.term), re.I)
        hits = []
        for menu in top_menus():
            for item in menu_items(menu):
                if pattern.search(item):
                    hits.append([menu] + [p.strip() for p in item.split(" > ")])
        if args.json:
            print(json.dumps(hits, indent=2))
        else:
            for hit in hits:
                quoted = " ".join(f'"{p}"' if " " in p else p for p in hit)
                print(f"{' > '.join(hit)}\n    python3 logic.py do {quoted}")
            print(f"\n{len(hits)} command(s) match {args.term!r}.")

    elif args.command == "do":
        if DESTRUCTIVE.search(" ".join(args.path)) and not args.yes:
            sys.exit(f"'{' > '.join(args.path)}' looks destructive — pass --yes to confirm.")
        click_path(args.path)
        print(f"clicked: {' > '.join(args.path)}")

    elif args.command == "key":
        send_key(args.key, args.mods.split(","))
        print(f"sent: {args.mods + '+' if args.mods else ''}{args.key}")

    elif args.command == "windows":
        names = windows()
        print(json.dumps(names, indent=2) if args.json else "\n".join(names))

    elif args.command == "mixer":
        return mixer_main(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
