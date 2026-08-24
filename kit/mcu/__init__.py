"""Claude MCU: a Mackie Control Universal emulator that lets Claude drive Logic Pro.

    protocol.py   wire format (pure, no I/O)
    state.py      what the host has told us (LCD, LEDs, faders, meters, ...)
    daemon.py     owns the virtual CoreMIDI ports and serves a unix socket
    client.py     socket client used by the CLI
    mcu.py        the CLI; run via ../mcuctl
    fake_logic.py a stand-in host for tests and smoke runs
"""
