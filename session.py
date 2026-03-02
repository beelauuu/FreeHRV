"""Session management: CSV recording + metadata JSON."""

import csv
import json
import os
import time
from datetime import datetime, timezone


class SessionManager:
    def __init__(self, sessions_dir: str = "sessions"):
        self.sessions_dir = sessions_dir
        self._session_id: str | None = None
        self._csv_file = None
        self._writer = None
        self._start_time: float | None = None
        self._label: str = ""
        self._notes: str = ""
        self._rr_accum: list[tuple[float, float]] = []
        self._hr_accum: list[int] = []
        self._total_rr: int = 0
        self._artifact_count: int = 0
        os.makedirs(sessions_dir, exist_ok=True)

    @property
    def recording(self) -> bool:
        return self._csv_file is not None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def start(self, label: str = "", notes: str = "") -> str:
        if self.recording:
            self.stop()

        now = datetime.now()
        self._session_id = now.strftime("%Y%m%d_%H%M%S")
        self._label = label
        self._notes = notes
        self._start_time = time.time()
        self._rr_accum = []
        self._hr_accum = []
        self._total_rr = 0
        self._artifact_count = 0

        csv_path = os.path.join(self.sessions_dir, f"hrv_{self._session_id}.csv")
        self._csv_file = open(csv_path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._csv_file)
        self._writer.writerow(
            ["timestamp", "elapsed_sec", "hr_bpm", "rr_ms",
             "rmssd", "ln_rmssd", "label", "is_artifact"]
        )
        self._csv_file.flush()
        return self._session_id

    def record(
        self,
        hr: int,
        rr: float,
        rmssd: float | None,
        ln_rmssd: float | None,
        is_artifact: bool,
    ) -> None:
        if not self.recording:
            return
        self._total_rr += 1
        if is_artifact:
            self._artifact_count += 1
        else:
            self._rr_accum.append((time.time(), rr))
            self._hr_accum.append(hr)
        elapsed = round(time.time() - self._start_time, 3)
        ts = datetime.now(tz=timezone.utc).isoformat()
        self._writer.writerow([
            ts,
            elapsed,
            hr,
            round(rr, 3),
            round(rmssd, 4) if rmssd is not None else "",
            round(ln_rmssd, 4) if ln_rmssd is not None else "",
            self._label,
            int(is_artifact),
        ])
        self._csv_file.flush()

    def get_accumulated_data(self) -> tuple[list, list, int, int]:
        """Return accumulated RR/HR data before stopping. Call before stop()."""
        return (
            list(self._rr_accum),
            list(self._hr_accum),
            self._total_rr,
            self._artifact_count,
        )

    def stop(self) -> str | None:
        if not self.recording:
            return None

        session_id = self._session_id
        self._csv_file.close()
        self._csv_file = None
        self._writer = None

        end_time = datetime.now(tz=timezone.utc).isoformat()
        start_dt = datetime.fromtimestamp(self._start_time, tz=timezone.utc).isoformat()
        meta = {
            "session_id": session_id,
            "start": start_dt,
            "end": end_time,
            "label": self._label,
            "notes": self._notes,
        }
        meta_path = os.path.join(self.sessions_dir, f"hrv_{session_id}_meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

        self._session_id = None
        self._start_time = None
        self._rr_accum = []
        self._hr_accum = []
        self._total_rr = 0
        self._artifact_count = 0
        return session_id
