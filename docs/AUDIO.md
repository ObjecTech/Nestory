# Optional local audio

Audio sensing and playback are opt-in. No recordings, copyrighted music or pretrained weights
are bundled. Only use media you are authorized to process. Configure device settings privately.

## Offline voice pipeline

Provision Sherpa-ONNX 1.13.4 and its compatible dependencies in `.venv-voice`:

```bash
python3 -m venv .venv-voice
.venv-voice/bin/python -m pip install sherpa-onnx==1.13.4 numpy click sentencepiece pypinyin
```

Review MODEL_LICENSE.md before downloading the KWS, Paraformer and VAD artifacts. Place them
in the ignored models directory or set BMON_VOICE_MODEL_DIR, BMON_VOICE_ASR_MODEL_DIR,
BMON_VOICE_VAD_MODEL and BMON_VOICE_KEYWORDS_FILE. The KWS tokenizer must match the downloaded
model; use the upstream sherpa-onnx-cli text2token instructions for your own wake phrases.
Set BMON_VOICE_AUDIO_DEVICE and BMON_VOICE_PYTHON for the deployment, then enable voice through
the authenticated settings page. The default is off. Configure the local schedule and timezone
with BMON_VOICE_SCHEDULE_START, BMON_VOICE_SCHEDULE_END and BMON_VOICE_TIMEZONE.

Original spoken feedback files are deliberately excluded. Without your own authorized
`assets/voice_feedback/repeat.mp3`, failed-command spoken feedback is unavailable; the UI
still exposes a generic feedback failure. Offline recognition/control remains available.

## Playback

For AirPlay use an isolated environment with pyatv, set BMON_AIRPLAY_PYTHON to its interpreter
and configure BMON_SPEAKER_HOST and BMON_SPEAKER_NAME to match your authorized receiver.
For PipeWire set BMON_PLAYBACK_BACKEND=pipewire and configure BMON_BLUETOOTH_SPEAKER_MAC.
Install ffmpeg, PipeWire/PulseAudio compatibility and bluetoothctl as appropriate for your OS.
Set BMON_PLAY_MEDIA_DIR to a local directory containing your own category subdirectories.
Do not add that media directory to Git. Audio hardware and receiver compatibility require
validation on the target device.

For control-flow testing when no speaker is connected, set
`BMON_PLAYBACK_BACKEND=silent` and use WAV files. This backend follows each file's real
duration and supports the normal play, pause, previous, next, stop and seek controls, but
deliberately produces no sound. Replace it with `pipewire` or `airplay` for audible playback.
