from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path


def _pipewire_env() -> dict[str, str]:
    env = os.environ.copy()
    runtime_dir = env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_dir}/bus")
    return env


def _bluetooth_connected(mac: str) -> bool:
    result = subprocess.run(
        ["/usr/bin/bluetoothctl", "info", mac],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
        env=_pipewire_env(),
    )
    return "Connected: yes" in result.stdout


def _bluetooth_sink_id(mac: str) -> str | None:
    result = subprocess.run(
        ["/usr/bin/wpctl", "status", "--name"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
        env=_pipewire_env(),
    )
    needle = f"bluez_output.{mac.replace(':', '_')}"
    in_sinks = False
    for line in result.stdout.splitlines():
        if "Sinks:" in line:
            in_sinks = True
            continue
        if "Sink endpoints:" in line:
            in_sinks = False
        if in_sinks and needle in line:
            match = re.search(r"(\d+)\.", line)
            if match:
                return match.group(1)
    return None


def _wait_for_bluetooth_sink(mac: str, timeout_seconds: float = 35.0) -> str:
    """Allow the reconnect service to repair a stale ACL-only connection."""
    deadline = time.monotonic() + timeout_seconds
    connected_seen = False
    while True:
        connected = _bluetooth_connected(mac)
        connected_seen = connected_seen or connected
        if connected:
            sink_id = _bluetooth_sink_id(mac)
            if sink_id:
                return sink_id
        if time.monotonic() >= deadline:
            break
        time.sleep(1)
    if connected_seen:
        raise RuntimeError("蓝牙蓝牙音频通道自动恢复超时")
    raise RuntimeError("蓝牙蓝牙音响尚未连接")


def _set_default_and_volume(mac: str, volume: int) -> None:
    sink_id = _wait_for_bluetooth_sink(mac)
    env = _pipewire_env()
    subprocess.run(
        ["/usr/bin/wpctl", "set-default", sink_id], check=True, timeout=5, env=env
    )
    subprocess.run(
        ["/usr/bin/wpctl", "set-volume", sink_id, f"{volume / 100:.2f}"],
        check=True,
        timeout=5,
        env=env,
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Play one file through the default PipeWire sink")
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--volume", type=int, default=25)
    parser.add_argument("--start-seconds", type=float, default=0.0)
    parser.add_argument("--ffmpeg", type=Path, default=Path("/usr/bin/ffmpeg"))
    parser.add_argument("--bluetooth-mac", required=True)
    args = parser.parse_args()
    if not args.file.is_file():
        parser.error("audio file does not exist")
    args.volume = max(0, min(60, args.volume))
    args.start_seconds = max(0.0, args.start_seconds)
    return args


def _run(args: argparse.Namespace) -> int:
    _set_default_and_volume(args.bluetooth_mac, args.volume)
    command = [
        str(args.ffmpeg),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-re",
    ]
    if args.start_seconds:
        command.extend(["-ss", str(args.start_seconds)])
    command.extend([
        "-i",
        str(args.file),
        "-map",
        "0:a:0",
        "-vn",
        "-f",
        "pulse",
        "default",
    ])
    player = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=sys.stderr,
        env=_pipewire_env(),
    )
    stopping = threading.Event()

    def commands() -> None:
        for raw in sys.stdin:
            value = raw.strip()
            if value == "STOP":
                stopping.set()
                if player.poll() is None:
                    player.terminate()
                return
            if value.startswith("VOLUME "):
                try:
                    volume = max(0, min(60, int(value.split()[1])))
                    _set_default_and_volume(args.bluetooth_mac, volume)
                except (IndexError, OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                    continue

    threading.Thread(target=commands, name="system-audio-commands", daemon=True).start()
    return_code = player.wait()
    return 0 if stopping.is_set() else return_code


def main() -> int:
    try:
        return _run(_arguments())
    except Exception as exc:
        print("Audio operation failed", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
