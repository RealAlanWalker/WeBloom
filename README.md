# WeBloom

WeBloom explores a simple idea: technology should help people notice time spent together, not compete for their attention. Two wearable sensor nodes shape a living flower garden; when both people choose to keep a moment, the web experience can create a privacy-preserving shared proof on Injective.

The readable memory remains with its participants. The transaction calldata contains only a salted fingerprint, a flower seed, and a timestamp; the public wallet addresses and receipt verify that one participant sent the proof to the other participant's wallet.

## Repository layout

- `public/`, `assets/`, `server.js`: the existing WeBloom website.
- `web_pitch/WeBloom-Zeabur/`: the standalone shared-memory web experience, including the twin-flower generator and Injective EVM testnet proof flow.
- `installation/hardware/`: ESP32 gateway/node firmware, T5AI-Core audio firmware, and the Python collector.
- `installation/touchdesigner/`: the production TouchDesigner project, point-cloud assets, WebGL assets, and rebuild scripts.
- `installation/launchers/`: Windows launchers for real hardware, simulation, and CSV replay.
- `installation/docs/`: detailed project and deployment notes.

See [`installation/README.md`](installation/README.md) for the live sensor-to-flower and audio recording workflow.

See [`web_pitch/WeBloom-Zeabur/README.md`](web_pitch/WeBloom-Zeabur/README.md) for the shared-memory experience, privacy model, Injective transaction design, and Zeabur deployment notes.

Runtime sensor CSV files, recordings, logs, browser profiles, backups, and local archives are intentionally excluded from Git.
