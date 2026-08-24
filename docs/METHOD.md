# The method

*How to make an agent operate an application without looking at it — and how to
tell whether it worked.*

This document is the transferable part. The Logic Pro code in `kit/` is one
instance of it.

---

## 1. The thesis

An agent driving a GUI is doing lossy translation twice. The application already
holds its state as numbers; it renders those numbers into pixels for a human;
the agent then tries to recover the numbers from the pixels. Every step of that
round trip loses precision, costs context, and — this is the part that matters —
**fails silently**, because a wrong pixel looks exactly like a right one.

Most non-trivial applications expose a second interface that skips the round
trip entirely, built for some other machine to use:

| Interface class | Examples |
|---|---|
| Control-surface protocols | Mackie Control (Logic, Pro Tools, Cubase, Reaper), HUI, EUCON |
| Accessibility trees | macOS AX, Windows UIA, AT-SPI — every app that ships screen-reader support |
| Automation dictionaries | AppleScript, COM, D-Bus |
| Network control | OSC, MIDI 2.0 Property Exchange, Ableton Link, REST admin APIs |
| Scripting runtimes | Blender/Photoshop/Figma plugin APIs, Excel VBA, Logic's Scripter |
| The document format itself | reading and writing the project file when the app is closed |

The method is: **find that interface, become a first-class client of it, and use
pixels only for what it genuinely cannot reach.**

The phrase that captures it: the agent does not *use* the app, it *is* a device
the app was built to talk to.

## 2. The surface ladder

Rank every available interface by how structured it is, and always use the
narrowest one that reaches the control. For Logic Pro that ordering came out as:

| Rung | Surface | Reaches | Costs |
|---|---|---|---|
| 1 | **Mackie Control over virtual MIDI** | faders, V-Pots, buttons, LEDs, LCD text, meters, playhead, plug-in parameters | nothing — no focus, no window, works in the background |
| 2 | **Accessibility tree** | every visible control's value, including ones the surface protocol omits | needs the app frontmost; a focus flicker |
| 3 | **Menu tree** (discovered live, invoked by name) | every command the app has | brings the app forward |
| 4 | **OSC** (Dolby Renderer) | transport, monitoring, mute | fire-and-forget: no acknowledgement, must confirm elsewhere |
| 5 | **computer-use** | third-party plug-in windows; anything above cannot reach | screenshots, latency, and the silence problem |

Two rules make the ladder work rather than just sort it:

- **A surface is only admissible if it can be read back.** OSC sits at rung 4 not
  because it is slow but because it never answers; anything sent over it must be
  confirmed on another surface or reported as unconfirmed.
- **Cross-verify across rungs.** The MCU says a fader is at 9874; accessibility
  says the strip reads −6.1 dB. Two independent paths agreeing is the strongest
  evidence available without human ears.

## 3. The read-back rule

> Never report a change you did not read back.

This is the whole discipline, and every piece of the code exists to serve it.

| Failure it catches | What the naive agent would have said |
|---|---|
| A mute click applied to eight selected strips | "muted Audio 3" |
| A fader `set value` that nudges instead of setting | "set to −6.0 dB" |
| A parameter entry that silently missed (~20% of the time in the GUI workflow) | "spread set to 180" |
| A V-Pot turn in the wrong view editing a different track | "vocals moved to the front" |
| A MIDI message re-parsed by the OS into RECORD | "pressed button 0xDF" |
| A modal alert swallowing every subsequent click | "applied the settings" |

Implementation shape, in three parts:

1. **Setters return before/after, not success.** `set_volume()` reports
   `-6.0 dB -> 0.0 dB (target 0.0, 6 steps, raw 173, 5.25s)`. There is no code
   path that prints "done".
2. **Closed loops for non-absolute controls.** Read the app's rendered value,
   compute the difference, take the coarsest step that does not overshoot,
   re-read. Terminate on tolerance, on a step budget, or on *the readout not
   moving* — the last one is what turns a silent failure into a reported one.
3. **Idempotent commands read state first.** `mute 3 on` presses only if the LED
   says otherwise, so it is safe to run twice and honest when it is already true.

## 4. Documentation as data, not memory

An agent that recalls an application's behaviour from training is guessing with
confidence. The fix is mechanical: harvest the vendor's own documentation into
structured records and query it before acting.

For Logic that is 2,686 pages of Apple PDFs → **3,048 topic records** and
**7,447 parameter records** (`name`, `control` type, `description`, parsed
`choices`, `range`, page number). It is queried like a database, never loaded
into context:

