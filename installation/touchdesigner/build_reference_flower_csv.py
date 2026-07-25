"""Build a full-lifecycle CSV-driven point-cloud flower.

Run this script while the reference TOE is open.  It leaves the original
network intact and adds an isolated ``adx_*`` render branch that reuses the
reference project's transformed flower point clouds.  The new branch reads
the live sensor CSV and drives the complete interaction lifecycle: empty ->
germination -> branching -> blooming -> full bloom -> breathing, with the
spread/gather response layered on only after the flower has opened.
"""

import os


ROOT_PATH = "/project1"
CSV_PATH = r"E:\AdventureX\outputs\ADX_Flower_PointCloud\live\sensor_live.csv"
OUTPUT_TOE = r"E:\AdventureX\outputs\ADX_Flower_PointCloud\ADX_Flower_PointCloud.toe"
FLOWER_PLY = r"E:\AdventureX\outputs\ADX_Flower_PointCloud\assets\reference\flower2.ply"
SUTSUKI_PLY = r"E:\AdventureX\outputs\ADX_Flower_PointCloud\assets\reference\sutsuki.ply"
PREVIEW_PATH = r"E:\AdventureX\outputs\ADX_Flower_PointCloud\preview_bloom.png"


def setpar(node, name, value):
    par = getattr(node.par, name, None)
    if par is None:
        return False
    try:
        par.val = value
        return True
    except Exception:
        return False


def setexpr(node, name, expression):
    par = getattr(node.par, name, None)
    if par is None:
        return False
    try:
        par.expr = expression
        return True
    except Exception:
        return False


def wire(source, target, index=0):
    source.outputConnectors[0].connect(target.inputConnectors[index])


def fresh(root, op_type, name, x=0, y=0):
    old = root.op(name)
    if old is not None:
        old.destroy()
    node = root.create(op_type, name)
    node.nodeX = x
    node.nodeY = y
    return node


SCATTER_PIXEL = r'''// Full lifecycle + stable spread/gather for the reference bouquet.
uniform vec4 uControl; // spread, time, beats/second, per-source seed
uniform vec4 uLife;    // progress, growth, bloom, breathe
out vec4 fragColor;

float hash12(vec2 p)
{
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

vec3 hash32(vec2 p)
{
    vec3 q = vec3(
        hash12(p + 17.1),
        hash12(p + 53.7),
        hash12(p + 91.3));
    return q * 2.0 - 1.0;
}

void main()
{
    ivec2 pixel = ivec2(gl_FragCoord.xy);
    vec4 src = texelFetch(sTD2DInputs[0], pixel, 0);
    vec3 p = src.xyz;

    // Unused texels in the final point-file row must not create an origin clump.
    if (dot(abs(p), vec3(1.0)) < 0.000001) {
        fragColor = TDOutputSwizzle(vec4(10000.0, 10000.0, 10000.0, 0.0));
        return;
    }

    vec2 key = vec2(pixel) + vec2(uControl.w * 131.0, uControl.w * 71.0);
    float h = hash12(key);
    vec3 stableRandom = normalize(hash32(key) + vec3(0.001, 0.002, 0.003));

    // This center is the measured center of the complete reference bouquet,
    // not the center of each copied flower.  All points therefore return to
    // the exact original cluster instead of being reassembled differently.
    vec3 clusterCenter = vec3(-1.36, 0.16, 0.48);

    // Compute the final world-space height of every source before revealing
    // it.  Source 5 is rotated in the approved reference composition, so its
    // local Y is not "up"; using p.y directly made parts of its crown appear
    // before its roots.  These coefficients are the verified Geometry COMP
    // transforms of the five approved sources.
    float worldY = p.y;
    if (uControl.w < 1.5) {
        worldY = p.y - 0.05;
    } else if (uControl.w < 2.5) {
        worldY = p.y;
    } else if (uControl.w < 3.5) {
        worldY = p.y + 0.05;
    } else if (uControl.w < 4.5) {
        worldY = p.y - 0.05;
    } else {
        worldY = dot(p, vec3(-0.2766641, 0.8447346, -0.4581271)) + 0.38;
    }

    // A single rising world-space plane now reveals the entire plant from
    // the lowest root (-1.72) to the highest crown (1.68).  No random timing
    // offset is allowed to make an upper flower appear before a lower stem.
    float height = clamp((worldY + 1.72) / 3.40, 0.0, 1.0);
    float appearAt = 0.015 + height * 0.94;
    float appear = smoothstep(appearAt - 0.018, appearAt + 0.028, uLife.y);
    appear *= smoothstep(0.012, 0.050, uLife.x);

    // Bud -> bloom: only the flower-head region closes slightly around the
    // bouquet axis.  At full bloom the exact approved reference coordinates
    // are restored; no random flower reassembly is involved.
    float head = smoothstep(0.46, 0.88, height);
    float budScale = mix(0.76, 1.0, uLife.z);
    vec2 headDelta = p.xz - clusterCenter.xz;
    p.xz = clusterCenter.xz + headDelta * mix(1.0, budScale, head);
    p.y -= head * (1.0 - uLife.z) * 0.055;
    float openness = smoothstep(0.84, 1.0, uLife.x);
    p.xz = clusterCenter.xz + (p.xz - clusterCenter.xz) * (1.0 + head * openness * 0.065);

    // Full bloom keeps breathing at the BPM-derived tempo.  The deliberately
    // small amplitude reads as respiration, not high-frequency jitter.
    float breath = 1.0 + uLife.w * 0.005 * sin(uControl.y * uControl.z * 1.3823008);
    p = clusterCenter + (p - clusterCenter) * breath;

    vec3 radial = normalize((p - clusterCenter) + stableRandom * 0.08);
    vec3 direction = normalize(mix(radial, stableRandom, 0.28));

    // Spread/gather becomes active only after blooming; early lifecycle
    // frames stay readable as roots, stems and flower heads grow in order.
    float opened = smoothstep(0.76, 0.94, uLife.x);
    // Spread is stable at rest. The previous per-point beat phase caused the
    // cloud to shimmer back and forth and read as blur instead of particles.
    float amount = clamp(uControl.x, 0.0, 1.0) * opened;
    float radius = mix(0.34, 1.08, h);

    p += direction * amount * radius;
    // A static curl preserves organic structure without continuous jitter.
    p.x += sin(p.y * 1.7 + uControl.w) * amount * 0.022;
    p.z += cos(p.x * 1.3 + uControl.w) * amount * 0.020;

    if (appear < 0.001) {
        p = vec3(10000.0);
    }
    fragColor = TDOutputSwizzle(vec4(p, appear));
}
'''


