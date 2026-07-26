# WeBloom shared memory proof

WeBloom begins with a human question: can technology help two people notice that a moment mattered to both of them without turning that moment into another feed, score, or public archive?

The standalone web experience turns that idea into a complete flow. Two people write one memory, generate a pair of related flowers, and may anchor a privacy-preserving fingerprint of the moment on Injective EVM Testnet.

## Experience

1. Two participants enter their names, one partner testnet wallet, and a short shared memory.
2. The browser creates a cryptographically random salt and a salted SHA-256 fingerprint locally.
3. The fingerprint deterministically shapes two related flowers, one for each participant.
4. After the sender accepts the privacy notice and connects a wallet, a zero-value transaction sends the public proof to the partner address on Injective EVM Testnet.
5. Both participants can verify the transaction receipt in Blockscout and download a private capsule for their own records.

## Why Injective

Injective is used as a shared witness, not as a public memory database. Its EVM testnet gives the project a fast, low-cost receipt that neither participant can later alter. The receipt answers a narrow question: did one wallet send this fingerprint to the other wallet at this time?

This division of responsibility is intentional:

- the wearable installation senses a real encounter;
- the local experience turns that encounter into two flowers;
- the participants keep the readable memory;
- Injective keeps the verifiable receipt.

## Privacy boundary

Before a transaction, the following data stays in the browser:

- both participant names;
- the readable memory;
- the random verification salt.

The transaction calldata contains only the public proof:

- protocol identifier;
- privacy scheme (`salted-sha256`);
- salted memory fingerprint;
- flower seed;
- creation timestamp.

Wallet addresses, transaction metadata, and calldata are public and permanent once submitted. The interface states this before enabling the transaction. The downloaded capsule contains the readable memory and salt, so it must be kept private.

The current prototype records one sender transaction to a partner wallet. It does not collect a second cryptographic signature from the partner, so the receipt proves delivery between the two addresses rather than independent approval by both wallets.

## Network

- Network: Injective EVM Testnet
- Chain ID: `1439` (`0x59f`)
- Transaction value: `0 INJ`
- Explorer: `https://testnet.blockscout.injective.network/`

## Run locally

Serve this directory with any static HTTP server. For example:

```bash
python -m http.server 4174 --bind 127.0.0.1
```

Then open `http://127.0.0.1:4174/`.

Wallet and cryptographic features should be tested from a secure context. A production deployment must use HTTPS.

## Deploy on Zeabur

Deploy the repository as a static site and set the service root directory to:

```text
web_pitch/WeBloom-Zeabur
```

No build command, API key, private key, or server-side secret is required.
