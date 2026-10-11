from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

import pyatv
from pyatv.const import Protocol
from pyatv.interface import MediaMetadata


def _select_speaker(devices, host: str, speaker_name: str):
    expected_name = speaker_name.strip().casefold()
    if expected_name:
        for device in devices:
            if str(getattr(device, "name", "")).strip().casefold() == expected_name:
                return device
        # A DHCP address can be reassigned to a different AirPlay receiver.
        # When a target name is configured, never fall back to a mismatched name.
        return None
    for device in devices:
        if str(getattr(device, "address", "")) == host:
            return device
    return None


async def _discover_speaker(args: argparse.Namespace, loop):
    """Discover RAOP by multicast first; Some receivers may ignore unicast mDNS scans."""
    errors: list[str] = []
    for attempt in range(1, args.discovery_attempts + 1):
        try:
            devices = await pyatv.scan(
                loop,
                protocol={Protocol.RAOP},
                timeout=args.scan_timeout,
            )
            selected = _select_speaker(devices, args.host, args.speaker_name)
            if selected is not None:
                return selected
        except Exception as exc:  # Network discovery errors are retried below.
            errors.append(str(exc))

        try:
            devices = await pyatv.scan(
                loop,
                hosts=[args.host],
                protocol={Protocol.RAOP},
                timeout=min(2, args.scan_timeout),
            )
            selected = _select_speaker(devices, args.host, args.speaker_name)
            if selected is not None:
                return selected
        except Exception as exc:  # Keep multicast retry as the primary path.
            errors.append(str(exc))

        if attempt < args.discovery_attempts:
            await asyncio.sleep(attempt)

    detail = f"（{errors[-1]}）" if errors and errors[-1] else ""
    raise RuntimeError(
        f"多次扫描后仍未发现音响 {args.speaker_name} / {args.host}{detail}"
    )


async def _command_loop(reader: asyncio.StreamReader, atv) -> None:
    while True:
        raw = await reader.readline()
        if not raw:
            await atv.remote_control.stop()
            return
        command = raw.decode("utf-8", errors="replace").strip().split()
        if not command:
            continue
        if command[0] == "STOP":
            await atv.remote_control.stop()
            return
        if command[0] == "VOLUME" and len(command) == 2:
            try:
                await atv.audio.set_volume(max(0, min(60, int(command[1]))))
            except (TypeError, ValueError):
                continue


def _prepare_playback_file(args: argparse.Namespace) -> tuple[Path, Path | None]:
    """Return a seeked local file because this receiver is silent for WAV pipes."""
    if args.start_seconds <= 0.05:
        return args.file, None

    handle = tempfile.NamedTemporaryFile(
        prefix="baby-monitor-airplay-",
        suffix=".mp3",
        delete=False,
    )
    temporary = Path(handle.name)
    handle.close()
    result = subprocess.run(
        [
            args.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(args.start_seconds),
            "-i",
            str(args.file),
            "-map",
            "0:a:0",
            "-vn",
            "-codec:a",
            "copy",
            str(temporary),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0 or temporary.stat().st_size == 0:
        temporary.unlink(missing_ok=True)
        detail = result.stderr.strip()
        raise RuntimeError(f"准备跳转播放失败：{detail[-240:]}")
    return temporary, temporary


async def _run(args: argparse.Namespace) -> int:
    loop = asyncio.get_running_loop()
    device = await _discover_speaker(args, loop)
    playback_file, temporary = _prepare_playback_file(args)
    atv = await pyatv.connect(device, loop)
    transport = None
    try:
        await atv.audio.set_volume(args.volume)
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        transport, _ = await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        metadata = MediaMetadata(
            title=args.title,
            artist="Edge AI Monitor",
            album=args.category,
        )
        # A real path makes pyatv use its file decoder. Audio receiver accepts this
        # source reliably, while it may accept a live WAV pipe but output silence.
        stream_task = asyncio.create_task(
            atv.stream.stream_file(str(playback_file), metadata=metadata)
        )
        command_task = asyncio.create_task(_command_loop(reader, atv))
        done, _pending = await asyncio.wait(
            {stream_task, command_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if command_task in done:
            await stream_task
        else:
            command_task.cancel()
            await asyncio.gather(command_task, return_exceptions=True)
            await stream_task
    finally:
        if transport is not None:
            transport.close()
        close_tasks = atv.close()
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return 0


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stream one local track over RAOP")
    parser.add_argument("--host", required=True)
    parser.add_argument("--speaker-name", default="Audio output")
    parser.add_argument("--discovery-attempts", type=int, default=3)
    parser.add_argument("--scan-timeout", type=float, default=5.0)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--category", required=True)
    parser.add_argument("--volume", type=int, default=25)
    parser.add_argument("--start-seconds", type=float, default=0)
    parser.add_argument("--ffmpeg", default="/usr/bin/ffmpeg")
    args = parser.parse_args()
    if not args.file.is_file():
        parser.error("audio file does not exist")
    args.volume = max(0, min(60, args.volume))
    args.start_seconds = max(0.0, args.start_seconds)
    args.discovery_attempts = max(1, min(5, args.discovery_attempts))
    args.scan_timeout = max(1.0, min(5.0, args.scan_timeout))
    return args


def main() -> int:
    try:
        return asyncio.run(_run(_arguments()))
    except Exception as exc:  # Worker errors are returned to the parent service.
        print("Audio operation failed", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
