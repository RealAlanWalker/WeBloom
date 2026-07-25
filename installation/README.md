# WeBloom interactive installation

This directory is the reproducible physical installation used for the two-person emotional flower experience.

## End-to-end flow

```text
person_01 ESP32-C3 ─┐
                    ├─ ESP-NOW → ESP32-S3 gateway → Python collector → live sensor CSV
person_40 ESP32-C3 ─┘                                      │
                                                          ├─ TouchDesigner lifecycle and point-cloud animation
T5AI-Core microphone → independent USB PCM → WAV recorder ┘
```

The two wearable nodes produce PPG, distance/RSSI, and IMU data. The flower starts from zero after both people produce usable heart-rate signals, grows upward from roots to crown, blooms sequentially, and then breathes. Distance and heart-rate synchronization regulate growth; a strong IMU gesture scatters the point cloud before it converges again.

T5AI-Core is an independent USB audio branch. Entering the two-way interaction zone starts a recording and leaving it stops the recording. Completed WAV files and `audio_events.csv` are written under the local runtime `live/audio/` directory and are not committed.

## Main files

- `touchdesigner/ADX_Flower_PointCloud.toe`: production TouchDesigner project.
- `touchdesigner/build_reference_flower_csv.py`: rebuild/configuration script.
- `touchdesigner/assets/reference/`: full flower point-cloud sources.
- `hardware/desktop/collector.py`: gateway collector and lifecycle signal source.
- `hardware/desktop/t5_audio.py`: T5 binary PCM parser and atomic WAV writer.
- `hardware/desktop/start_flower_link.ps1`: combined gateway, audio, CSV, and TouchDesigner launcher.
- `hardware/firmware/`: node, gateway, diagnostics, and T5 firmware.

## Running on the installation PC

The production launcher currently targets the installation layout at `E:\AdventureX`. If the repository is cloned elsewhere, update the paths at the top of `hardware/desktop/start_flower_link.ps1` and the Windows launchers.

With the S3 gateway, two C3 nodes, and optional T5AI-Core connected, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware\desktop\start_flower_link.ps1
```

The launcher detects the S3 gateway and T5 `SERIAL-B` USB interface, resets the flower lifecycle, starts one hidden collector, and writes fresh runtime data to the local `live/` directory. If T5 is absent, sensor collection and the flower continue without audio.

## Validation

The integrated hardware session was verified with both `person_01` and `person_40` writing live rows, T5 recording 16 kHz mono 16-bit PCM, and all nine T5 protocol tests passing. Runtime evidence is kept in the local project archive rather than this public repository.