CSV_EXECUTE = r'''import csv
import math
import os
import time


STATE = {
    'progress': 0.0,
    'max_target': 0.0,
    'lifecycle_started': False,
    'reset_token': None,
    'hold': 0.0,
    'last_update': 0.0,
    'last_size': 0,
    'motion_lp': 0.0,
    'motion_burst': 0.0,
}

# The gateway includes both the firmware device ID and the physical sender
# MAC.  Treat the MAC as authoritative so a stale/misconfigured device ID can
# never merge both wearables into the first participant slot.
REGISTERED_DEVICE_BY_MAC = {
    '44:B1:76:01:D7:C8': 'person_01',
    '44:B1:76:08:4B:E8': 'person_40',
}


def _number(row, key, default=0.0):
    try:
        value = row.get(key, "")
        return float(value) if value not in (None, "") else float(default)
    except Exception:
        return float(default)


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def _tail_rows(path, count=180):
    if not path or not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8-sig", newline="") as stream:
        header_line = stream.readline().strip()
    if not header_line:
        return []
    headers = next(csv.reader([header_line]))
    with open(path, "rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - 262144), os.SEEK_SET)
        text = stream.read().decode("utf-8", errors="ignore")
    lines = [line for line in text.splitlines() if line.strip()]
    if size > 262144 and lines:
        lines = lines[1:]
    rows = []
    for line in lines[-count:]:
        try:
            values = next(csv.reader([line]))
            if len(values) >= len(headers):
                rows.append(dict(zip(headers, values)))
        except Exception:
            pass
    return rows


def _features(rows):
    devices = {}
    distances = []
    rssi_by_device = {}
    within_by_device = {}
    gyro_values = []

    for row in rows:
        source_mac = (row.get('source_mac') or '').strip().upper()
        device = REGISTERED_DEVICE_BY_MAC.get(
            source_mac,
            (row.get('device_id') or '').strip(),
        )
        if device:
            slot = devices.setdefault(device, {'bpm': 0.0, 'quality': 0.0, 'state': ''})
            # Only a PPG-bearing row advances heart-rate state.  The collector
            # already performs the intended five-second weak-signal hold and
            # writes that held value into subsequent PPG rows.  Mirror the
            # newest row exactly, including clearing an expired value; keeping
            # the last non-empty BPM here made an unworn ring look alive for
            # the rest of the session.
            if _number(row, 'ppg_present', 0.0) >= 0.5:
                bpm = _number(row, 'bpm', 0.0)
                quality = _number(row, 'heart_rate_quality', 0.0)
                slot['bpm'] = bpm if 35.0 <= bpm <= 190.0 and quality >= 0.15 else 0.0
                slot['quality'] = quality if slot['bpm'] > 0.0 else 0.0
                slot['state'] = (row.get('heart_rate_state') or '').strip()

        distance = _number(row, 'distance_mm', 0.0)
        valid = _number(row, 'distance_valid', 0.0)
        if valid >= 0.5 and distance > 0.0:
            distances.append(distance)
            if device:
                within_by_device[device] = max(
                    within_by_device.get(device, 0.0),
                    _number(row, 'within_one_meter', 0.0),
                )

        rssi = _number(row, 'ranging_rssi_filtered_dbm', 0.0)
        if device and rssi < 0.0:
            rssi_by_device.setdefault(device, []).append(rssi)

        if _number(row, 'imu_valid', 0.0) >= 0.5:
            for sample in range(5):
                gx = _number(row, 'imu_gyro_x_dps_%d' % sample, 0.0)
                gy = _number(row, 'imu_gyro_y_dps_%d' % sample, 0.0)
                gz = _number(row, 'imu_gyro_z_dps_%d' % sample, 0.0)
                gyro_values.append(math.sqrt(gx * gx + gy * gy + gz * gz))

    # A replay/export may legitimately omit computed BPM, as the supplied
    # 20260725_003324 sample does.  Two live device IDs still represent the
    # two-person interaction; BPM is an enhancement, not a gate that blocks
    # the plant from ever growing.
    registered_names = ('person_01', 'person_40')
    device_names = [name for name in registered_names if name in devices]
    device_names.extend(
        name for name in sorted(devices)
        if name not in registered_names
    )
    # Both boards have optical heart-rate sensing. They may keep sending IMU
    # and ranging rows while unworn, so only real optical-contact states count
    # as participants; no_contact/low_signal/low_perfusion remain idle.
    worn_states = {
        'warming_up', 'contact_unstable', 'good', 'degraded',
        'low_periodicity', 'ambiguous_period', 'long_gap',
    }
    # A fresh BPM written by the collector is sufficient even while its state
    # is the short `signal_weak/held` bridge.  This preserves useful readings
    # without requiring a narrow explicit-wearing label; once the collector's
    # five-second hold expires, the newest PPG row clears bpm back to zero.
    wearing_names = [
        name for name in device_names
        if devices[name]['bpm'] > 0.0 or devices[name]['state'] in worn_states
    ]
    beats = [devices[name]['bpm'] for name in wearing_names if devices[name]['bpm'] > 0.0]
    presence = 1.0 if wearing_names else 0.0
    pair = 1.0 if len(wearing_names) >= 2 else 0.0
    distance = sum(distances[-8:]) / len(distances[-8:]) if distances else 2200.0
    proximity = _clamp(1.0 - (distance - 150.0) / 1650.0)
    # Mirror the hardware README's host-side interaction rule: both RSSI
    # directions must cross the -87 dBm entry boundary. The firmware's two
    # per-node zone flags are accepted only when both agree as well.
    dual_rssi_inside = False
    dual_zone_inside = False
    if pair > 0.5:
        expected = wearing_names[:2]
        if all(rssi_by_device.get(name) for name in expected):
            recent_rssi = []
            for name in expected:
                values = rssi_by_device[name][-5:]
                recent_rssi.append(sum(values) / len(values))
            dual_rssi_inside = all(value >= -87.0 for value in recent_rssi)
        dual_zone_inside = all(
            within_by_device.get(name, 0.0) >= 0.5 for name in expected
        )
    # The ranging board currently reports the measured pair distance in one
    # direction only. A fresh sub-metre distance is already direct evidence
    # that the two participants are interacting; dual RSSI/zone remains an
    # optional stronger path when both directions are available.
    range_inside = bool(pair > 0.5 and distances and distance <= 1000.0)
    host_interaction = 1.0 if dual_rssi_inside or dual_zone_inside or range_inside else 0.0
    if host_interaction > 0.5:
        proximity = max(proximity, 0.72)
    if len(beats) >= 2:
        hr_sync = _clamp(1.0 - abs(beats[0] - beats[1]) / 30.0)
    else:
        hr_sync = 0.55 if pair > 0.5 and proximity >= 0.62 else 0.0
    bpm = sum(beats) / len(beats) if beats else 66.0
    if gyro_values:
        recent_gyro = sorted(gyro_values[-80:])
        motion_raw = recent_gyro[int((len(recent_gyro) - 1) * 0.90)]
    else:
        motion_raw = 0.0
    # Normal wrist movement remains calm; a fast broad wave rises quickly to
    # a full impulse. One valid IMU is sufficient and the other may be absent.
    motion = _clamp((motion_raw - 45.0) / 115.0)
    participants = []
    for name in registered_names:
        slot = devices.get(name, {'bpm': 0.0, 'quality': 0.0, 'state': ''})
        signal_active = slot['bpm'] > 0.0 or slot['state'] in worn_states
        participants.append({
            'id': name,
            'online': 1.0 if name in devices else 0.0,
            'state': slot['state'],
            'wearing': 1.0 if signal_active else 0.0,
            'bpm': slot['bpm'],
            'quality': slot['quality'],
        })
    return {
        'presence': presence,
        'pair': pair,
        'distance': distance,
        'has_distance': 1.0 if distances else 0.0,
        'proximity': proximity,
        'bpm': bpm,
        'hr_sync': hr_sync,
        'motion': motion,
        'host_interaction': host_interaction,
        'has_bpm': 1.0 if beats else 0.0,
        # The installation starts only when both named wearables have their
        # first independently valid numeric BPM in the same live window.
        'dual_bpm': 1.0 if all(
            devices.get(name, {}).get('bpm', 0.0) > 0.0
            for name in registered_names
        ) else 0.0,
        'participants': participants,
    }


def _demo_features(now):
    # About one minute from empty to full bloom, followed by 15 s breathing.
    elapsed = now % 75.0
    progress = _clamp((elapsed - 1.0) / 59.0)
    phase = elapsed / 75.0
    proximity = _clamp((phase - 0.16) * 1.75)
    return {
        'presence': 1.0 if phase > 0.04 else 0.0,
        'pair': 1.0 if phase > 0.20 else 0.0,
        'distance': 1800.0 - proximity * 1550.0,
            'proximity': proximity,
            'bpm': 74.0,
            'hr_sync': _clamp((phase - 0.34) * 2.2),
            'motion': 0.16,
            'host_interaction': 1.0 if phase > 0.52 else 0.0,
            'has_bpm': 1.0,
        '_progress': progress,
    }


def _update():
    control = op('/project1/adx_pointcloud_control')
    if control is None:
        return

    # The hardware launcher changes this token for every physical session.
    # It lets a collector restart begin a new plant without restarting or
    # focusing TouchDesigner.
    csv_path = str(control.par.Csvfile.eval())
    reset_path = os.path.join(os.path.dirname(csv_path), 'reset_lifecycle.txt')
    try:
        with open(reset_path, 'r', encoding='utf-8') as reset_stream:
            reset_token = reset_stream.read().strip()
    except OSError:
        reset_token = ''
    if STATE['reset_token'] is None:
        STATE['reset_token'] = reset_token
    elif reset_token != STATE['reset_token']:
        STATE['reset_token'] = reset_token
        STATE['progress'] = 0.0
        STATE['max_target'] = 0.0
        STATE['lifecycle_started'] = False
        STATE['hold'] = 0.0
        STATE['last_size'] = 0
        STATE['motion_lp'] = 0.0
        STATE['motion_burst'] = 0.0
        control.par.Progress = 0.0
        control.par.Spread = 0.0

    now = absTime.seconds
    wall_now = time.monotonic()
    if STATE['last_update'] <= 0.0:
        frame_dt = 0.0
    else:
        frame_dt = wall_now - STATE['last_update']
        if frame_dt <= 0.0:
            frame_dt = 0.0
        elif frame_dt > 2.0:
            frame_dt = 0.5
    # Lifecycle time comes from one monotonic wall clock.  TouchDesigner may
    # invoke callbacks more than once for the same frame; those duplicate
    # calls now contribute zero time instead of an invented 1/20 second.
    dt = min(frame_dt, 0.5)
    STATE['last_update'] = wall_now

    demo = bool(control.par.Demomode)
    manual = not bool(control.par.Livemode) and not demo
    just_started = False
    if demo:
        feat = _demo_features(now)
        target_progress = feat['_progress']
        live = True
    elif manual:
        feat = {
            'presence': 1.0, 'pair': 1.0, 'distance': 400.0,
            'proximity': 0.8, 'bpm': 66.0, 'hr_sync': 0.7,
            'motion': 0.0, 'host_interaction': 1.0, 'has_bpm': 0.0,
        }
        target_progress = float(control.par.Manualprogress)
        live = False
    else:
        path = str(control.par.Csvfile.eval())
        rows = _tail_rows(path)
        fresh_file = bool(path and os.path.isfile(path) and (time.time() - os.path.getmtime(path)) < 4.0)
        if not rows or not fresh_file:
            feat = {
                'presence': 0.0, 'pair': 0.0, 'distance': 0.0,
                'proximity': 0.0, 'bpm': 66.0, 'hr_sync': 0.0,
                'motion': 0.0, 'host_interaction': 0.0, 'has_bpm': 0.0,
                'dual_bpm': 0.0,
            }
            # A temporary CSV pause after the one-shot start edge freezes the
            # current plant; it must never collapse or restart from zero.
            target_progress = (
                STATE['progress'] if STATE['lifecycle_started'] else 0.0
            )
            live = False
        else:
            size = os.path.getsize(path)
            STATE['last_size'] = size
            feat = _features(rows)
            live = True
            # Before both people independently obtain their first numeric BPM,
            # the flower remains completely absent.  The first simultaneous
            # pair is the one-shot start edge for this session: reset exactly
            # to zero, then complete the whole biological lifecycle without
            # later signal dropouts making branches disappear or restarting.
            if not STATE['lifecycle_started']:
                target_progress = 0.0
                STATE['progress'] = 0.0
                STATE['max_target'] = 0.0
                if feat['dual_bpm'] > 0.5:
                    STATE['lifecycle_started'] = True
                    STATE['progress'] = 0.0
                    STATE['max_target'] = 1.0
                    STATE['hold'] = 0.0
                    STATE['motion_lp'] = 0.0
                    STATE['motion_burst'] = 0.0
                    target_progress = 1.0
                    just_started = True
            else:
                target_progress = 1.0
                STATE['max_target'] = 1.0

    current_progress = STATE['progress']
    if demo:
        current_progress = target_progress
    elif manual:
        current_progress = target_progress
    else:
        # One continuous roughly one-minute lifecycle begins at the dual-BPM
        # edge.  Signals modulate it gently, but cannot collapse a minute-long
        # growth into the old 12-second jump.
        growth_speed = 0.0
        if STATE['lifecycle_started']:
            growth_speed = 0.82
        if STATE['lifecycle_started'] and feat['pair'] > 0.5:
            growth_speed += 0.12 * feat['proximity'] + 0.08 * feat['hr_sync']
            if feat['host_interaction'] > 0.5:
                growth_speed += 0.05
            if feat['has_bpm'] > 0.5:
                growth_speed += 0.03
        growth_speed = _clamp(growth_speed, 0.0, 1.05)
        if just_started:
            current_progress = 0.0
        else:
            current_progress = min(
                target_progress,
                current_progress + frame_dt * growth_speed / 58.0,
            )
    current_progress = _clamp(current_progress)
    STATE['progress'] = current_progress

    # The reference asset contains five dense branches, so its reveal needs a
    # longer growth window than the old single-bouquet model.  This preserves
    # a readable stem/branch phase before the crown becomes dense.
    growth = _clamp(current_progress / 0.72)
    bloom = _clamp((current_progress - 0.44) / 0.40)
    # Breathing and interaction effects begin only after the entire plant has
    # reached full size.  Growth itself stays visually clean and directional.
    effects_ready = current_progress >= 0.99
    breathe = 1.0 if effects_ready else 0.0
    stage = 0.0
    if current_progress >= 0.99:
        stage = 4.0
    elif current_progress >= 0.84:
        stage = 3.0
    elif current_progress >= 0.44:
        stage = 2.0
    elif current_progress >= 0.18:
        stage = 1.0

    if effects_ready:
        alpha = min(1.0, dt / 0.30)
        STATE['motion_lp'] += (feat['motion'] - STATE['motion_lp']) * alpha
        motion = STATE['motion_lp']
        STATE['motion_burst'] *= math.exp(-frame_dt / 2.8)
        STATE['motion_burst'] = max(STATE['motion_burst'], motion)
        motion_burst = STATE['motion_burst']
    else:
        STATE['motion_lp'] = 0.0
        STATE['motion_burst'] = 0.0
        motion = 0.0
        motion_burst = 0.0
    tempo = _clamp(feat['bpm'] / 60.0, 0.55, 2.2)
    distance_spread = (
        _clamp((feat['distance'] - 380.0) / 1450.0)
        if feat['pair'] > 0.5 and feat['distance'] > 0.0
        else 0.0
    )
    # Distance produces a restrained, readable spread. A strong IMU wave can
    # briefly scatter much farther, then the decaying burst converges back to
    # the distance-defined point cloud in a few seconds.
    target_spread = (
        _clamp(0.02 + 0.32 * distance_spread + 0.82 * motion_burst, 0.0, 0.96)
        if effects_ready else 0.0
    )
    if manual:
        target_spread = float(control.par.Manualspread)
    current_spread = float(control.par.Spread)
    spread_rate = 5.0 if target_spread > current_spread else 1.25
    spread_alpha = 1.0 - math.exp(-spread_rate * frame_dt)
    current_spread += (target_spread - current_spread) * spread_alpha

    control.par.Progress = current_progress
    control.par.Targetprogress = target_progress
    control.par.Growth = growth
    control.par.Bloom = bloom
    control.par.Breathe = breathe
    control.par.Stage = stage
    control.par.Presence = feat['presence']
    control.par.Pair = feat['pair']
    control.par.Proximity = feat['proximity']
    control.par.Hrsync = feat['hr_sync']
    control.par.Spread = current_spread
    control.par.Targetspread = target_spread
    control.par.Tempo = tempo
    control.par.Motion = motion
    control.par.Motionburst = motion_burst
    control.par.Growthspeed = growth_speed if not demo and not manual else 1.0
    control.par.Distance = feat['distance']
    control.par.Live = 1.0 if live else 0.0


def onStart():
    control = op('/project1/adx_pointcloud_control')
    if control is not None:
        csv_path = str(control.par.Csvfile.eval())
        reset_path = os.path.join(os.path.dirname(csv_path), 'reset_lifecycle.txt')
        try:
            with open(reset_path, 'r', encoding='utf-8') as reset_stream:
                STATE['reset_token'] = reset_stream.read().strip()
        except OSError:
            STATE['reset_token'] = ''
        STATE['progress'] = 0.0
        STATE['max_target'] = 0.0
        STATE['lifecycle_started'] = False
        STATE['hold'] = 0.0
        STATE['motion_burst'] = 0.0
        STATE['last_update'] = 0.0
        control.par.Progress = 0.0
        control.par.Spread = 0.0
    return


def onFrameStart(frame):
    # 20 Hz is enough for a 10 Hz sensor feed and avoids reopening the CSV on
    # every render frame.
    if absTime.frame % 3 == 0:
        _update()
    return
'''


