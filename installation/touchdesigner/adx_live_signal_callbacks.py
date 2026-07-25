"""Live two-participant lifecycle for the migrated five-plant point cloud.

This text is installed into ``adx_flower/ctrl/signal_callbacks``.  The plant
does not jump between fixed progress targets.  Instead, fresh two-person
signals produce a continuous growth velocity.  After full bloom, one strong
IMU acceleration/gyro event creates one scatter-and-return impulse.
"""

import csv
import math
import os
import time
from datetime import datetime


TAIL_BYTES = 240000
OUT_CHANNELS = (
    "growth", "bloom", "openness", "breathe", "disperse",
    "sway", "twist", "spin", "pulserate", "sparkle",
    "hue", "sat", "bright",
    "progress", "proximity", "hr_a", "hr_b", "hr_sync",
    "motion", "accel_anomaly", "motion_burst", "signal_strength",
    "presence", "pair", "stage", "live",
)


def _initial_state():
    return {
        "progress": 0.0,
        "session_started": False,
        "start_evidence": 0.0,
        "spin": 0.0,
        "last_time": 0.0,
        "last_change": -999.0,
        "fingerprint": None,
        "reset_token": None,
        "parsed": None,
        "sparkle": 0.0,
        "prev_sync": 0.0,
        "motion_lp": 0.0,
        "burst_started": -999.0,
        "burst_armed": True,
        "demo_epoch": None,
        "demo_prev_phase": 0.0,
    }


STATE = _initial_state()


IDLE = {
    "presence": 0.0,
    "pair": 0.0,
    "proximity": 0.0,
    "hr_a": 0.0,
    "hr_b": 0.0,
    "hr_sync": 0.0,
    "mean_bpm": 0.0,
    "motion": 0.0,
    "accel_anomaly": 0.0,
    "signal_strength": 0.0,
}


def _reset_runtime(keep_fingerprint=False):
    fingerprint = STATE.get("fingerprint") if keep_fingerprint else None
    reset_token = STATE.get("reset_token")
    STATE.clear()
    STATE.update(_initial_state())
    STATE["fingerprint"] = fingerprint
    STATE["reset_token"] = reset_token


def _f(row, key, default=None):
    value = row.get(key, "")
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _stamp(text):
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return None


def _smooth01(value):
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


def _read_tail(path):
    size = os.path.getsize(path)
    with open(path, "rb") as stream:
        header_line = stream.readline().decode("utf-8-sig", errors="replace").strip()
        header_end = stream.tell()
        start = max(header_end, size - TAIL_BYTES)
        stream.seek(start)
        blob = stream.read().decode("utf-8", errors="replace")
    if not header_line:
        return []
    lines = blob.splitlines()
    if start > header_end and lines:
        lines = lines[1:]
    headers = next(csv.reader([header_line]))
    rows = []
    for line in lines[-220:]:
        if not line.strip() or line == header_line:
            continue
        try:
            values = next(csv.reader([line]))
        except Exception:
            continue
        if len(values) == len(headers):
            rows.append(dict(zip(headers, values)))
    return rows


def _gyro_energy(row):
    values = []
    for index in range(5):
        for axis in ("x", "y", "z"):
            value = _f(row, "imu_gyro_%s_dps_%d" % (axis, index))
            if value is not None:
                values.append(value)
    if not values:
        return None
    return math.sqrt(sum(value * value for value in values) / len(values))


def _accel_anomaly(row):
    """Peak deviation from gravity, expressed in g."""
    values = []
    for index in range(5):
        ax = _f(row, "imu_accel_x_g_%d" % index)
        ay = _f(row, "imu_accel_y_g_%d" % index)
        az = _f(row, "imu_accel_z_g_%d" % index)
        if ax is None or ay is None or az is None:
            continue
        values.append(abs(math.sqrt(ax * ax + ay * ay + az * az) - 1.0))
    return max(values) if values else None


