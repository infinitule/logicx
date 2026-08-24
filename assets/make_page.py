#!/usr/bin/env python3
"""Assemble docs/index.html — the GitHub Pages landing page — with the SVGs inlined.

    python3 assets/make_page.py

Inlining keeps the page a single self-contained file: it renders identically on
GitHub Pages, from a file:// URL, or anywhere else it is opened.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
A = ROOT / "assets"


def svg(name):
    s = (A / name).read_text()
    s = re.sub(r'\swidth="\d+"\s', " ", s, count=1)
    s = re.sub(r'\sheight="\d+"\s', " ", s, count=1)
    return s.replace("<svg ", '<svg class="fig" ', 1)


HEAD = """<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Logic Control Surface</title>
<style>
  :root{--bg:#0B0F14;--panel:#111820;--panel2:#0F1620;--edge:#1E2A38;--txt:#E6EDF3;
        --mut:#8B949E;--dim:#6E7A87;--blue:#58A6FF;--grn:#3FB950;--red:#FF7B72;--pur:#BC8CFF;--amb:#D29922}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
       font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
       -webkit-font-smoothing:antialiased}
  .wrap{max-width:1080px;margin:0 auto;padding:56px 24px 96px}
  .fig{width:100%;height:auto;display:block;margin:34px 0}
  h1{font-size:15px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);
     font-weight:600;margin:0 0 26px}
  h2{font-size:27px;line-height:1.25;margin:64px 0 14px;letter-spacing:-.015em}
  h3{font-size:18px;margin:34px 0 10px;color:var(--txt)}
  p{color:var(--mut);margin:14px 0}
  .lede{font-size:20px;line-height:1.55;color:var(--txt);margin:26px 0}
  .lede b{color:var(--grn);font-weight:600}
  .sub{font-style:italic;color:var(--dim);font-size:15px}
  a{color:var(--blue);text-decoration:none}
  a:hover{text-decoration:underline}
  code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
  code{font-size:.9em;background:var(--panel);border:1px solid var(--edge);
       border-radius:5px;padding:1px 5px;color:var(--txt)}
  pre{background:var(--panel2);border:1px solid var(--edge);border-radius:10px;
      padding:16px 18px;overflow-x:auto;font-size:13px;line-height:1.55;color:var(--mut)}
  pre code{background:none;border:0;padding:0}
  table{width:100%;border-collapse:collapse;margin:22px 0;font-size:14.5px}
  th,td{text-align:left;padding:11px 12px;border-bottom:1px solid var(--edge);vertical-align:top}
  th{color:var(--dim);font-weight:600;font-size:12px;letter-spacing:.06em;text-transform:uppercase}
  td{color:var(--mut)}
  td b,td strong{color:var(--txt)}
  .badges{display:flex;flex-wrap:wrap;gap:8px;margin:22px 0 8px}
  .badge{font-size:11.5px;letter-spacing:.04em;border:1px solid var(--edge);
         border-radius:999px;padding:5px 12px;color:var(--mut);background:var(--panel)}
  .quote{border-left:2px solid var(--amb);padding:6px 0 6px 18px;margin:22px 0;
         color:var(--mut);font-size:15px}
  .rule{height:1px;background:var(--edge);margin:56px 0}
  .foot{color:var(--dim);font-size:14px}
  ul{color:var(--mut)} li{margin:7px 0}
  @media (max-width:640px){.wrap{padding:34px 16px 64px}h2{font-size:22px}.lede{font-size:17px}}
