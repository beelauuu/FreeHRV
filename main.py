"""FastAPI HRV dashboard — main entry point."""

import asyncio
import json
import os
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from bleak import BleakClient, BleakScanner
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ble import HR_MEASUREMENT, parse_hr_measurement
from hrv import HRVProcessor
from session import SessionManager
from stats import compute_session_stats

# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------

class AppState:
    def __init__(self):
        self.ws_clients: set[WebSocket] = set()
        self.hrv = HRVProcessor(window_sec=60)
        self.session = SessionManager()
        self.ble_task: asyncio.Task | None = None
        self.connected: bool = False
        self.scanning: bool = False
        self.reconnect_attempt: int = 0
        self.reconnecting: bool = False
        self.current_hr: int | None = None
        self.device_name: str | None = None
        self.device_address: str | None = None
        self.scan_results: list[dict] = []


state = AppState()


# ---------------------------------------------------------------------------
# WebSocket broadcast helpers
# ---------------------------------------------------------------------------

async def broadcast(msg: dict) -> None:
    dead: set[WebSocket] = set()
    data = json.dumps(msg)
    for ws in list(state.ws_clients):
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    state.ws_clients -= dead


def broadcast_sync(msg: dict) -> None:
    """Fire-and-forget broadcast from a sync context (not used; kept for reference)."""
    loop = asyncio.get_event_loop()
    asyncio.run_coroutine_threadsafe(broadcast(msg), loop)


async def broadcast_status() -> None:
    await broadcast({
        "type": "status",
        "connected": state.connected,
        "scanning": state.scanning,
        "recording": state.session.recording,
        "device": state.device_name,
        "address": state.device_address,
        "reconnecting": state.reconnecting,
        "attempt": state.reconnect_attempt,
    })


# ---------------------------------------------------------------------------
# BLE connection + streaming
# ---------------------------------------------------------------------------

MAX_RECONNECT = 3
RECONNECT_DELAY = 5.0


async def connect_and_stream(address: str, name: str) -> None:
    state.device_address = address
    state.device_name = name

    for attempt in range(1, MAX_RECONNECT + 2):  # +1 so first try isn't labelled reconnect
        if attempt == 1:
            state.reconnecting = False
            state.reconnect_attempt = 0
        else:
            state.reconnecting = True
            state.reconnect_attempt = attempt - 1
            await broadcast_status()
            await asyncio.sleep(RECONNECT_DELAY)

        # Check if task was cancelled during sleep
        if asyncio.current_task().cancelled():
            return

        try:
            stop_event = asyncio.Event()

            def on_disconnect(client: BleakClient) -> None:
                state.connected = False
                stop_event.set()

            async with BleakClient(address, disconnected_callback=on_disconnect) as client:
                state.connected = True
                state.reconnecting = False
                state.reconnect_attempt = 0
                await broadcast_status()

                def on_notification(sender, data: bytearray) -> None:
                    asyncio.ensure_future(_handle_notification(data))

                await client.start_notify(HR_MEASUREMENT, on_notification)
                await stop_event.wait()

        except asyncio.CancelledError:
            state.connected = False
            await broadcast_status()
            return
        except Exception as exc:
            state.connected = False
            print(f"BLE error (attempt {attempt}): {exc}")

        if attempt > MAX_RECONNECT:
            break

    # All attempts exhausted
    state.connected = False
    state.reconnecting = False
    await broadcast({
        "type": "status",
        "connected": False,
        "scanning": False,
        "recording": state.session.recording,
        "device": state.device_name,
        "address": state.device_address,
        "reconnecting": False,
        "attempt": 0,
        "error": "Max reconnect attempts reached",
    })