WEB_CALLBACKS = r'''import json
import time

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>AdventureX Emotional Flower</title>
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#000;color:#f8f6f9;font-family:Inter,"Microsoft YaHei",system-ui,sans-serif}
#flower{position:fixed;inset:0;width:100%;height:100%;object-fit:contain;background:#000}
#veil{position:fixed;inset:0;background:linear-gradient(90deg,transparent 48%,rgba(0,0,0,.18) 68%,rgba(0,0,0,.72));pointer-events:none}
#panel{position:fixed;right:24px;top:24px;bottom:24px;width:min(390px,30vw);padding:22px;border:1px solid rgba(255,255,255,.14);border-radius:24px;background:rgba(10,9,12,.72);backdrop-filter:blur(18px);box-shadow:0 18px 70px rgba(0,0,0,.45);overflow:auto}
.eyebrow{color:#d9a7c7;font-size:11px;letter-spacing:.18em;text-transform:uppercase}.title{margin:7px 0 18px;font-size:24px;font-weight:650}.status{display:flex;align-items:center;gap:8px;margin-bottom:18px;font-size:13px;color:#bdb7c1}.dot{width:8px;height:8px;border-radius:50%;background:#777;box-shadow:0 0 0 4px rgba(255,255,255,.05)}.dot.on{background:#7ff0bd;box-shadow:0 0 14px rgba(127,240,189,.7)}
.people{display:grid;grid-template-columns:1fr 1fr;gap:10px}.person,.metric{padding:14px;border:1px solid rgba(255,255,255,.10);border-radius:16px;background:rgba(255,255,255,.045)}.who{font-size:12px;color:#aaa4ad;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.bpm{font-size:28px;margin:6px 0 3px;font-variant-numeric:tabular-nums}.unit{font-size:11px;color:#a69fa8}.wear{font-size:12px;color:#f0a9d2}.wear.on{color:#7ff0bd}
.section{margin-top:18px}.labelrow{display:flex;justify-content:space-between;align-items:center;font-size:12px;color:#aaa4ad}.stage{color:#f3d0e5;font-size:14px}.track{height:6px;margin-top:9px;border-radius:8px;background:rgba(255,255,255,.09);overflow:hidden}.fill{height:100%;width:0;background:linear-gradient(90deg,#78ddb3,#ef93ca);border-radius:8px;transition:width .25s linear}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}.metric strong{display:block;margin-top:5px;font-size:18px;font-weight:560;font-variant-numeric:tabular-nums}.metric span{font-size:11px;color:#aaa4ad}.hint{margin-top:18px;color:#817b83;font-size:11px;line-height:1.55}.alert{color:#ffb3c4}
@media(max-width:820px){#veil{background:linear-gradient(0deg,rgba(0,0,0,.8),transparent 62%)}#panel{left:12px;right:12px;top:auto;bottom:12px;width:auto;max-height:46vh;padding:16px;border-radius:18px}.title{font-size:19px;margin-bottom:12px}.people{grid-template-columns:1fr 1fr}.bpm{font-size:23px}}
</style></head><body><img id="flower" alt="实时粒子花丛"><div id="veil"></div><aside id="panel">
<div class="eyebrow">AdventureX / Live</div><div class="title">双人情绪花丛</div><div class="status"><i id="liveDot" class="dot"></i><span id="liveText">正在连接采集器</span></div>
<div class="people"><div class="person"><div id="id1" class="who">佩戴者 1</div><div id="bpm1" class="bpm">--</div><div class="unit">BPM</div><div id="wear1" class="wear">等待佩戴</div></div><div class="person"><div id="id2" class="who">佩戴者 2</div><div id="bpm2" class="bpm">--</div><div class="unit">BPM</div><div id="wear2" class="wear">等待佩戴</div></div></div>
<div class="section"><div class="labelrow"><span>植物生命周期</span><b id="stage" class="stage">等待佩戴</b></div><div class="track"><div id="progress" class="fill"></div></div></div>
<div class="grid"><div class="metric"><span>两人距离</span><strong id="distance">--</strong></div><div class="metric"><span>互动状态</span><strong id="pair">等待</strong></div><div class="metric"><span>心率同步</span><strong id="sync">--</strong></div><div class="metric"><span>IMU 波动</span><strong id="motion">0%</strong></div><div class="metric"><span>生长速度</span><strong id="speed">0.00×</strong></div><div class="metric"><span>点云扩散</span><strong id="spread">0%</strong></div></div>
<div class="hint">两人佩戴稳定后，再双击启动文件。靠近与心率会温和调节生长速度；快速大幅挥手会让点云散开并自动聚拢。双击页面可切换全屏。</div></aside><script>
const $=id=>document.getElementById(id), img=$('flower'); let frameBusy=false;
const bpmDisplay=new Map(), BPM_HOLD_MS=4000;
async function frame(){if(frameBusy)return;frameBusy=true;try{const r=await fetch('/frame.jpg?t='+Date.now(),{cache:'no-store'});if(!r.ok)throw 0;const b=await r.blob(),u=URL.createObjectURL(b),old=img.src;img.src=u;if(old&&old.startsWith('blob:'))URL.revokeObjectURL(old)}catch(e){}frameBusy=false}
const stages=['等待佩戴','萌芽','枝叶生长','花冠开放','盛放呼吸'];
function participant(p,i){const key=p.id||('participant_'+i),now=Date.now();let held=bpmDisplay.get(key);if(p.bpm>0){held={value:p.bpm,at:now};bpmDisplay.set(key,held)}else if(held&&now-held.at>=BPM_HOLD_MS){bpmDisplay.delete(key);held=null}const shown=p.bpm>0?p.bpm:(held?held.value:0);$("id"+i).textContent=p.id||('佩戴者 '+i);$("bpm"+i).textContent=shown>0?Math.round(shown):'--';const w=$("wear"+i);if(!p.online){w.textContent='设备离线';w.className='wear alert'}else if(p.bpm>0||p.wearing){w.textContent='信号有效 · '+(p.state||'采集中');w.className='wear on'}else{w.textContent='在线 · '+(p.state||'等待有效心率');w.className='wear'}}
async function data(){try{const r=await fetch('/data.json?t='+Date.now(),{cache:'no-store'}),d=await r.json(),online=d.participants.filter(p=>p.online).length;$('liveDot').className='dot'+(d.live?' on':'');$('liveText').textContent=d.live?(online===2?'采集器在线 · 两个戒指已接入':'采集器在线 · 仅 '+online+'/2 戒指有数据'):'等待采集数据';participant(d.participants[0],1);participant(d.participants[1],2);$('stage').textContent=stages[Math.max(0,Math.min(4,Math.round(d.stage)))];$('progress').style.width=(d.progress*100).toFixed(1)+'%';$('distance').textContent=d.distance_valid?(d.distance/1000).toFixed(2)+' m':'--';$('pair').textContent=d.pair?'互动中':'等待';$('sync').textContent=d.pair?Math.round(d.hr_sync*100)+'%':'--';$('motion').textContent=Math.round(d.motion_burst*100)+'%';$('speed').textContent=d.growth_speed.toFixed(2)+'×';$('spread').textContent=Math.round(d.spread*100)+'%'}catch(e){$('liveDot').className='dot';$('liveText').textContent='前端正在重连'}}
setInterval(frame,80);setInterval(data,250);frame();data();document.body.addEventListener('dblclick',()=>document.fullscreenElement?document.exitFullscreen():document.documentElement.requestFullscreen());
</script></body></html>"""

def onHTTPRequest(webServerDAT, request, response):
    path=request['uri'].split('?')[0]
    response['statusCode']=200
    response['statusReason']='OK'
    response['Access-Control-Allow-Origin']='*'
    if path == '/frame.jpg':
        response['content-type']='image/jpeg'
        response['Cache-Control']='no-store'
        top=op('/project1/adx_web_out')
        try:
            response['data']=top.saveByteArray('.jpg', quality=0.90)
        except TypeError:
            response['data']=top.saveByteArray('.jpg')
        return response
    if path == '/data.json':
        control=op('/project1/adx_pointcloud_control')
        execute=op('/project1/adx_csv_execute')
        try:
            rows=execute.module._tail_rows(str(control.par.Csvfile.eval()))
            features=execute.module._features(rows) if rows else {'participants': []}
        except Exception:
            features={'participants': []}
        participants=list(features.get('participants') or [])[:2]
        while len(participants) < 2:
            fallback_id='person_01' if len(participants) == 0 else 'person_40'
            participants.append({'id':fallback_id,'online':0.0,'state':'','wearing':0.0,'bpm':0.0,'quality':0.0})
        payload={
            'time': time.time(),
            'live': bool(control.par.Live),
            'participants': participants,
            'progress': float(control.par.Progress),
            'target_progress': float(control.par.Targetprogress),
            'stage': float(control.par.Stage),
            'pair': bool(control.par.Pair),
            'distance_valid': bool(features.get('has_distance', 0.0)),
            'distance': float(features.get('distance', 0.0)) if features.get('has_distance', 0.0) else 0.0,
            'proximity': float(control.par.Proximity),
            'hr_sync': float(control.par.Hrsync),
            'motion': float(control.par.Motion),
            'motion_burst': float(control.par.Motionburst),
            'growth_speed': float(control.par.Growthspeed),
            'spread': float(control.par.Spread),
        }
        response['content-type']='application/json; charset=utf-8'
        response['Cache-Control']='no-store'
        response['data']=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        return response
    response['content-type']='text/html; charset=utf-8'
    response['data']=PAGE
    return response
'''


