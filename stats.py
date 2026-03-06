"""Session summary statistics: time-domain and frequency-domain HRV metrics."""

import math
from scipy.signal import detrend


def _compute_lf_hf(rr_data: list[tuple[float, float]]) -> tuple[float | None, float | None, float | None]:
    """Compute LF power, HF power, and LF/HF ratio via FFT resampling.

    Returns (lf_power, hf_power, lf_hf) or (None, None, None) if insufficient data.
    """
    try:
        import numpy as np
    except ImportError:
        return None, None, None

    if len(rr_data) < 30:
        return None, None, None

    rr_ms = [rr for _, rr in rr_data]

    # Build time axis from cumulative RR intervals.
    # Wall-clock timestamps are unreliable: multiple RR intervals per BLE
    # packet share the same timestamp, making np.interp produce garbage.
    # Cumulative RR gives the true beat-to-beat timeline.
    t_rel = [0.0]
    for rr in rr_ms[:-1]:
        t_rel.append(t_rel[-1] + rr / 1000.0)
    duration_sec = t_rel[-1]
    if duration_sec < 60:
        return None, None, None

    # Resample at 4 Hz
    fs = 4.0
    n_samples = int(duration_sec * fs)
    if n_samples < 8:
        return None, None, None

    t_uniform = np.linspace(0, duration_sec, n_samples)
    rr_resampled = np.interp(t_uniform, t_rel, rr_ms)

    # Detrend (remove linear trend) and apply Hann window
    rr_detrended = detrend(rr_resampled, type='linear')
    window = np.hanning(n_samples)
    rr_windowed = rr_detrended * window

    # FFT and power spectral density
    fft_vals = np.fft.rfft(rr_windowed)
    freqs = np.fft.rfftfreq(n_samples, d=1.0 / fs)
    power = (np.abs(fft_vals) ** 2) / (fs * n_samples)

    # Integrate LF and HF bands
    lf_mask = (freqs >= 0.04) & (freqs <= 0.15)
    hf_mask = (freqs >= 0.15) & (freqs <= 0.40)

    if not np.any(lf_mask) or not np.any(hf_mask):
        return None, None, None

    lf_power = float(np.trapz(power[lf_mask], freqs[lf_mask]))
    hf_power = float(np.trapz(power[hf_mask], freqs[hf_mask]))

    if hf_power <= 0:
        return lf_power, hf_power, None

    lf_hf = lf_power / hf_power
    return lf_power, hf_power, lf_hf


def compute_session_stats(
    rr_data: list[tuple[float, float]],
    hr_data: list[int],
) -> dict:
    """Compute summary statistics over a full session's clean RR intervals.

    Args:
        rr_data: List of (timestamp, rr_ms) tuples — non-artifact beats only.
        hr_data: HR values (bpm) corresponding to each entry in rr_data.

    Returns:
        dict with keys: mean_hr, sdnn, rmssd, lf_power, hf_power, lf_hf,
                        n_rr, duration_sec, artifact_rate (artifact_rate always 0.0 here;
                        caller overrides it with the true rate).
    """
    n_rr = len(rr_data)

    # Duration from wall-clock timestamps
    if n_rr >= 2:
        duration_sec = round(rr_data[-1][0] - rr_data[0][0], 2)
    else:
        duration_sec = 0.0

    # --- Time-domain ---
    if n_rr < 2:
        return {
            "mean_hr": None,
            "sdnn": None,
            "rmssd": None,
            "lf_power": None,
            "hf_power": None,
            "lf_hf": None,
            "n_rr": n_rr,
            "duration_sec": duration_sec,
            "artifact_rate": 0.0,
        }

    rr_vals = [rr for _, rr in rr_data]

    # mean_hr
    mean_hr = round(sum(hr_data) / len(hr_data), 1) if hr_data else None

    # SDNN — population SD
    mean_rr = sum(rr_vals) / n_rr
    sdnn = math.sqrt(sum((r - mean_rr) ** 2 for r in rr_vals) / n_rr)
    sdnn = round(sdnn, 2)

    # RMSSD — sqrt(mean(successive diffs²))
    successive_diffs_sq = [(rr_vals[i + 1] - rr_vals[i]) ** 2 for i in range(n_rr - 1)]
    rmssd = math.sqrt(sum(successive_diffs_sq) / len(successive_diffs_sq))
    rmssd = round(rmssd, 2)

    # --- Frequency-domain ---
    lf_power, hf_power, lf_hf = _compute_lf_hf(rr_data)

    return {
        "mean_hr": mean_hr,
        "sdnn": sdnn,
        "rmssd": rmssd,
        "lf_power": round(lf_power, 2) if lf_power is not None else None,
        "hf_power": round(hf_power, 2) if hf_power is not None else None,
        "lf_hf": round(lf_hf, 3) if lf_hf is not None else None,
        "n_rr": n_rr,
        "duration_sec": duration_sec,
        "artifact_rate": 0.0,
    }
