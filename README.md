# WeBloom

WeBloom connects two wearable sensor nodes to a living point-cloud flower garden. The repository contains both the public web experience and the physical interactive installation.

## Repository layout

- `public/`, `assets/`, `server.js`: the existing WeBloom website.
- `installation/hardware/`: ESP32 gateway/node firmware, T5AI-Core audio firmware, and the Python collector.
- `installation/touchdesigner/`: the production TouchDesigner project, point-cloud assets, WebGL assets, and rebuild scripts.
- `installation/launchers/`: Windows launchers for real hardware, simulation, and CSV replay.
- `installation/docs/`: detailed project and deployment notes.

See [`installation/README.md`](installation/README.md) for the live sensor-to-flower and audio recording workflow.

Runtime sensor CSV files, recordings, logs, browser profiles, backups, and local archives are intentionally excluded from Git.