async def _handle_notification(data: bytearray) -> None:
    try:
        parsed = parse_hr_measurement(data)
    except Exception as exc:
        await broadcast({"type": "error", "message": f"Parse error: {exc}"})
        return

    hr = parsed["hr_bpm"]
    state.current_hr = hr
    rr_list = parsed["rr_intervals"]
    ts = time.time()

    if not rr_list:
        await broadcast({"type": "hr_only", "hr": hr, "ts": ts})
        return

    for rr in rr_list:
        metrics = state.hrv.process_rr(rr)
        if state.session.recording:
            state.session.record(
                hr=hr,
                rr=rr,
                rmssd=metrics["rmssd"],
                ln_rmssd=metrics["ln_rmssd"],
                is_artifact=metrics["is_artifact"],
            )
        await broadcast({
            "type": "data",
            "hr": hr,
            "rr": round(rr, 3),
            "rmssd": round(metrics["rmssd"], 3) if metrics["rmssd"] is not None else None,
            "ln_rmssd": round(metrics["ln_rmssd"], 4) if metrics["ln_rmssd"] is not None else None,
            "artifact_rate": metrics["artifact_rate"],
            "rr_count": metrics["rr_count"],
            "is_artifact": metrics["is_artifact"],
            "ts": ts,
        })


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Cleanup on shutdown
    if state.ble_task and not state.ble_task.done():
        state.ble_task.cancel()
        try:
            await state.ble_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="HRV Monitor", lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.post("/scan")
async def scan():
    state.scanning = True
    await broadcast_status()
    try:
        devices = await BleakScanner.discover(timeout=5.0)
        results = [
            {"name": d.name or "Unknown", "address": d.address}
            for d in devices
            if d.name  # only named devices
        ]
        state.scan_results = results
        await broadcast({"type": "scan_result", "devices": results})
        return JSONResponse({"devices": results})
    finally:
        state.scanning = False
        await broadcast_status()


@app.post("/connect")
async def connect(body: dict):
    address = body.get("address", "")
    name = body.get("name", "Unknown")
    if not address:
        return JSONResponse({"error": "address required"}, status_code=400)

    # Cancel existing task
    if state.ble_task and not state.ble_task.done():
        state.ble_task.cancel()
        try:
            await state.ble_task
        except asyncio.CancelledError:
            pass

    state.hrv.reset()
    state.ble_task = asyncio.create_task(connect_and_stream(address, name))
    return JSONResponse({"status": "connecting", "address": address})


@app.post("/disconnect")
async def disconnect():
    if state.ble_task and not state.ble_task.done():
        state.ble_task.cancel()
        try:
            await state.ble_task
        except asyncio.CancelledError:
            pass
    state.connected = False
    state.device_name = None
    state.device_address = None
    await broadcast_status()
    return JSONResponse({"status": "disconnected"})


@app.post("/session/start")
async def session_start(body: dict):
    label = body.get("label", "")
    notes = body.get("notes", "")
    sid = state.session.start(label=label, notes=notes)
    await broadcast({"type": "session_started", "id": sid})
    await broadcast_status()
    return JSONResponse({"session_id": sid})


@app.post("/session/stop")
async def session_stop():
    rr_data, hr_data, total_rr, artifact_count = state.session.get_accumulated_data()
    sid = state.session.stop()

    stats = compute_session_stats(rr_data, hr_data)
    stats["artifact_rate"] = round(artifact_count / total_rr * 100, 1) if total_rr else 0.0

    # Persist stats into meta JSON
    meta_path = os.path.join("sessions", f"hrv_{sid}_meta.json")
    if sid and os.path.exists(meta_path):
        with open(meta_path, "r+", encoding="utf-8") as f:
            meta = json.load(f)
            meta["stats"] = stats
            f.seek(0)
            json.dump(meta, f, indent=2)

    await broadcast({"type": "session_stopped", "id": sid, "stats": stats})
    await broadcast_status()
    return JSONResponse({"session_id": sid, "stats": stats})


@app.post("/hrv/window")
async def hrv_window(body: dict):
    seconds = body.get("seconds", 60)
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return JSONResponse({"error": "seconds must be an integer"}, status_code=400)
    state.hrv.set_window(seconds)
    return JSONResponse({"window_sec": seconds})


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    state.ws_clients.add(ws)
    # Send current status immediately on connect
    await broadcast_status()
    if state.scan_results:
        await ws.send_text(json.dumps({"type": "scan_result", "devices": state.scan_results}))
    try:
        while True:
            # Keep connection alive; client messages are ignored
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        state.ws_clients.discard(ws)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _open_browser():
    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:8765")


if __name__ == "__main__":
    t = threading.Thread(target=_open_browser, daemon=True)
    t.start()
    uvicorn.run(app, host="127.0.0.1", port=8765)
