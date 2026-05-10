import json
import time
import re
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI()

AUDIO_DIR = Path("static/audio")
AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac", ".opus"}

# WebSocket -> {id, label, platform, joined_at, offset_ms, rtt_ms, ready_track}
client_info: dict[WebSocket, dict] = {}

# Currently selected track filename (relative to AUDIO_DIR), shared by all clients
current_track: str | None = None


def humanize_filename(name: str) -> str:
    """'01_some_song-name.mp3' -> 'Some Song Name'"""
    stem = Path(name).stem
    # strip leading track numbers like "01 " or "01_" or "01-"
    stem = re.sub(r"^\d+\s*[-_.\s]+", "", stem)
    # turn separators into spaces
    stem = re.sub(r"[_\-.]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    # capitalize words but preserve all-caps acronyms
    return " ".join(w if w.isupper() and len(w) > 1 else w.capitalize() for w in stem.split())


def scan_tracks() -> list[dict]:
    if not AUDIO_DIR.exists():
        return []
    tracks = []
    for f in sorted(AUDIO_DIR.iterdir()):
        if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
            tracks.append({
                "filename": f.name,
                "title": humanize_filename(f.name),
                "url": f"/static/audio/{f.name}",
                "size_bytes": f.stat().st_size,
            })
    return tracks


async def broadcast(message: dict, exclude: WebSocket | None = None):
    text = json.dumps(message)
    for client in list(client_info.keys()):
        if client is exclude:
            continue
        try:
            await client.send_text(text)
        except Exception:
            pass


async def broadcast_clients_update():
    info_list = [dict(info) for info in client_info.values()]
    await broadcast({"type": "clients_update", "clients": info_list})


async def broadcast_track_state():
    await broadcast({"type": "track_selected", "filename": current_track})


def all_ready_for(track: str | None) -> bool:
    if track is None or not client_info:
        return False
    return all(info.get("ready_track") == track for info in client_info.values())


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    global current_track
    await ws.accept()
    client_info[ws] = {
        "id": "pending",
        "label": "Connecting...",
        "platform": "",
        "joined_at": time.time(),
        "offset_ms": None,
        "rtt_ms": None,
        "ready_track": None,
    }
    print(f"Client connected. Total: {len(client_info)}")

    # Send the current track list and selected track right away
    await ws.send_text(json.dumps({"type": "tracks_list", "tracks": scan_tracks()}))
    await ws.send_text(json.dumps({"type": "track_selected", "filename": current_track}))

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type")

            if mtype == "hello":
                client_info[ws].update({
                    "id": msg.get("client_id", "anon"),
                    "label": msg.get("label", "Unknown device"),
                    "platform": msg.get("platform", ""),
                })
                print(f"  Hello from {client_info[ws]['label']} ({client_info[ws]['id']})")
                await broadcast_clients_update()

            elif mtype == "ping":
                await ws.send_text(json.dumps({
                    "type": "pong",
                    "client_send_time": msg.get("client_send_time"),
                    "server_time": time.time(),
                }))

            elif mtype == "sync_report":
                client_info[ws]["offset_ms"] = msg.get("offset_ms")
                client_info[ws]["rtt_ms"] = msg.get("rtt_ms")
                await broadcast_clients_update()

            elif mtype == "rescan_tracks":
                await broadcast({"type": "tracks_list", "tracks": scan_tracks()})

            elif mtype == "select_track":
                fname = msg.get("filename")
                tracks = scan_tracks()
                valid_names = {t["filename"] for t in tracks}
                if fname in valid_names:
                    current_track = fname
                    print(f"Track selected: {fname}")
                    # invalidate everyone's readiness — new track to download
                    for info in client_info.values():
                        info["ready_track"] = None
                    await broadcast_track_state()
                    await broadcast_clients_update()
                else:
                    print(f"Invalid track requested: {fname}")

            elif mtype == "track_ready":
                fname = msg.get("filename")
                if fname == current_track:
                    client_info[ws]["ready_track"] = fname
                    label = client_info[ws]["label"]
                    print(f"  {label} ready for {fname}")
                    await broadcast_clients_update()

            elif mtype == "trigger_play":
                if current_track is None:
                    print("Play ignored — no track selected")
                    continue
                if not all_ready_for(current_track):
                    print(f"Play ignored — not all clients ready for {current_track}")
                    await broadcast({"type": "play_blocked", "reason": "not_all_ready"})
                    continue
                target_time = time.time() + 2.0
                print(f"\nScheduling PLAY ({current_track}) at server-time {target_time:.4f}")
                await broadcast({
                    "type": "scheduled_play",
                    "target_time": target_time,
                    "filename": current_track,
                })

            elif mtype == "trigger_stop":
                print("Broadcast STOP")
                await broadcast({"type": "scheduled_stop"})

            else:
                print(f"Unknown message type: {mtype}")

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WS error: {e}")
    finally:
        info = client_info.pop(ws, None)
        label = info["label"] if info else "?"
        print(f"Client disconnected: {label}. Total: {len(client_info)}")
        await broadcast_clients_update()


@app.get("/")
async def root():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")