def _features(rows, now_stamp):
    devices = {}
    distance = None
    distance_stamp = -1.0
    within = 0.0
    rssi = None
    rssi_stamp = -1.0

    for row in rows:
        device = (row.get("device_id") or "").strip()
        stamp = _stamp(row.get("row_received_at", "")) or now_stamp
        if not device:
            continue
        slot = devices.setdefault(device, {
            "stamp": 0.0,
            "bpm": None,
            "bpm_stamp": -1.0,
            "wear": 0.0,
            "motion": 0.0,
            "accel": 0.0,
            "imu_stamp": -1.0,
        })
        slot["stamp"] = max(slot["stamp"], stamp)

        bpm = _f(row, "bpm")
        quality = _f(row, "heart_rate_quality", 0.0) or 0.0
        if bpm is not None and bpm > 30.0 and quality >= 0.12 and stamp >= slot["bpm_stamp"]:
            slot["bpm"] = bpm
            slot["bpm_stamp"] = stamp
            slot["wear"] = 1.0

        heart_state = (row.get("heart_rate_state") or "").strip()
        if heart_state and heart_state != "no_contact":
            wear_hint = 0.38 if heart_state == "low_perfusion" else 0.72
            slot["wear"] = max(slot["wear"], wear_hint)

        imu_valid = (_f(row, "imu_valid", 0.0) or 0.0) >= 0.5
        if imu_valid and stamp >= slot["imu_stamp"]:
            slot["imu_stamp"] = stamp
            slot["motion"] = _gyro_energy(row) or 0.0
            slot["accel"] = _accel_anomaly(row) or 0.0

        millimetres = _f(row, "distance_mm")
        valid = (_f(row, "distance_valid", 0.0) or 0.0) >= 0.5
        if valid and millimetres is not None and stamp >= distance_stamp:
            distance = millimetres
            distance_stamp = stamp
            within = _f(row, "within_one_meter", 0.0) or 0.0

        value = _f(row, "ranging_rssi_filtered_dbm")
        if value is None:
            value = _f(row, "ble_rssi_dbm")
        if value is not None and value < 0.0 and stamp >= rssi_stamp:
            rssi = value
            rssi_stamp = stamp

    newest = max([slot["stamp"] for slot in devices.values()] or [now_stamp])
    online = {
        name: slot for name, slot in devices.items()
        if newest - slot["stamp"] <= 6.0
    }

    if distance is not None and newest - distance_stamp <= 3.0:
        proximity = 1.0 - (distance - 150.0) / 1650.0
    elif rssi is not None and newest - rssi_stamp <= 3.0:
        proximity = (rssi + 82.0) / 34.0 * 0.70
    else:
        proximity = 0.0
    proximity = max(0.0, min(1.0, proximity))
    if within >= 0.5 and newest - distance_stamp <= 3.0:
        proximity = max(proximity, 0.62)

    engagement = {}
    fresh_motion = []
    fresh_accel = []
    for name, slot in online.items():
        imu_fresh = newest - slot["imu_stamp"] <= 1.0
        score = 0.12 + slot["wear"] * 0.68
        if imu_fresh and (slot["motion"] > 8.0 or slot["accel"] > 0.06):
            score += 0.20
        engagement[name] = max(0.0, min(1.0, score))
        if imu_fresh:
            fresh_motion.append(slot["motion"])
            fresh_accel.append(slot["accel"])

    beats = []
    for name in sorted(online):
        slot = online[name]
        if slot["bpm"] is not None and newest - slot["bpm_stamp"] <= 4.0:
            beats.append(slot["bpm"])
    hr_a = beats[0] if len(beats) > 0 else 0.0
    hr_b = beats[1] if len(beats) > 1 else 0.0
    hr_sync = 0.0
    if len(beats) >= 2:
        hr_sync = max(0.0, min(1.0, 1.0 - abs(hr_a - hr_b) / 30.0))

    scores = sorted(engagement.values(), reverse=True)
    presence = scores[0] if scores else 0.0
    pair = min(scores[:2]) if len(scores) >= 2 else 0.0
    bpm_coverage = min(1.0, len(beats) / 2.0)
    motion = max(fresh_motion) if fresh_motion else 0.0
    motion_norm = max(0.0, min(1.0, motion / 220.0))
    accel_anomaly = max(fresh_accel) if fresh_accel else 0.0
    accel_anomaly = max(0.0, min(2.0, accel_anomaly))

    signal_strength = (
        pair * 0.32
        + proximity * 0.27
        + bpm_coverage * 0.20
        + hr_sync * 0.12
        + min(1.0, motion_norm * 2.0) * 0.09
    )
    signal_strength = max(0.0, min(1.0, signal_strength))

    return {
        "presence": presence,
        "pair": pair,
        "proximity": proximity,
        "hr_a": hr_a,
        "hr_b": hr_b,
        "hr_sync": hr_sync,
        "mean_bpm": sum(beats) / len(beats) if beats else 0.0,
        "motion": motion_norm,
        "accel_anomaly": accel_anomaly,
        "signal_strength": signal_strength,
    }


