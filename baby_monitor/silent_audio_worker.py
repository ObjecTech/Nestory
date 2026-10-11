"""Real-time, no-output player for testing controls without audio hardware."""
from __future__ import annotations

import argparse
import sys
import threading
import wave
from pathlib import Path


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an audio track without an output device")
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--start-seconds", type=float, default=0.0)
    args = parser.parse_args()
    if not args.file.is_file():
        parser.error("audio file does not exist")
    args.start_seconds = max(0.0, args.start_seconds)
    return args


def _wav_duration(path: Path) -> float:
    if path.suffix.lower() != ".wav":
        raise RuntimeError("silent test playback currently requires WAV audio")
    with wave.open(str(path), "rb") as source:
        return source.getnframes() / source.getframerate()


def _run(args: argparse.Namespace) -> int:
    remaining = max(0.0, _wav_duration(args.file) - args.start_seconds)
    stopping = threading.Event()

    def commands() -> None:
        for raw in sys.stdin:
            if raw.strip() == "STOP":
                stopping.set()
                return

    threading.Thread(target=commands, name="silent-audio-commands", daemon=True).start()
    stopping.wait(remaining)
    return 0


def main() -> int:
    try:
        return _run(_arguments())
    except (OSError, RuntimeError, EOFError, wave.Error, ZeroDivisionError) as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
