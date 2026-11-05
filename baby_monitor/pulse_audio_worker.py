"""Play one file on a Bluetooth speaker through PulseAudio using GStreamer.

Runs under the system Python (it needs the distribution's PyGObject/GStreamer
bindings). Protocol on stdin matches the other workers: ``STOP`` and
``VOLUME <0-60>``. Pause/resume is done by the parent with SIGSTOP/SIGCONT.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

MAX_VOLUME = 60


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def _run(command: list[str], timeout: float = 10) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, check=False, env=_env()
    )


def _bluetooth_connected(mac: str) -> bool:
    return "Connected: yes" in _run(["bluetoothctl", "info", mac], 5).stdout


def sink_name(mac: str) -> str:
    return f"bluez_sink.{mac.replace(':', '_')}.a2dp_sink"


def card_name(mac: str) -> str:
    return f"bluez_card.{mac.replace(':', '_')}"


def _sink_present(mac: str) -> bool:
    return sink_name(mac) in _run(["pactl", "list", "sinks", "short"], 5).stdout


def wait_for_sink(mac: str, timeout_seconds: float = 30.0) -> str:
    """Connect the trusted speaker if needed and make sure it is in A2DP mode."""
    deadline = time.monotonic() + timeout_seconds
    connect_tried = False
    connected_seen = False
    while True:
        connected = _bluetooth_connected(mac)
        connected_seen = connected_seen or connected
        if not connected and not connect_tried:
            connect_tried = True
            _run(["bluetoothctl", "connect", mac], 20)
            continue
        if connected:
            if _sink_present(mac):
                return sink_name(mac)
            # Speakers sometimes come up in hands-free mode; ask for music mode.
            _run(["pactl", "set-card-profile", card_name(mac), "a2dp_sink"], 5)
            if _sink_present(mac):
                return sink_name(mac)
        if time.monotonic() >= deadline:
            break
        time.sleep(1)
    if connected_seen:
        raise RuntimeError("蓝牙音箱已连接，但音乐通道没有就绪")
    raise RuntimeError("蓝牙音箱没有连接，请打开音箱")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Play one file on a PulseAudio Bluetooth sink")
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--volume", type=int, default=25)
    parser.add_argument("--start-seconds", type=float, default=0.0)
    parser.add_argument("--bluetooth-mac", required=True)
    args = parser.parse_args()
    if not args.file.is_file():
        parser.error("audio file does not exist")
    args.volume = max(0, min(MAX_VOLUME, args.volume))
    args.start_seconds = max(0.0, args.start_seconds)
    return args


def _play(args: argparse.Namespace) -> int:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    sink = wait_for_sink(args.bluetooth_mac)
    Gst.init(None)
    playbin = Gst.ElementFactory.make("playbin", "player")
    audio_sink = Gst.ElementFactory.make("pulsesink", "speaker")
    if playbin is None or audio_sink is None:
        raise RuntimeError("GStreamer playbin/pulsesink unavailable")
    audio_sink.set_property("device", sink)
    playbin.set_property("audio-sink", audio_sink)
    playbin.set_property("video-sink", Gst.ElementFactory.make("fakesink", None))
    playbin.set_property("uri", args.file.resolve().as_uri())
    playbin.set_property("volume", args.volume / 100)

    bus = playbin.get_bus()
    playbin.set_state(Gst.State.PAUSED)
    playbin.get_state(10 * Gst.SECOND)
    if args.start_seconds:
        playbin.seek_simple(
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
            int(args.start_seconds * Gst.SECOND),
        )
    playbin.set_state(Gst.State.PLAYING)

    stopping = threading.Event()

    def commands() -> None:
        for raw in sys.stdin:
            value = raw.strip()
            if value == "STOP":
                stopping.set()
                return
            if value.startswith("VOLUME "):
                try:
                    volume = max(0, min(MAX_VOLUME, int(value.split()[1])))
                except (IndexError, ValueError):
                    continue
                playbin.set_property("volume", volume / 100)

    threading.Thread(target=commands, name="pulse-audio-commands", daemon=True).start()

    return_code = 0
    try:
        while not stopping.is_set():
            message = bus.timed_pop_filtered(
                200 * Gst.MSECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR
            )
            if message is None:
                continue
            if message.type == Gst.MessageType.ERROR:
                error, _debug = message.parse_error()
                print(f"Playback error: {error.message}", file=sys.stderr, flush=True)
                return_code = 1
            break
    finally:
        playbin.set_state(Gst.State.NULL)
    return return_code


def main() -> int:
    try:
        return _play(_arguments())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    except Exception:
        print("Audio operation failed", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
