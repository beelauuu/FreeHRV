"""BLE constants and HR Measurement characteristic parser."""

HR_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"


def parse_hr_measurement(data: bytearray) -> dict:
    """Parse the HR Measurement characteristic (0x2A37).

    Returns:
        {"hr_bpm": int, "rr_intervals": list[float]}
        RR intervals are in milliseconds.
    """
    flags = data[0]
    hr_format = flags & 0x01        # bit 0: 0=uint8, 1=uint16
    energy_present = (flags >> 3) & 0x01  # bit 3
    rr_present = (flags >> 4) & 0x01      # bit 4

    idx = 1
    if hr_format == 0:
        hr_bpm = data[idx]
        idx += 1
    else:
        hr_bpm = int.from_bytes(data[idx:idx + 2], byteorder="little")
        idx += 2

    if energy_present:
        idx += 2  # skip energy expended (uint16)

    rr_intervals: list[float] = []
    if rr_present:
        while idx + 1 < len(data):
            raw = int.from_bytes(data[idx:idx + 2], byteorder="little")
            rr_ms = raw * 1000.0 / 1024.0
            rr_intervals.append(rr_ms)
            idx += 2

    return {"hr_bpm": int(hr_bpm), "rr_intervals": rr_intervals}
