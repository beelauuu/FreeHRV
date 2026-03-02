"""HRV processing: artifact detection, RMSSD, lnRMSSD."""

import math
import time
from collections import deque


class HRVProcessor:
    def __init__(self, window_sec: int = 60):
        self.window_sec = window_sec
        # (timestamp, rr_ms, is_artifact)
        self.buffer: deque[tuple[float, float, bool]] = deque()
        # last N accepted RR for median-based artifact detection
        self.recent: deque[float] = deque(maxlen=10)

    def process_rr(self, rr_ms: float) -> dict:
        """Process a single RR interval and return updated metrics."""
        is_artifact = self._detect_artifact(rr_ms)
        ts = time.time()
        self.buffer.append((ts, rr_ms, is_artifact))
        if not is_artifact:
            self.recent.append(rr_ms)
        self._prune_buffer(ts)
        metrics = self._compute_metrics()
        metrics["is_artifact"] = is_artifact
        return metrics

    def _detect_artifact(self, rr_ms: float) -> bool:
        """Return True if rr_ms is likely an artifact."""
        if rr_ms < 300 or rr_ms > 2000:
            return True
        if len(self.recent) >= 3:
            med = _median(list(self.recent))
            if med > 0 and abs(rr_ms - med) / med > 0.25:
                return True
        return False

    def _prune_buffer(self, now: float) -> None:
        cutoff = now - self.window_sec
        while self.buffer and self.buffer[0][0] < cutoff:
            self.buffer.popleft()

    def _compute_metrics(self) -> dict:
        valid = [rr for (_, rr, art) in self.buffer if not art]
        total = len(self.buffer)
        artifact_count = sum(1 for (_, _, art) in self.buffer if art)

        rmssd: float | None = None
        ln_rmssd: float | None = None

        if len(valid) >= 2:
            diffs_sq = [(valid[i + 1] - valid[i]) ** 2 for i in range(len(valid) - 1)]
            rmssd = math.sqrt(sum(diffs_sq) / len(diffs_sq))
            if rmssd > 0:
                ln_rmssd = math.log(rmssd)

        artifact_rate = (artifact_count / total * 100.0) if total > 0 else 0.0

        return {
            "rmssd": rmssd,
            "ln_rmssd": ln_rmssd,
            "artifact_rate": round(artifact_rate, 1),
            "rr_count": len(valid),
        }

    def set_window(self, sec: int) -> None:
        self.window_sec = sec
        self._prune_buffer(time.time())

    def reset(self) -> None:
        self.buffer.clear()
        self.recent.clear()


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 == 1 else (s[mid - 1] + s[mid]) / 2.0