def _check_reset_token(csv_path):
    reset_path = os.path.join(os.path.dirname(csv_path), "reset_lifecycle.txt")
    try:
        token = open(reset_path, "r", encoding="utf-8").read().strip()
    except OSError:
        token = None
    if token and STATE["reset_token"] is None:
        STATE["reset_token"] = token
    elif token and token != STATE["reset_token"]:
        STATE["reset_token"] = token
        _reset_runtime()
        STATE["reset_token"] = token


def _load(owner):
    path = str(owner.par.Csvfile.eval())
    if not path or not os.path.exists(path):
        return dict(IDLE), False
    _check_reset_token(path)
    try:
        stat = os.stat(path)
    except OSError:
        return dict(IDLE), False

    fingerprint = (stat.st_mtime_ns, stat.st_size)
    previous = STATE["fingerprint"]
    if previous is not None and stat.st_size < previous[1] * 0.5:
        _reset_runtime()

    changed = fingerprint != STATE["fingerprint"]
    if changed or STATE["parsed"] is None:
        rows = _read_tail(path)
        if not rows:
            STATE["parsed"] = dict(IDLE)
        else:
            stamps = [_stamp(row.get("row_received_at", "")) for row in rows]
            newest = max([stamp for stamp in stamps if stamp is not None] or [0.0])
            STATE["parsed"] = _features(rows, newest)
        STATE["fingerprint"] = fingerprint
        STATE["last_change"] = absTime.seconds
    # File age is authoritative on first load too; otherwise an old CSV would
    # be treated as live for eight seconds merely because the project opened.
    live = time.time() - stat.st_mtime < 8.0
    return dict(STATE["parsed"] or IDLE), live


def _demo(now):
    """Accelerated demonstration; it still drives the same continuous logic."""
    if STATE["demo_epoch"] is None:
        STATE["demo_epoch"] = now
    elapsed = (now - STATE["demo_epoch"]) % 36.0
    phase = elapsed / 36.0
    if phase < STATE["demo_prev_phase"]:
        _reset_runtime(keep_fingerprint=True)
        STATE["demo_epoch"] = now
        elapsed = 0.0
        phase = 0.0
    STATE["demo_prev_phase"] = phase

    pair = 0.0 if elapsed < 2.0 else min(1.0, (elapsed - 2.0) / 2.0)
    proximity = max(0.0, min(1.0, (elapsed - 2.0) / 12.0))
    signal = max(0.0, min(1.0, pair * 0.45 + proximity * 0.40 + 0.15))
    shake = 0.95 if 27.0 <= elapsed <= 27.45 else 0.03
    return {
        "presence": pair,
        "pair": pair,
        "proximity": proximity,
        "hr_a": 72.0,
        "hr_b": 76.0,
        "hr_sync": max(0.0, min(1.0, (elapsed - 7.0) / 8.0)),
        "mean_bpm": 74.0,
        "motion": 0.92 if shake > 0.5 else 0.10,
        "accel_anomaly": shake,
        "signal_strength": signal,
        "_demo_speed": 2.8,
    }


