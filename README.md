# HRV Monitor

A local HRV dashboard for the Polar H10 (and compatible Bluetooth HR monitors). Streams real-time heart rate and RR intervals over BLE, computes HRV metrics, and displays them in a browser dashboard.

## Features

- Live heart rate and RMSSD / ln(RMSSD) display
- Selectable RMSSD window: 30s / 60s / 120s
- Artifact detection and rate tracking
- Real-time Chart.js dual-axis chart (HR + RMSSD)
- Session recording — saves CSV + JSON metadata to `sessions/`
- Session summary modal with SDNN, RMSSD, and LF/HF ratio
- Dark/light mode with system preference detection and `localStorage` persistence
- Auto-reconnect on BLE drop (3 attempts, 5s delay)

## Requirements

- Python 3.10+
- A Bluetooth adapter supported by [bleak](https://github.com/hbldh/bleak)
- Polar H10 or any BLE device exposing the standard HR Measurement characteristic (0x2A37)

## Setup

```bash
pip install -r requirements.txt
python main.py
```

Opens at `http://127.0.0.1:8765`.

## Usage

1. Click **Scan** to discover nearby BLE HR devices
2. Select your device and click **Connect**
3. Metrics and chart update automatically via WebSocket
4. Optionally set a label/notes and click **Start Recording** to save a session
5. Click **Stop** — a summary modal appears and the session is written to `sessions/`

## Project Structure

```
main.py          FastAPI app, AppState, BLE task, WebSocket endpoint
ble.py           BLE UUIDs and HR Measurement characteristic parser
hrv.py           HRVProcessor — RMSSD, ln(RMSSD), artifact detection
session.py       SessionManager — CSV + JSON session persistence
static/
  index.html     Single-file frontend (Chart.js, dark/light theme)
sessions/        Auto-created; holds recorded session files
```

## Artifact Detection

RR intervals are rejected if:
- `rr < 300 ms` or `rr > 2000 ms`
- `|rr − median| / median > 0.25` (±25% of rolling median)
