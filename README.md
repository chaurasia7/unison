# यूनिसन (Unison)

**Browser-based multi-device audio synchronization with sub-10 ms accuracy.**

A self-hosted web app that lets multiple devices on the same network play the same audio file in tight synchronization. No installs on the listening devices — every laptop, phone, or tablet just opens a URL.

---

## Qn is Why ? I mean why not

Imagine pressing play on two laptops at the exact same instant — but not by clicking with your reflexes. By having a server coordinate them down to the millisecond, so the two speakers behave like one wide source. That's the goal.

The classroom version of this problem ("two people clicking play together") gets you ~150 ms of slop, which sounds like a slap-back echo. Actual perceptual unity needs the offset between speakers under ~10 ms — at which point you stop hearing two sources and start hearing one wider one.

This project builds that, in a browser, without any plugins or apps.

---

## Features

- **Sample-accurate synchronization** across all connected devices using the Web Audio API scheduler
- **Cross-platform** — runs in any modern browser on macOS, Windows, Linux, Android, iOS
- **Network clock synchronization** via Cristian's-algorithm-style handshake (NTP-style) with median-of-fast-half offset estimation
- **Hardware latency compensation** — measures and subtracts each device's audio output latency
- **Periodic re-sync** to fight clock drift over long sessions
- **Per-device volume control** (independent gain per client)
- **Live device dashboard** showing each connected client's offset, RTT, and ready state
- **Track library** — drop audio files into a folder and they appear instantly across all devices
- **Readiness gating** — server only triggers playback when every client has finished decoding the chosen track, preventing partial-start desyncs
- **Zero-config local network** — no cloud, no port forwarding, no domain required

---

## How it works

The system synchronizes three different clocks across each client:

1. **Server wall clock** (`time.time()`) — the single source of truth for all timing decisions
2. **Client wall clock** (`Date.now()`) — used for clock sync handshakes and message scheduling
3. **Client audio clock** (`AudioContext.currentTime`) — sample-accurate, what audio is actually scheduled against

### Synchronization pipeline

```
[1] Clock sync handshake
    Client sends 20 pings to server. Each ping records:
        t0 = client send time
        t_server = server receive timestamp
        t1 = client receive time
    Offset estimate: t_server + RTT/2 − (t0 + RTT/2)
    Final offset: median of the 10 fastest samples (filters jitter)

[2] Trigger play
    Any client clicks "Play synced".
    Server picks target_time = now() + 2.0 (jitter buffer).
    Server broadcasts {scheduled_play, target_time, filename}.

[3] Per-client scheduling
    Each client converts:
        target_time (server seconds)
            → local Date.now() time (using offset)
                → AudioContext.currentTime (re-anchored each call)
                    → minus baseLatency + outputLatency (hardware compensation)
    Schedules: source.start(scheduledTime)

[4] Sample-accurate playback
    Web Audio's audio thread fires the buffer at the scheduled time,
    immune to main-thread jank, setTimeout slop, or GC pauses.
```

### Why the 2-second buffer?

Network jitter, decoder warm-up, and main-thread lag mean the "play at T" message can take a variable amount of time to be processed by each client. The 2-second lead time is the jitter buffer — long enough that every client can prepare without missing the deadline. Reduce it and you risk a client missing the start window; increase it and the UI feels sluggish.

### Measured performance

On a typical home Wi-Fi network with one Mac and one Windows laptop:

| Metric | Value |
|---|---|
| Clock offset accuracy | ~1–5 ms after handshake |
| Round-trip time (RTT) | 1–10 ms wired LAN, 5–30 ms Wi-Fi |
| Cross-device audio offset | < 10 ms (perceptually a single source) |
| Hardware latency (Mac) | ~5 ms |
| Hardware latency (Windows) | ~10 ms |

System clocks between OSes can disagree by 50+ ms (especially Windows vs macOS NTP behaviors), so the clock-sync layer is doing real work — it isn't a redundant safeguard.

---

## Architecture

```
┌─────────────────────────────────────────────┐
│              FastAPI server                 │
│  ┌────────────────────────────────────────┐ │
│  │  WebSocket endpoint (/ws)              │ │
│  │  ── ping/pong (clock sync)             │ │
│  │  ── trigger_play / scheduled_play      │ │
│  │  ── select_track / track_ready         │ │
│  │  ── clients_update broadcast           │ │
│  └────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────┐ │
│  │  Static file server (/static/audio/*)  │ │
│  └────────────────────────────────────────┘ │
└──────────────┬──────────────────────────────┘
               │  ws:// over LAN
       ┌───────┼───────┬───────────┐
       ▼       ▼       ▼           ▼
   ┌──────┐ ┌──────┐ ┌──────┐  ┌──────┐
   │ Mac  │ │ Win  │ │ Phone│  │  …   │
   │      │ │      │ │      │  │      │
   │ Web  │ │ Web  │ │ Web  │  │      │
   │Audio │ │Audio │ │Audio │  │      │
   └──────┘ └──────┘ └──────┘  └──────┘
```

The server is intentionally minimal — it brokers messages and schedules events, but does no audio processing. Each client downloads the chosen audio file once, decodes it locally into an `AudioBuffer`, and is responsible for its own scheduling. This makes the server stateless w.r.t. audio content and trivially scalable to N clients.

---

## Setup

### Requirements

- Python 3.9+
- All listening devices on the same Wi-Fi or LAN

### Installation

```bash
# Clone the project
git clone <your-repo-url> unison
cd unison

# Install dependencies
pip install 'fastapi' 'uvicorn[standard]'
```

### Project structure

```
unison/
├── server.py
├── README.md
└── static/
    ├── index.html
    └── audio/
        ├── song1.mp3
        ├── song2.flac
        └── ...
```

### Adding audio

Drop any of these formats into `static/audio/`:

`.mp3` `.wav` `.ogg` `.m4a` `.flac` `.aac` `.opus`

Filenames are humanized for display: `01_blinding-lights.mp3` → "Blinding Lights".
The track list refreshes when you click the ↻ button — no server restart needed.

---

## Running

### Start the server

```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

The `--host 0.0.0.0` is essential — it tells the server to accept connections from other devices on the network. Without it, only the host laptop can reach the server.

### Find the server's local IP

| OS | Command |
|---|---|
| macOS | `ipconfig getifaddr en0` |
| Linux | `hostname -I` |
| Windows | `ipconfig` (look under "IPv4 Address") |

You'll get something like `192.168.1.42`.

### Connect from each device

Open this URL in any modern browser:

```
http://<server-ip>:8000
```

The host machine itself can use `http://localhost:8000`.

### Play music

1. On each device, click **Initialize** (this creates the AudioContext — browsers require a user gesture).
2. Pick a track from the list. All devices switch to it automatically and start preloading.
3. Wait for every device card in the "Connected devices" panel to show **ready** (green).
4. Click **Play synced** on any device. All devices begin playback at the same wall-clock instant.

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | FastAPI + Uvicorn (async, WebSocket-native) |
| Transport | WebSockets over plain HTTP (LAN-only by default) |
| Audio engine | Web Audio API (`AudioBufferSourceNode`, `GainNode`) |
| Frontend | Vanilla HTML/CSS/JS — no build step, no framework |
| Time sync | Custom NTP-style protocol over WebSocket |

---