def onCook(scriptOp):
    root = scriptOp.parent().parent()
    now = absTime.seconds
    dt = now - STATE["last_time"]
    if dt <= 0.0 or dt > 0.5:
        dt = 1.0 / 60.0
    STATE["last_time"] = now

    feat, live = _load(root)
    demo = bool(root.par.Demomode.eval())
    if demo:
        feat = _demo(now)
    elif not live:
        feat = dict(IDLE)

    # A one-second accumulation avoids accidental starts from a single packet.
    # Once started, progress is monotonic and its velocity is continuously
    # controlled by signal quality; there are no discrete target progress jumps.
    start_ready = feat["pair"] >= 0.30 and feat["signal_strength"] >= 0.24
    if not STATE["session_started"]:
        if start_ready:
            STATE["start_evidence"] = min(2.0, STATE["start_evidence"] + dt)
        else:
            STATE["start_evidence"] = max(0.0, STATE["start_evidence"] - dt * 0.6)
        if STATE["start_evidence"] >= 1.0:
            STATE["session_started"] = True

    progress = STATE["progress"]
    if STATE["session_started"]:
        required = 0.20 + progress * 0.18
        strength = feat["signal_strength"]
        if feat["pair"] >= 0.22 and strength >= required:
            response = max(0.0, min(1.0, (strength - required) / max(0.12, 1.0 - required)))
            speed = (0.009 + response * 0.014) * feat.get("_demo_speed", 1.0)
            progress += dt * speed
    progress = max(0.0, min(1.0, progress))
    STATE["progress"] = progress

    # Visual regions remain continuous mappings; stage is display metadata only.
    growth = max(0.0, min(1.0, progress / 0.55))
    bloom = max(0.0, min(1.0, (progress - 0.44) / 0.40))
    openness = max(0.0, min(1.0, (progress - 0.84) / 0.16))
    breathe = max(0.0, min(1.0, (progress - 0.88) / 0.12))

    # IMU event: fast attack, short hold, then exact smooth convergence.
    accel_anomaly = feat.get("accel_anomaly", 0.0)
    shock = accel_anomaly >= 0.32 or feat["motion"] >= 0.72
    calm = accel_anomaly <= 0.12 and feat["motion"] <= 0.28
    if progress >= 0.985 and STATE["burst_armed"] and shock:
        STATE["burst_started"] = now
        STATE["burst_armed"] = False
    if calm and now - STATE["burst_started"] > 1.0:
        STATE["burst_armed"] = True

    burst_age = now - STATE["burst_started"]
    if burst_age < 0.0 or burst_age >= 3.6:
        burst = 0.0
    elif burst_age < 0.18:
        burst = _smooth01(burst_age / 0.18)
    elif burst_age < 0.52:
        burst = 1.0
    else:
        burst = 1.0 - _smooth01((burst_age - 0.52) / 3.08)
    disperse = burst * breathe * float(root.par.Disperse.eval())

    bpm = feat["mean_bpm"] if feat["mean_bpm"] > 30.0 else 66.0
    pulserate = 2.0 * math.pi * (bpm / 60.0)
    alpha = min(1.0, dt / 1.5)
    STATE["motion_lp"] += (feat["motion"] - STATE["motion_lp"]) * alpha
    motion_s = STATE["motion_lp"]
    sway = (0.007 + motion_s * 0.020 + (1.0 - feat["proximity"]) * 0.007)
    sway *= float(root.par.Swayscale.eval())

    STATE["spin"] += dt * float(root.par.Spinspeed.eval())
    spin = STATE["spin"] % 360.0
    twist = 0.16 * bloom + 0.10 * openness + 0.04 * math.sin(now * 0.055) + 0.03 * motion_s
    twist *= float(root.par.Twistscale.eval())
    twist = max(-0.6, min(0.6, twist))

    jump = max(0.0, feat["hr_sync"] - STATE["prev_sync"])
    STATE["prev_sync"] = feat["hr_sync"]
    STATE["sparkle"] = max(STATE["sparkle"] * math.exp(-dt * 1.6), jump * 3.0, bloom * 0.10)
    sparkle = min(1.0, STATE["sparkle"])
    arousal = max(0.0, min(1.0, (bpm - 55.0) / 50.0))
    hue = float(root.par.Hueshift.eval()) + (arousal - 0.5) * 0.03
    sat = float(root.par.Saturation.eval()) * (0.85 + 0.30 * bloom)
    bright = float(root.par.Brightness.eval()) * (0.72 + 0.38 * progress)

    stage = min(4.0, math.floor(progress * 5.0))
    values = {
        "growth": growth,
        "bloom": bloom,
        "openness": openness,
        "breathe": breathe,
        "disperse": disperse,
        "sway": sway,
        "twist": twist,
        "spin": spin,
        "pulserate": pulserate,
        "sparkle": sparkle,
        "hue": hue,
        "sat": sat,
        "bright": bright,
        "progress": progress,
        "proximity": feat["proximity"],
        "hr_a": feat["hr_a"],
        "hr_b": feat["hr_b"],
        "hr_sync": feat["hr_sync"],
        "motion": feat["motion"],
        "accel_anomaly": accel_anomaly,
        "motion_burst": burst,
        "signal_strength": feat["signal_strength"],
        "presence": feat["presence"],
        "pair": feat["pair"],
        "stage": stage,
        "live": 0.0 if demo else float(live),
    }

    scriptOp.clear()
    scriptOp.numSamples = 1
    for name in OUT_CHANNELS:
        channel = scriptOp.appendChan(name)
        channel[0] = float(values[name])
    return