```bash
python3 reference/kit/params.py "Mackie Control" --grep "V-POT"
python3 reference/kit/lookup.py "Plug-in edit view" --source control-surfaces
```

Two things this bought that memory could not:

- The **paging rule** for plug-in parameters ("parameters change in groups of
  eight") — needed to enumerate a plug-in's controls without guessing.
- The **second-STOP-returns-to-start** behaviour, found by querying the transport
  table when the playhead would not rewind. It worked on the first attempt.

The corpus also states its own limits. Distributor loudness targets are not in
Apple's or Dolby's documentation, so the agent is instructed to say so rather
than improvise a number.

## 5. Discovering the surface for an app that is not Logic

A checklist, in the order that pays off fastest:

1. **Does the app support control surfaces or hardware controllers?** Ship-grade
   protocols are documented, stable across versions, and bidirectional. This is
   the jackpot: the app *wants* to be driven.
2. **Does it ship screen-reader support?** Then its accessibility tree has named,
   readable, settable elements. Enumerate it before assuming anything: `role`,
   `description`, `value`, and available `actions`.
3. **Does it have an automation dictionary or scripting runtime?** AppleScript,
   COM, or a plugin API turns commands into named calls.
4. **Does it emit or accept OSC/MIDI/network control?** Often undocumented but
   discoverable by listening on the port.
5. **Is the document format readable?** Sometimes the honest answer is to close
   the app and edit the file.
6. **Only then, pixels.**

For each candidate surface, answer these before building on it:

- What does it let me **read**? (A write-only surface is a liability.)
- What does it let me **write**?
- What does it **not** reach? Write that down — it becomes the fallback rule.
- What does it do **silently wrong**? (Modes, selections, focus, clamping.)

The Logic answer to the third question is instructive: the control surface
cannot assign an **object** output at all — the value list simply has no such
entry. Discovering that was a result, not a defeat: it defines a one-way door,
and the rule became *never flatten object routing*, because the surface that can
undo it does not exist.

## 6. Safety rails that come with the territory

An agent with a machine interface is faster at everything, including damage.

| Rail | Why |
|---|---|
| Destructive-command guard (`--yes` for record-arm, record, replace, drop) | On a real session, arming a track over a take is not reversible. |
| **Message framing validation** | An 8-bit data byte is re-parsed by CoreMIDI into a *different message* — `0x90 0xDF 0x7F` arrived as RECORD. A guard on button names is worthless if raw bytes bypass it. |
| Test isolation by device id and port name | Logic adopts *any* new MIDI port matching the protocol. Tests use device id `0x7E` and a pid-unique port name so a test run can never touch a live session. |
| Verified-pid shutdown | A stale pidfile containing `0` or `-1` turns `daemon stop` into `os.kill(-1, …)` — every process the user owns. |
| One-way-door awareness | Anything the surface cannot undo (object routing) is off-limits without explicit human instruction. |
| Scratch project for capability tests | New capabilities are proven on a disposable project, never on the user's work. |

## 7. Division of labour between models

The build used two models with different jobs, and the split is part of the
method rather than an accident of availability:

- **A fast model writes the surface implementation.** Protocol work is
  well-specified: a written spec plus a fake host to test against produces
  thousands of lines of correct code in one pass. Claude Fable 5 wrote
  `protocol.py`, `state.py`, `daemon.py`, `client.py`, `mcu.py`, `fake_logic.py`
  and 78 tests, then — as independent reviewer subagents — found six genuine
  defects in that same output, including the MIDI-framing bypass.
- **A slower orchestrator holds the live state.** Driving a real application is
  a sequence of small, irreversible-ish decisions where the cost of a wrong
  assumption is high and the evidence is a two-character LCD cell. Claude Opus 5
  did the probing, the cross-checking, and the refusals.

The general shape: **specification and verification are separable from
execution**, and they want different amounts of thinking.

## 8. Where the method stops

- It needs a real machine interface. An app with no surface, no accessibility
  and no scripting leaves only pixels.
- Structured surfaces have gaps, and the gaps are discovered empirically. Budget
  for that: roughly half the live session time here went into finding what the
  surfaces would *not* do.
- Read-back is not the same as *correct*. The MCU echo proves Logic accepted a
  fader move; it does not prove the mix is good. Taste stays human — here the
  final Atmos master is explicitly pending a listen.
- A protocol client can still be wrong at a semantic level: the V-Pot view
  confusion would have edited the wrong track *with perfect verification of the
  wrong thing*. Verification tells you what changed, not whether you meant it.
