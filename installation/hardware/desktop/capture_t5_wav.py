"""Capture a button-triggered T5AI-Core recording from its log UART."""

import argparse
import re
import time
import wave
from pathlib import Path

import serial


BEGIN_RE = re.compile(
    rb"AUDIO_BEGIN bytes=(\d+) sample_rate=(\d+) bits=(\d+) channels=(\d+)"
)
DATA_RE = re.compile(rb"AUDIO_DATA offset=(\d+) hex=([0-9A-F]+)")
END_RE = re.compile(rb"AUDIO_END bytes=(\d+)")


def capture(port: str, output: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    expected_bytes = None
    sample_rate = sample_bits = channels = None
    pcm = bytearray()

    with serial.Serial(port, 460800, timeout=0.25) as uart:
        uart.reset_input_buffer()
        print("接收器已启动，请按 T5AI-Core 板载按钮。")
        while time.monotonic() < deadline:
            line = uart.readline()
            if not line:
                continue
            begin = BEGIN_RE.search(line)
            if begin:
                expected_bytes, sample_rate, sample_bits, channels = map(
                    int, begin.groups()
                )
                pcm = bytearray(expected_bytes)
                print(f"开始接收 {expected_bytes} 字节 PCM。")
                continue

            data = DATA_RE.search(line)
            if data and expected_bytes is not None:
                offset = int(data.group(1))
                chunk = bytes.fromhex(data.group(2).decode("ascii"))
                if offset + len(chunk) > expected_bytes:
                    raise RuntimeError("音频块超出声明长度")
                pcm[offset : offset + len(chunk)] = chunk
                continue

            end = END_RE.search(line)
            if end and expected_bytes is not None:
                if int(end.group(1)) != expected_bytes:
                    raise RuntimeError("结束标记长度与开始标记不一致")
                output.parent.mkdir(parents=True, exist_ok=True)
                with wave.open(str(output), "wb") as wav_file:
                    wav_file.setnchannels(channels)
                    wav_file.setsampwidth(sample_bits // 8)
                    wav_file.setframerate(sample_rate)
                    wav_file.writeframes(pcm)
                print(f"已保存 {output}，{len(pcm)} 字节。")
                return

    raise TimeoutError(f"等待音频超时（{timeout:g} 秒）")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM11")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    output = args.output or Path("data") / time.strftime(
        "t5_button_recording_%Y%m%d_%H%M%S.wav"
    )
    capture(args.port, output, args.timeout)


if __name__ == "__main__":
    main()
