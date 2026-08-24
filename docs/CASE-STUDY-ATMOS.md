# Case study: rebuilding a Dolby Atmos master by protocol

One session. One real song — a finished track with a delivered stereo master and
a previous Atmos build that had been approved by ear. The instruction was to
reset the Atmos configuration and rebuild it "in record time".

**No screenshot was taken at any point. The cursor was never moved.**

Every number below was read back out of Logic Pro or measured from the exported
file. Command output is copied verbatim.

---

## 0 · Look before touching

Two independent reads of the same project, one per surface. The control surface,
which needs no focus:

```
$ mcuctl status
handshake: queried (3.3h ago)   online: no   last host message: 3m ago (26133 total)
transport: play=off  stop=on  record=off  rewind=off  fast_fwd=off  cycle=off
display:   assign 'PN'   time ' 41 2 2  2' (beats)
strips:    1 Audio1  2 Audio2  3 Audio3  4 RAPTUR  5 Audo16  6 Audo14  7 RAPTUR  8 RAPTUR
```

Then the accessibility tree, for the fields MIDI does not carry — and this is
where the first real finding appeared. The project's **track names lie**:

```
Track 1 “Audio 1”                  =>  RAPTURE (Backing Vocals)
Track 2 “Audio 2”, mute            =>  RAPTURE (Bass), muted
Track 4 “RAPTURE (Backing Vocals)” =>  RAPTURE (FX)
Track 6 “Audio 14”                 =>  THE GAME OF LUST.1      ← the stereo master
Track 7 “RAPTURE (Bass)”           =>  RAPTURE (Synth)
Track 8 “RAPTURE (Drums)”          =>  RAPTURE (Vocals)
```

The track called "Drums" holds the vocals. Any placement decision made from track
names would have put the vocal object where the drums belong. The region names
are the truth, and the region names are only visible through the tree.

## 1 · Reset the Atmos configuration

Spatial Audio was switched off and back on through Project Settings, and the
pop-ups were read back rather than assumed:

```
pop-ups: -3 dB Compensated, 7.1.2, 48 kHz, Off
pop-ups: -3 dB Compensated, 7.1.2, 48 kHz, Dolby Atmos
```

Logic then raised a modal alert. On a screenshot-driven run this is the moment
everything stalls — a dialog is a wall. Here it appeared *on the control
surface*:

```
top  |The project will be converted to 7.1.2 surround for mix |
val  |                                   OK     Cancel Do'tAs |
```

A V-Pot push under `OK` answered it. The blindest state in GUI automation turned
out to be the most readable one on this surface.

## 2 · Rebuild the mix state

The recipe is the author's own, proven on the previous build: the **bed** is the
finished stereo master, the doubled stems are muted, and the remaining stems
become quiet spatial garnish.

