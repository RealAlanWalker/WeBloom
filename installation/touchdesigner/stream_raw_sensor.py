"""把「原始手环 CSV」持续追加写入 live 目录，供 TouchDesigner 实时尾随读取。

字段与真实网关导出的 sensor_data_*.csv 完全一致（同一套表头），
只填 TD 那一侧真正会用到的列：
    row_received_at / device_id / bpm / heart_rate_state / heart_rate_quality
    distance_mm / distance_valid / within_one_meter / zone_name
    ranging_rssi_filtered_dbm / ble_rssi_dbm / imu_gyro_*_dps_*

用法:
    # 模拟两个人从远到近、心率逐渐同步的一整段交互（循环）
    python stream_raw_sensor.py

    # 回放你自己导出的真实 CSV（按 row_received_at 的原始节奏重放）
    python stream_raw_sensor.py --replay "D:\\path\\sensor_data_20260725_003324.csv"

    # 换输出位置 / 换节奏
    python stream_raw_sensor.py --out "E:\\...\\sensor_live.csv" --hz 10
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import random
import time
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))

HEADER = (
    "row_received_at,device_id,source_mac,pair_delta_ms,ppg_present,ppg_received_at,"
    "ppg_packet_seq,ppg_first_sample_seq,ppg_first_sample_timestamp_ms,ppg_sample_rate_hz,"
    "ppg_sample_count,ppg_red_0,ppg_ir_0,ppg_red_1,ppg_ir_1,ppg_red_2,ppg_ir_2,ppg_red_3,"
    "ppg_ir_3,ppg_red_4,ppg_ir_4,ppg_red_5,ppg_ir_5,ppg_red_6,ppg_ir_6,ppg_red_7,ppg_ir_7,"
    "ppg_red_8,ppg_ir_8,ppg_red_9,ppg_ir_9,context_present,context_received_at,"
    "context_packet_seq,context_first_sample_seq,context_first_sample_timestamp_ms,"
    "imu_sample_rate_hz,imu_sample_count,imu_valid,"
    "imu_accel_x_raw_0,imu_accel_y_raw_0,imu_accel_z_raw_0,imu_gyro_x_raw_0,imu_gyro_y_raw_0,imu_gyro_z_raw_0,"
    "imu_accel_x_raw_1,imu_accel_y_raw_1,imu_accel_z_raw_1,imu_gyro_x_raw_1,imu_gyro_y_raw_1,imu_gyro_z_raw_1,"
    "imu_accel_x_raw_2,imu_accel_y_raw_2,imu_accel_z_raw_2,imu_gyro_x_raw_2,imu_gyro_y_raw_2,imu_gyro_z_raw_2,"
    "imu_accel_x_raw_3,imu_accel_y_raw_3,imu_accel_z_raw_3,imu_gyro_x_raw_3,imu_gyro_y_raw_3,imu_gyro_z_raw_3,"
    "imu_accel_x_raw_4,imu_accel_y_raw_4,imu_accel_z_raw_4,imu_gyro_x_raw_4,imu_gyro_y_raw_4,imu_gyro_z_raw_4,"
    "imu_accel_x_g_0,imu_accel_y_g_0,imu_accel_z_g_0,imu_gyro_x_dps_0,imu_gyro_y_dps_0,imu_gyro_z_dps_0,"
    "imu_accel_x_g_1,imu_accel_y_g_1,imu_accel_z_g_1,imu_gyro_x_dps_1,imu_gyro_y_dps_1,imu_gyro_z_dps_1,"
    "imu_accel_x_g_2,imu_accel_y_g_2,imu_accel_z_g_2,imu_gyro_x_dps_2,imu_gyro_y_dps_2,imu_gyro_z_dps_2,"
    "imu_accel_x_g_3,imu_accel_y_g_3,imu_accel_z_g_3,imu_gyro_x_dps_3,imu_gyro_y_dps_3,imu_gyro_z_dps_3,"
    "imu_accel_x_g_4,imu_accel_y_g_4,imu_accel_z_g_4,imu_gyro_x_dps_4,imu_gyro_y_dps_4,imu_gyro_z_dps_4,"
    "distance_mm,distance_age_ms,distance_valid,distance_extrapolated,distance_source,ble_rssi_dbm,"
    "ranging_peer_device_id,ranging_rssi_raw_dbm,ranging_rssi_filtered_dbm,ranging_tx_power_qdbm,"
    "ranging_config_version,clock_synced,context_flags,zone_state,zone_name,within_one_meter,"
    "range_trend_state,range_trend_name,range_trend_delta_db,heart_rate_updated,"
    "heart_rate_window_end_sample_seq,heart_rate_window_end_timestamp_ms,bpm,heart_rate_state,"
    "heart_rate_quality,heart_rate_periodicity,heart_rate_signal_rms,ppg_packet_loss_rate,"
    "sample_missing_rate,max_gap_ms,context_gap_rate,ppg_gateway_timestamp_ms,"
    "ppg_gateway_received_packets,ppg_invalid_packets,ppg_queue_overflows,"
    "context_gateway_timestamp_ms,context_gateway_received_packets,context_invalid_packets,"
    "context_queue_overflows"
)

COLUMNS = HEADER.split(",")

DEVICES = [
    {"id": "person_01", "mac": "44:B1:76:01:D7:C8", "base_bpm": 82.0, "seq": 24150},
    {"id": "person_40", "mac": "44:B1:76:08:4B:E8", "base_bpm": 74.0, "seq": 629140},
]

# --------------------------------------------------------------------------- 剧本
# (阶段名, 时长秒, 距离 mm 起, 距离 mm 止, 人数, 心率贴合度 0-1, 体动 0-1)
SCRIPT = [
    ("idle",      6.0, 2400, 2400, 0, 0.0, 0.05),
    ("arrive_a", 10.0, 2200, 1500, 1, 0.0, 0.30),
    ("arrive_b", 10.0, 1500,  950, 2, 0.15, 0.35),
    ("approach", 12.0,  950,  420, 2, 0.45, 0.28),
    ("close",    12.0,  420,  240, 2, 0.80, 0.18),
    ("hold",     26.0,  260,  220, 2, 0.95, 0.10),
    ("part",     12.0,  260, 1600, 2, 0.40, 0.35),
    ("leave",     8.0, 1600, 2400, 0, 0.0, 0.08),
]
TOTAL = sum(step[1] for step in SCRIPT)


def zone_for(mm: float) -> tuple[int, str, int]:
    if mm <= 400:
        return 3, "intimate", 1
    if mm <= 1000:
        return 2, "near", 1
    if mm <= 2000:
        return 1, "social", 0
    return 0, "far", 0


def script_at(t: float) -> dict:
    t = t % TOTAL
    for name, span, d0, d1, people, sync, motion in SCRIPT:
        if t < span:
            k = t / span
            ease = k * k * (3.0 - 2.0 * k)
            return {
                "phase": name,
                "distance": d0 + (d1 - d0) * ease,
                "people": people,
                "sync": sync * (0.35 + 0.65 * ease) if sync else 0.0,
                "motion": motion,
                "k": k,
            }
        t -= span
    return {"phase": "idle", "distance": 2400, "people": 0, "sync": 0.0,
            "motion": 0.05, "k": 0.0}


def build_row(device: dict, state: dict, now: datetime, index: int,
              contact: bool, bpm: float) -> dict:
    row = {name: "" for name in COLUMNS}
    stamp = now.isoformat(timespec="milliseconds")
    mm = state["distance"]
    zone_state, zone_name, within = zone_for(mm)
    rssi = -42.0 - 26.0 * min(1.0, mm / 2400.0) - random.uniform(0.0, 2.0)

    device["seq"] += 10
    row["row_received_at"] = stamp
    row["device_id"] = device["id"]
    row["source_mac"] = device["mac"]
    row["ppg_present"] = "1"
    row["ppg_received_at"] = stamp
    row["ppg_packet_seq"] = str(device["seq"] // 10)
    row["ppg_first_sample_seq"] = str(device["seq"])
    row["ppg_sample_rate_hz"] = "100"
    row["ppg_sample_count"] = "10"

    # PPG 波形：有接触时是明显的脉搏波，没接触就是平的直流
    for i in range(10):
        if contact:
            beat = math.sin(2.0 * math.pi * (bpm / 60.0) * (index * 0.1 + i * 0.01))
            red = 3400 + beat * 90 + random.uniform(-6, 6)
            ir = 3450 + beat * 84 + random.uniform(-6, 6)
        else:
            red = 910 + random.uniform(-3, 3)
            ir = 826 + random.uniform(-3, 3)
        row["ppg_red_%d" % i] = "%d" % red
        row["ppg_ir_%d" % i] = "%d" % ir

    # IMU：体动强度决定陀螺仪能量
    row["context_present"] = "1"
    row["context_received_at"] = stamp
    row["imu_sample_rate_hz"] = "50"
    row["imu_sample_count"] = "5"
    row["imu_valid"] = "1"
    energy = state["motion"] * 120.0
    for i in range(5):
        for axis in ("x", "y", "z"):
            row["imu_gyro_%s_dps_%d" % (axis, i)] = "%.4f" % (
                random.gauss(0.0, energy * 0.55))
        row["imu_accel_x_g_%d" % i] = "%.4f" % random.gauss(0.0, 0.04)
        row["imu_accel_y_g_%d" % i] = "%.4f" % random.gauss(-0.98, 0.04)
        row["imu_accel_z_g_%d" % i] = "%.4f" % random.gauss(0.05, 0.04)

    # 测距 / 分区
    row["distance_mm"] = "%d" % mm
    row["distance_age_ms"] = "%d" % random.randint(20, 90)
    row["distance_valid"] = "1" if state["people"] >= 2 else "0"
    row["distance_extrapolated"] = "0"
    row["distance_source"] = "uwb"
    row["ble_rssi_dbm"] = "%.1f" % rssi
    row["ranging_rssi_raw_dbm"] = "%.1f" % (rssi - random.uniform(0, 3))
    row["ranging_rssi_filtered_dbm"] = "%.1f" % rssi
    row["clock_synced"] = "1"
    row["zone_state"] = str(zone_state)
    row["zone_name"] = zone_name
    row["within_one_meter"] = str(within if state["people"] >= 2 else 0)
    row["range_trend_name"] = "approaching" if state["k"] < 0.9 else "steady"

    # 心率
    row["heart_rate_updated"] = "1"
    row["heart_rate_window_end_sample_seq"] = str(device["seq"] + 9)
    if contact:
        row["bpm"] = "%.1f" % bpm
        row["heart_rate_state"] = "locked"
        row["heart_rate_quality"] = "%.4f" % random.uniform(0.55, 0.92)
        row["heart_rate_periodicity"] = "%.4f" % random.uniform(0.6, 0.95)
        row["heart_rate_signal_rms"] = "%.4f" % random.uniform(40, 90)
    else:
        row["heart_rate_state"] = "no_contact"
        row["heart_rate_quality"] = "0.0000"
        row["heart_rate_periodicity"] = "0.0000"
        row["heart_rate_signal_rms"] = "0.0000"
    row["ppg_packet_loss_rate"] = "0.0000"
    row["sample_missing_rate"] = "0.0000"
    row["max_gap_ms"] = "0"
    return row


def ensure_header(path: str, reset: bool) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if reset or not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", encoding="utf-8", newline="") as stream:
            stream.write(HEADER + "\n")


def trim(path: str, keep: int) -> None:
    """别让文件无限长 —— 只留最近 keep 行。"""
    with open(path, "r", encoding="utf-8", newline="") as stream:
        lines = stream.readlines()
    if len(lines) <= keep + 1:
        return
    with open(path, "w", encoding="utf-8", newline="") as stream:
        stream.write(lines[0])
        stream.writelines(lines[-keep:])


def run_simulation(path: str, hz: float, keep: int, speed: float = 1.0) -> None:
    ensure_header(path, reset=True)
    period = 1.0 / hz
    start = time.time()
    index = 0
    last_phase = None
    while True:
        t = time.time() - start
        state = script_at(t * speed)
        if state["phase"] != last_phase:
            last_phase = state["phase"]
            print("[%6.1fs] %-9s distance=%4dmm people=%d" % (
                t, state["phase"], int(state["distance"]), state["people"]))

        now = datetime.now(TZ)
        rows = []
        for slot, device in enumerate(DEVICES):
            present = state["people"] > slot
            base = device["base_bpm"]
            partner = DEVICES[1 - slot]["base_bpm"]
            bpm = base + (partner - base) * state["sync"] * 0.5
            bpm += math.sin(t * 0.23 + slot) * 2.0 + random.uniform(-0.8, 0.8)
            rows.append(build_row(device, state, now, index, present, bpm))
            now = now + timedelta(milliseconds=8)

        with open(path, "a", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=COLUMNS,
                                    extrasaction="ignore", lineterminator="\n")
            for row in rows:
                writer.writerow(row)

        index += 1
        if index % 200 == 0:
            trim(path, keep)
        time.sleep(max(0.0, period - (time.time() - start - t)))


def run_replay(source: str, path: str, speed: float, keep: int) -> None:
    with open(source, "r", encoding="utf-8-sig", newline="") as stream:
        reader = list(csv.reader(stream))
    header = reader[0]
    body = [r for r in reader[1:] if len(r) == len(header)]
    if not body:
        raise SystemExit("源 CSV 没有可用数据行")
    stamp_idx = header.index("row_received_at") if "row_received_at" in header else 0

    def when(row):
        try:
            return datetime.fromisoformat(row[stamp_idx]).timestamp()
        except Exception:
            return None

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as stream:
        csv.writer(stream, lineterminator="\n").writerow(header)

    print("回放 %d 行 -> %s (速度 x%.1f, 循环)" % (len(body), path, speed))
    while True:
        prev = None
        for row in body:
            stamp = when(row)
            if prev is not None and stamp is not None:
                gap = (stamp - prev) / speed
                if 0 < gap < 5.0:
                    time.sleep(gap)
            if stamp is not None:
                prev = stamp
            out = list(row)
            out[stamp_idx] = datetime.now(TZ).isoformat(timespec="milliseconds")
            with open(path, "a", encoding="utf-8", newline="") as stream:
                csv.writer(stream, lineterminator="\n").writerow(out)
        trim(path, keep)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        default=r"E:\AdventureX\outputs\ADX_Flower_PointCloud\live\sensor_live.csv")
    parser.add_argument("--replay", default="", help="回放一个真实导出的 CSV")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--hz", type=float, default=10.0)
    parser.add_argument("--keep", type=int, default=600)
    parser.add_argument("--pidfile", default="", help="启动后把自己的 PID 写到这里")
    args = parser.parse_args()

    print("写入 ->", args.out)
    # 把 PID 写下来，方便调用方只杀掉「上一个信号源」，
    # 而不是粗暴地 taskkill 所有 python.exe（那会连别人的进程一起杀）。
    if args.pidfile:
        try:
            os.makedirs(os.path.dirname(args.pidfile), exist_ok=True)
            with open(args.pidfile, "w", encoding="utf-8") as stream:
                stream.write(str(os.getpid()))
        except OSError as exc:
            print("写 pidfile 失败:", exc)
    try:
        if args.replay:
            run_replay(args.replay, args.out, args.speed, args.keep)
        else:
            run_simulation(args.out, args.hz, args.keep, args.speed)
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