def _append_float(page, name, label, value, minimum=0.0, maximum=1.0):
    parameter = page.appendFloat(name, label=label)[0]
    parameter.default = value
    parameter.val = value
    parameter.min = minimum
    parameter.max = maximum
    parameter.clampMin = True
    parameter.clampMax = True
    return parameter


def build():
    root = op(ROOT_PATH)
    if root is None:
        raise RuntimeError("Reference project root /project1 was not found")

    required = {
        "pointtransform5", "pointtransform6", "pointtransform7", "pointtransform8", "pointtransform9",
        "col3", "col4", "col5", "col6", "col7", "box3", "box4", "box5", "box6",
    }
    missing = sorted(name for name in required if root.op(name) is None)
    if missing:
        raise RuntimeError("Reference flower network is incomplete: " + ", ".join(missing))

    for name in ("point_file_in3", "point_file_in4", "point_file_in5"):
        node = root.op(name)
        node.par.file = FLOWER_PLY
        node.par.red = "x"
        node.par.green = "y"
        node.par.blue = "z"
    for name in ("point_file_in6", "point_file_in7"):
        node = root.op(name)
        node.par.file = SUTSUKI_PLY
        node.par.red = "x"
        node.par.green = "y"
        node.par.blue = "z"

    # Only replace nodes owned by this builder; the user's reference network is
    # preserved as the source of truth.
    for child in list(root.children):
        if child.name.startswith("adx_live_") or child.name.startswith("adx_scatter_") or child.name in {
            "adx_pointcloud_control", "adx_csv_execute", "adx_flower_out", "adx_final_out",
            "adx_show", "adx_web_res", "adx_web_out", "adx_web_callbacks", "adx_web_server"
        }:
            try:
                child.destroy()
            except Exception:
                # A copied Geometry COMP can invalidate a stale Python OP
                # handle when its linked source is removed earlier in the same
                # pass.  The next fresh/copy call still replaces it by name.
                pass

    control = fresh(root, baseCOMP, "adx_pointcloud_control", 2850, -1150)
    page = control.appendCustomPage("Live Point Cloud")
    csv_par = page.appendFile("Csvfile", label="Live sensor CSV")[0]
    csv_par.default = CSV_PATH
    csv_par.val = CSV_PATH
    live = page.appendToggle("Livemode", label="CSV live mode")[0]
    live.default = True
    live.val = True
    demo = page.appendToggle("Demomode", label="Full lifecycle demo")[0]
    demo.default = False
    demo.val = False
    _append_float(page, "Manualprogress", "Manual lifecycle progress", 0.0, 0.0, 1.0)
    _append_float(page, "Manualspread", "Manual spread", 0.03, 0.0, 1.0)
    _append_float(page, "Progress", "Lifecycle progress", 0.0, 0.0, 1.0)
    _append_float(page, "Targetprogress", "CSV target progress", 0.0, 0.0, 1.0)
    _append_float(page, "Growth", "Growth", 0.0, 0.0, 1.0)
    _append_float(page, "Bloom", "Bloom", 0.0, 0.0, 1.0)
    _append_float(page, "Breathe", "Breathing", 0.0, 0.0, 1.0)
    _append_float(page, "Stage", "Lifecycle stage", 0.0, 0.0, 4.0)
    _append_float(page, "Presence", "People present", 0.0, 0.0, 1.0)
    _append_float(page, "Pair", "Two-person pair", 0.0, 0.0, 1.0)
    _append_float(page, "Proximity", "Pair proximity", 0.0, 0.0, 1.0)
    _append_float(page, "Hrsync", "Heart-rate sync", 0.0, 0.0, 1.0)
    _append_float(page, "Live", "CSV receiving", 0.0, 0.0, 1.0)
    _append_float(page, "Spread", "Smoothed spread", 0.0, 0.0, 1.0)
    _append_float(page, "Targetspread", "CSV target spread", 0.0, 0.0, 1.0)
    _append_float(page, "Tempo", "Pulse tempo", 1.0, 0.35, 2.5)
    _append_float(page, "Motion", "Motion energy", 0.0, 0.0, 1.0)
    _append_float(page, "Motionburst", "IMU scatter burst", 0.0, 0.0, 1.0)
    _append_float(page, "Growthspeed", "Signal growth speed", 0.0, 0.0, 1.15)
    _append_float(page, "Distance", "Distance mm", 0.0, 0.0, 5000.0)
    _append_float(page, "Glow", "Glow", 0.0, 0.0, 0.8)
    _append_float(page, "Camdistance", "Camera distance", 4.80, 2.0, 10.0)
    _append_float(page, "Camtargety", "Camera target height", 0.16, -2.0, 2.0)
    web_on = page.appendToggle("Webon", label="Black-background web output")[0]
    web_on.default = True
    web_on.val = True
    web_port = page.appendInt("Webport", label="Web port")[0]
    web_port.default = 9987
    web_port.val = 9987
    web_port.min = 1024
    web_port.max = 65535
    web_port.clampMin = True
    web_port.clampMax = True

    pixel = fresh(root, textDAT, "adx_scatter_pixel", 2850, -950)
    pixel.text = SCATTER_PIXEL

    groups = (
        ("pointtransform5", "col3", "box4", "geo8", 1.0),
        ("pointtransform6", "col4", "box5", "geo13", 2.0),
        ("pointtransform7", "col5", "box6", "geo15", 3.0),
        ("pointtransform8", "col6", "box3", "geo20", 4.0),
        ("pointtransform9", "col7", "box3", "geo21", 5.0),
    )

    material = fresh(root, constantMAT, "adx_live_point_mat", 3500, -1150)
    # The reference color textures are bright enough for PBR.  A lower
    # unlit multiplier preserves their pink/green detail instead of clipping
    # millions of overlapping point instances to white.
    # Keep the dense point cloud luminous without clipping the flower texture
    # to white once all five approved branches overlap.
    setpar(material, "colorr", 0.20)
    setpar(material, "colorg", 0.20)
    setpar(material, "colorb", 0.20)
    setpar(material, "alpha", 1.0)
    setpar(material, "blending", False)
    setpar(material, "depthtest", True)
    setpar(material, "depthwriting", True)
    setpar(material, "alphatest", False)

    geos = []
    for index, (position_name, color_name, box_name, reference_geo_name, seed) in enumerate(groups, start=1):
        scatter = fresh(root, glslTOP, "adx_scatter_%d" % index, 3100, -950 + (index - 1) * 130)
        wire(root.op(position_name), scatter)
        setpar(scatter, "glslversion", "glsl430")
        setpar(scatter, "pixeldat", pixel)
        setpar(scatter, "outputresolution", "useinput")
        setpar(scatter, "format", "rgba32float")
        setpar(scatter, "vec", 2)
        setpar(scatter, "vec0name", "uControl")
        setexpr(scatter, "vec0valuex", "op('/project1/adx_pointcloud_control').par.Spread")
        setexpr(scatter, "vec0valuey", "absTime.seconds")
        setexpr(scatter, "vec0valuez", "op('/project1/adx_pointcloud_control').par.Tempo")
        setpar(scatter, "vec0valuew", seed)
        setpar(scatter, "vec1name", "uLife")
        setexpr(scatter, "vec1valuex", "op('/project1/adx_pointcloud_control').par.Progress")
        setexpr(scatter, "vec1valuey", "op('/project1/adx_pointcloud_control').par.Growth")
        setexpr(scatter, "vec1valuez", "op('/project1/adx_pointcloud_control').par.Bloom")
        setexpr(scatter, "vec1valuew", "op('/project1/adx_pointcloud_control').par.Breathe")

        geo_name = "adx_live_geo_%d" % index
        old_geo = root.op(geo_name)
        if old_geo is not None:
            old_geo.destroy()
        # Copying the approved reference Geometry COMP preserves its SOP input
        # connector and point primitive.  A newly created Geometry COMP has no
        # external SOP connector in this TD build.
        geo = root.copy(root.op(reference_geo_name), name=geo_name)
        geo.nodeX = 3500
        geo.nodeY = -900 + (index - 1) * 150
        setpar(geo, "instancing", True)
        setpar(geo, "instancetop", scatter)
        setpar(geo, "instancetx", "r")
        setpar(geo, "instancety", "g")
        setpar(geo, "instancetz", "b")
        setpar(geo, "instancesop", scatter)
        setpar(geo, "instancesx", "a")
        setpar(geo, "instancesy", "a")
        setpar(geo, "instancesz", "a")
        setpar(geo, "instancecolorop", root.op(color_name))
        setpar(geo, "instancer", "r")
        setpar(geo, "instanceg", "g")
        setpar(geo, "instanceb", "b")
        setpar(geo, "instancea", "a")
        setpar(geo, "material", material)
        geo.display = True
        geo.render = True

        # The fifth source intentionally keeps the reference TOE's secondary
        # branch transform.  It is part of the approved rich bouquet layout.
        if reference_geo_name == "geo21":
            reference_geo = root.op(reference_geo_name)
            for par_name in ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"):
                getattr(geo.par, par_name).val = getattr(reference_geo.par, par_name).eval()

        # The reference point transforms contain the right individual flowers,
        # but viewed together their branch roots are laid out in a wide arc.
        # These fixed offsets form one intentional bouquet; the live GLSL then
        # expands and gathers points relative to this stable arrangement.
        bouquet_offsets = {
            1: (1.65, -0.05, -0.75),
            2: (0.00,  0.00,  0.05),
            3: (0.00,  0.05, -0.65),
            4: (-1.05, -0.05, 0.55),
        }
        if index in bouquet_offsets:
            geo.par.tx, geo.par.ty, geo.par.tz = bouquet_offsets[index]
        geos.append(geo)

    target = fresh(root, nullCOMP, "adx_live_camera_target", 3850, -650)
    target.par.tx = -1.36
    setexpr(target, "ty", "op('/project1/adx_pointcloud_control').par.Camtargety")
    target.par.tz = 0.48

    camera = fresh(root, cameraCOMP, "adx_live_camera", 3850, -850)
    # Look mostly along the reference bouquet's X axis.  From a frontal Z-axis
    # view its depth layers look like unrelated flowers placed side by side;
    # this oblique view restores the approved, overlapping flower cluster.
    setexpr(camera, "tx", "-1.36 - op('/project1/adx_pointcloud_control').par.Camdistance")
    camera.par.ty = 1.50
    camera.par.tz = -1.30
    camera.par.lookat = target
    camera.par.fov = 40.0
    setpar(camera, "bgcolorr", 0.0)
    setpar(camera, "bgcolorg", 0.0)
    setpar(camera, "bgcolorb", 0.0)
    setpar(camera, "bgcolora", 1.0)

    render = fresh(root, renderTOP, "adx_live_render", 4100, -850)
    setpar(render, "camera", camera)
    setpar(render, "geometry", " ".join(geo.path for geo in geos))
    setpar(render, "lights", "")
    setpar(render, "outputresolution", "custom")
    setpar(render, "resolutionw", 1080)
    setpar(render, "resolutionh", 1920)
    setpar(render, "format", "rgba16float")
    setpar(render, "antialias", "aa8high")
    setpar(render, "transparency", False)

    sharp = fresh(root, nullTOP, "adx_live_sharp", 4300, -850)
    wire(render, sharp)

    threshold = fresh(root, levelTOP, "adx_live_glow_threshold", 4300, -1050)
    wire(sharp, threshold)
    setpar(threshold, "blacklevel", 0.52)
    setpar(threshold, "gamma1", 1.55)

    blur = fresh(root, blurTOP, "adx_live_glow_blur", 4500, -1050)
    wire(threshold, blur)
    setpar(blur, "size", 28)

    glow = fresh(root, levelTOP, "adx_live_glow_level", 4700, -1050)
    wire(blur, glow)
    setexpr(glow, "opacity", "op('/project1/adx_pointcloud_control').par.Glow")

    combined = fresh(root, addTOP, "adx_live_combined", 4700, -850)
    wire(sharp, combined, 0)
    wire(glow, combined, 1)

    output = fresh(root, nullTOP, "adx_flower_out", 4900, -850)
    wire(combined, output)
    setpar(output, "format", "rgba8fixed")
    output.viewer = True

    final_out = fresh(root, outTOP, "adx_final_out", 5100, -850)
    wire(output, final_out)
    final_out.viewer = True

    # Clean presentation outputs.  The Window COMP is prepared but not opened
    # here, so rebuilding never steals focus.  Its content is only the final
    # flower TOP on black, with no TouchDesigner controls or network UI.
    show = fresh(root, windowCOMP, "adx_show", 5100, -1100)
    setpar(show, "winop", output)
    setpar(show, "title", "AdventureX Emotional Flower")
    setpar(show, "borders", False)
    setpar(show, "cursorvisible", "nocursor")
    setpar(show, "drawwindow", True)
    setpar(show, "size", "fill")
    setpar(show, "justifyh", "center")
    setpar(show, "justifyv", "center")
    setpar(show, "alwaysontop", False)
    setpar(show, "ignoretaskbar", True)
    setpar(show, "closeescape", True)
    setpar(show, "interact", False)

    web_res = fresh(root, resolutionTOP, "adx_web_res", 5100, -650)
    wire(output, web_res)
    setpar(web_res, "outputresolution", "custom")
    setpar(web_res, "resolutionw", 900)
    setpar(web_res, "resolutionh", 1600)
    setpar(web_res, "highqualresize", True)
    web_out = fresh(root, nullTOP, "adx_web_out", 5300, -650)
    wire(web_res, web_out)
    setpar(web_out, "format", "rgba8fixed")
    web_callbacks = fresh(root, textDAT, "adx_web_callbacks", 5100, -450)
    web_callbacks.text = WEB_CALLBACKS
    web_server = fresh(root, webserverDAT, "adx_web_server", 5300, -450)
    setpar(web_server, "callbacks", web_callbacks)
    setexpr(web_server, "port", "op('/project1/adx_pointcloud_control').par.Webport")
    setexpr(web_server, "active", "1 if op('/project1/adx_pointcloud_control').par.Webon else 0")

    execute = fresh(root, executeDAT, "adx_csv_execute", 2850, -750)
    execute.text = CSV_EXECUTE
    setpar(execute, "start", True)
    setpar(execute, "framestart", True)
    setpar(execute, "frame", True)

    # Force the source and render chain to cook once after asynchronous PLY
    # loading.  The execute DAT continues updating the CSV controls afterward.
    for name in ("point_file_in3", "point_file_in4", "point_file_in5", "point_file_in6", "point_file_in7"):
        root.op(name).cook(force=True)
    for scatter in [root.op("adx_scatter_%d" % index) for index in range(1, 6)]:
        scatter.cook(force=True)
    render.cook(force=True)
    output.cook(force=True)

    return {
        "control": control.path,
        "output": output.path,
        "render": render.path,
        "geometries": [geo.path for geo in geos],
        "shader_errors": {root.op("adx_scatter_%d" % i).path: list(root.op("adx_scatter_%d" % i).errors()) for i in range(1, 6)},
        "render_errors": list(render.errors()),
    }


BUILD_RESULT = build()