| What | Value | Surface used |
|---|---|---|
| Bed — `Audio 14`, the stereo master | output Surround, **−2.5 dB** | MCU fader, echo-confirmed |
| Bass + drums stems, main and Section | **muted** (they double the bed's low end) | accessibility, LED read back |
| 9 garnish stems | **−15 dB** | MCU faders |
| Empty `Audio 16` | Object → **Surround** | MCU preselect + V-Pot push |

Each fader move reported Logic's own echo:

```
fader 6: sent 11476 (-2.5 dB); host fader 12443 -> 11447 (-2.6 dB), LCD '-2.5 dB'
```

## 3 · Place the objects

Nine objects, positioned with [`objpan.py`](../kit/mcu/objpan.py) — a closed loop
over the Pan **Channel** view. Every parameter is stepped, re-read from Logic's
own display, and only reported when the display agrees:

```
-- strip 8 = main Vocals
  spread ok    176.0 ->   179.0  (target 180.0)
  lr     ok     -0.0 ->    -0.0  (target 0.0)
  bf     ok     96.9 ->   100.0  (target 100.0)
  elev   ok     15.0 ->    15.0  (target 15.0)
-- strip 4 = main FX
  bf     ok    -60.0 ->   -60.0  (target -60.0)
  elev   ok     35.0 ->    35.0  (target 35.0)
  spread ok    117.0 ->   120.0  (target 120.0)
```

All nine: vocals wide and front (spread 180, B/F +100, elev 15); backing vocals
behind (−40, elev 25); FX further back and higher (−60, elev 35); synths as a
height layer (+30, elev 50, spread 100); guitar left of centre (L/R −50, B/F +50,
elev 20). Main and Section sets both.

**The near miss.** The first attempt ran while the assignment display read `PN`
rather than `PN.` — Pan *Mixer* view, where each V-Pot is a different strip's
pan. Two values moved on the wrong tracks before the read-back caught it. They
were restored, and `ensure_channel_view()` now refuses to run unless the display
shows the period. Verification did not prevent the mistake; it *revealed* it,
which is the entire point.

## 4 · Verify by playback

The transport was driven from the surface and the meters read as numbers — no
image, no listening required to confirm signal flow:

```
--- t+10s  time:   '  6 1 4 77' (beats)
6 Audo14 #######.....        7      ← the bed, playing
8 RAPTUR #####.......        5      ← a vocal object, playing
3 Audio3 ............        0      ← muted stem, silent as intended
```

Through the loudest section, the Master strip's clip indicator stayed
`signal clipping off` — the check that matters, because the Dolby renderer has
no limiter and overs are the "distortion" that killed an earlier build.

## 5 · Export and verify the deliverable

`File > Export > Project as Spatial Audio…`, driven through the menu tree and
the save panel. The file was then parsed as RIFF, in place:

```
container: RIFF
chunks: JUNK, axml, chna, data, dbmd, fmt
fmt: 27 ch / 48000 Hz / 24-bit (tag 1)
data: 552 MB -> duration 142.0 s
ADM must-pass: PASS
```

`axml`, `chna` and `dbmd` are the three metadata chunks a Dolby Atmos ADM BWF
must carry; their presence, the channel count, the sample rate and the duration
were all checked against the song rather than assumed.

**27 channels, not 29.** The previous build carried two stray silent objects from
an empty track. Moving that track to the bed removed them — a defect fixed
because the strip table made it visible, not because anyone noticed it by eye.

Per-channel energy was measured from the export to confirm the architecture is
what it claims to be:

```
ch  1: rms  -26.2 dB   peak -14.4 dB     ← bed L
ch  2: rms  -26.2 dB   peak -14.4 dB     ← bed R
ch  3: rms  -18.6 dB   peak  -6.8 dB     ← bed C
ch 16: rms  -42.0 dB   peak -28.0 dB     ← object, ~16 dB under the bed
ch 22: rms -137.6 dB   peak -105.0 dB    ← near-silent object channel
```

The objects sit far below the bed, which is exactly the "halo, not competing
mix" the recipe calls for.

## What was not done

- **The listen.** The master is pending the human's ears. An agent can prove the
  file is structurally correct; it cannot approve it.
- **Loudness re-measurement of the full render.** A 5.1 monitoring bounce was
  started for a BS.1770 integrated-loudness reading — the correct method, per
  Apple's own note that the 5.1 monitoring format is required when measuring —
  but the session was interrupted before that number was captured. Levels are
  identical to the previously approved build, and that is stated rather than
  glossed.
- **Object routing changes.** The control surface has no `Object` entry in its
  output list, so object assignment is a one-way door from here. Existing object
  routing was never flattened.

## What this cost

| | previous GUI-driven build | this build |
|---|---|---|
| Interface | screenshots, cursor, typed values | MIDI protocol + accessibility |
| Value entry reliability | "entries silently miss ~20% of the time" (author's notes) | every value re-read; misses surfaced, not hidden |
| Coordinates | "re-derive x-coords per session — channel count changes shift everything" | none: strips and parameters are addressed by name |
| Wrong-target risk | high, invisible | caught twice by read-back, both restored |
| Screen | occupied by the agent | free; Logic ran in the background |
| Export verification | manual | RIFF chunk parse, automatic |