</style>"""


def main():
    p = []
    p.append(HEAD)
    p.append('<div class="wrap">')
    p.append("<h1>Logic Control Surface</h1>")
    p.append(svg("banner.svg"))
    p.append('<div class="badges">'
             + "".join(f'<span class="badge">{b}</span>' for b in [
                 "Mackie Control Universal", "Logic Pro 12.3.1", "96 offline + 11 live tests",
                 "python-rtmidi only", "MIT"])
             + "</div>")
    p.append('<p class="lede">An AI agent configured a finished <b>Dolby Atmos</b> music master in Logic Pro '
             "— 15 tracks mapped, 9 spatial objects placed, a 27-channel ADM BWF exported and chunk-verified — "
             "<b>without taking a single screenshot or moving the cursor once.</b> It did it by becoming a "
             "Mackie Control: a virtual MIDI device Logic adopts on its own and streams its entire mix state to.</p>")
    p.append('<p class="sub">This is not “AI mixing”. Every creative decision — placement map, levels, the final '
             "listen — is the human’s. This is about the <em>interface</em> between an agent and an application.</p>")

    p.append('<div class="rule"></div>')
    p.append("<h2>The problem, stated honestly</h2>")
    p.append("<p>The default way an agent uses a desktop app is to screenshot it, find a control by pixel, click, "
             "and screenshot again. It works. It is also slow, expensive, and structurally blind — and when it "
             "fails it fails silently. Here is a real failure from this project’s logs. The agent clicked "
             "<em>one</em> mute button, then read the mixer back:</p>")
    p.append("<pre><code>strip       dB   raw    pan  mute  solo\n"
             "Audio 1     0.0  173.0  0    on    off      &larr; not asked for\n"
             "Audio 2     0.0  173.0  0    on    off      &larr; not asked for\n"
             "Audio 3     0.0  173.0  0    on    off      &larr; the one that was clicked\n"
             "Audio 4     0.0  173.0  0    on    off      &larr; not asked for\n"
             "...all eight strips muted...</code></pre>")
    p.append("<p>Logic applies an edit to <strong>every selected channel strip</strong>. A screenshot taken "
             "immediately after would have shown eight lit mute buttons — and an agent that trusted its own "
             "action would have reported “muted Audio 3” and moved on.</p>")
    p.append('<div class="quote">“numeric fields in panner (dbl-click, type, <strong>ZOOM-VERIFY every entry — '
             'entries silently miss ~20% of the time</strong>) … Re-derive x-coords per session.”<br>'
             '<span class="foot">— the author’s own notes from the previous, GUI-driven build of this same song</span></div>')

    p.append("<h2>How it works</h2>")
    p.append(svg("surfaces.svg"))
    p.append("<h3>1 · Speak an interface the app already trusts</h3>")
    p.append("<p>Logic Pro has shipped Mackie Control Universal support for two decades. Faders are pitch-bend, "
             "buttons are note-on, the 8×2 character LCD arrives as SysEx, meters as channel pressure. A daemon "
             "opens a <em>virtual</em> CoreMIDI port pair; Logic probes it, recognises the handshake, and adopts "
             "the surface <strong>by itself</strong>. From then on Logic pushes its own state at us — names, "
             "fader positions, every LED, the playhead, the meters — and we push back faders, V-Pot turns and "
             "button presses. No focus, no window, no pixels.</p>")
    p.append("<h3>2 · The app’s own display is the sensor</h3>")
    p.append("<p>Nothing is reported from the command that was sent. A fader move is reported from the "
             "<em>echo</em> Logic sends back; a mute from the LED Logic lights; a dB value from the text Logic "
             "renders. Where a control has no absolute setter, the agent runs a closed loop until the app’s own "
             "readout agrees — or says it gave up.</p>")
    p.append(svg("loop.svg"))
    p.append("<h3>3 · Documentation becomes queryable data, not memory</h3>")
    p.append("<p>Apple’s four Logic Pro guides (2,686 pages) are harvested into 3,048 topic records and "
             "<strong>7,447 parameter records</strong> — one per documented control, with its type, choices and "
             "page number. This is how the agent discovered mid-session that a second press of STOP returns the "
             "playhead to the project start: a fact it did not know and did not guess.</p>")

    p.append("<h2>Which model did what</h2>")
    p.append("<table><tr><th>Model</th><th>Role</th></tr>"
             "<tr><td><b>Claude Fable 5</b></td><td>Wrote the protocol implementation and its tests as parallel "
             "subagents — <code>protocol.py</code>, <code>state.py</code>, <code>daemon.py</code>, "
             "<code>client.py</code>, <code>mcu.py</code>, <code>fake_logic.py</code> and 78 tests in one pass; "
             "later the documentation harvester. Then, as independent reviewers, found six real defects in its "
             "own output.</td></tr>"
             "<tr><td><b>Claude Opus 5</b></td><td>Orchestrated those subagents, then drove the live session: "
             "probed the accessibility tree by hand, mapped the MCU semantics against Apple’s documentation, ran "
             "the Atmos rebuild, and verified every result.</td></tr></table>")
    p.append("<p>Specification and verification are separable from execution, and they want different amounts of "
             "thinking.</p>")

    p.append("<h2>The measurement that justifies it</h2>")
    p.append(svg("cost.svg"))
    p.append("<table><tr><th></th><th>screenshot loop</th><th>protocol surface</th></tr>"
             "<tr><td>Read the full mix state</td><td>≥1 screenshot at <b>2,352 tokens</b>, values unreadable to "
             "one decimal</td><td><b>510 tokens</b>, 16 strips × 10 fields, exact</td></tr>"
             "<tr><td>Set a fader to exactly −6.0 dB</td><td>type, zoom, verify; ~20% silent miss rate</td>"
             "<td>closed loop, <b>6 steps / 5.25 s</b>, echo-confirmed</td></tr>"
             "<tr><td>Know a change landed</td><td>compare two images</td>"
             "<td><code>host fader 12443 -&gt; 9874 (-6.1 dB), LCD '-6.0 dB'</code></td></tr>"
             "<tr><td>While it works</td><td>the agent owns the screen</td><td>Logic can be in the background</td></tr>"
             "<tr><td>Wrong-target risk</td><td>invisible</td><td>setter deselects first, then re-reads</td></tr>"
             "</table>")

    p.append("<h2>Proof: a real Atmos master, built by protocol</h2>")
    p.append(svg("atmos-field.svg"))
    p.append("<p>Bed at −2.5 dB, doubled stems muted, nine garnish objects at −15 dB placed to the house map, "
             "stray silent objects eliminated (<b>29 ch → 27 ch</b>), exported and parsed: "
             "<code>27 ch / 48 kHz / 24-bit</code>, <code>axml + chna + dbmd</code> present, 142.0 s, 552 MB. "
             "Pending the one thing an agent must never do — the human’s listen.</p>")

    p.append("<h2>What we learned the hard way</h2>")
    rows = [
        ("One mute click muted all eight strips",
         "Logic applies mute/solo/fader edits to <strong>every selected strip</strong>. Setters now deselect first."),
        ("<code>set value</code> on a fader moved 0.1 dB regardless of the value sent",
         "The slider is not absolute: <code>set value</code> = ±1 unit nudge, <code>AXIncrement</code> = ±10 units ≈ 1 dB, "
         "and the dB lives in the element’s <em>name</em>. Hence the closed loop."),
        ("Accessibility calls failed intermittently with “window not found”",
         "Logic exposes windows to accessibility <strong>only while frontmost</strong>, and another app stealing focus "
         "breaks a lookup between two statements."),
        ("<code>send([0x90, 0xDF, 0x7F])</code> was logged as a press but Logic received <strong>RECORD</strong>",
         "An 8-bit data byte is re-parsed by CoreMIDI into a different message, bypassing the <code>--yes</code> guard. "
         "Framing is now validated before anything reaches the wire."),
        ("<code>daemon stop</code> could have killed an unrelated process",
         "A stale pidfile containing <code>0</code> or <code>-1</code> turns it into <code>os.kill(-1, …)</code> — every "
         "process the user owns. The pid must now verify as one of ours."),
        ("V-Pot turns edited the wrong track",
         "In Pan <em>Mixer</em> view each V-Pot is a different strip’s pan; only in Pan <em>Channel</em> view are they the "
         "selected strip’s object parameters. The difference is a single period in a two-character display: "
         "<code>PN</code> vs <code>PN.</code>"),
        ("A modal alert blocked everything and could not be seen",
         "Logic <strong>mirrors its alert dialogs onto the MCU LCD</strong>, and a V-Pot push answers them. The blindest "
         "moment in GUI automation turned into a readable one."),
        ("Output assignment “committed” but nothing changed",
         "The MCU preselect flashes until a V-Pot push confirms it — and the list has <strong>no “Object” entry</strong>. "
         "Object routing is unreachable from a control surface: a real ceiling, and a one-way door."),
        ("Logic sat at 100% CPU with no windows after a reboot",
         "Its configured audio interface was absent; Logic loops in <code>MDCA::Init</code> until the device returns. "
         "Diagnosable in seconds with <code>sample</code>, invisible to a screenshot."),
    ]
    p.append("<table><tr><th>Symptom</th><th>The actual cause</th></tr>"
             + "".join(f"<tr><td>{a}</td><td>{b}</td></tr>" for a, b in rows) + "</table>")

    p.append("<h2>What this is not</h2>")
    p.append("<ul>"
             "<li><strong>Not a general “AI controls any app” system.</strong> It works because Logic ships a "
             "documented control-surface protocol. The transferable part is the method: find the machine interface "
             "the app already exposes, and fall back to pixels only for what it genuinely cannot reach.</li>"
             "<li><strong>Not autonomous music-making.</strong> The placement map, the levels and the approval are "
             "the human’s.</li>"
             "<li><strong>Not a plug-in or a patch.</strong> Nothing is injected into Logic — a MIDI device and the "
             "macOS accessibility API, both public.</li>"
             "<li><strong>Not tested beyond one machine.</strong> Logic Pro 12.3.1, macOS 15, Python 3.14, one project.</li>"
             "<li><strong>No vendor documentation is redistributed.</strong> The harvester builds the corpus from "
             "your own copies of Apple’s PDFs.</li></ul>")

    p.append('<div class="rule"></div>')
    p.append('<p class="foot">Full method, wire format, and the step-by-step rebuild are in the repository’s '
             '<code>docs/</code>: METHOD.md, PROTOCOL.md, CASE-STUDY-ATMOS.md, and a generated ARCHITECTURE.md '
             'symbol index.<br><br>'
             'Mackie Control Universal is a protocol by Mackie Designs; Logic Pro, macOS and the Logic documentation '
             'are Apple’s; Dolby Atmos is Dolby’s. Built on '
             '<a href="https://github.com/SpotlightKid/python-rtmidi">python-rtmidi</a> and '
             '<a href="https://github.com/thestk/rtmidi">RtMidi</a>, unmodified. MIT licensed.<br><br>'
             'If you extend it, the one rule worth keeping is the one that made it work: '
             '<strong>never report a change you did not read back.</strong></p>')
    p.append("</div>")

    out = ROOT / "docs" / "index.html"
    out.write_text("\n".join(p))
    print(f"docs/index.html  {out.stat().st_size // 1024} KB (SVGs inlined)")


if __name__ == "__main__":
    main()
