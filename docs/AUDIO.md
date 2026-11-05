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

### Wake word

The default wake word is 「佩奇」 (`BMON_VOICE_WAKE_WORD`; 「佩奇佩奇」 is accepted too). The KWS
keywords file must contain the same phrases. On the Jetson it was generated with:

```bash
printf "佩奇 @佩奇\n佩奇佩奇 @佩奇佩奇\n" > keywords-bmon-raw.txt
.venv-voice/bin/sherpa-onnx-cli text2token --tokens tokens.txt --tokens-type ppinyin keywords-bmon-raw.txt keywords-bmon.txt
```

which yields `p èi q í @佩奇`. Changing the wake word needs both the setting and a regenerated
keywords file, followed by an app restart.

## Cry detection

The voice worker also scores the latest two seconds of microphone audio once per second with
the Sherpa-ONNX zipformer AudioSet tagger (`sherpa-onnx-zipformer-small-audio-tagging-2024-04-15`,
Apache-2.0, int8 model only, about 26 MB) for "Baby cry, infant cry" and "Crying, sobbing".
Two scores of at least `BMON_CRY_THRESHOLD` (default 0.5) within 5 seconds start a crying
period; 15 seconds without one end it. Periods are stored as `cry` intervals in the vision
database with the current observation mode and appear in the unified timeline, daily summary
and AI assistant answers ("昨晚哭了几次"). **No audio is recorded or stored.**

Cry detection runs around the clock, even when wake-word control is off or outside its
schedule; wake words and commands are ignored at those times. Place the model directory under
`models/` or set `BMON_CRY_MODEL_DIR`; set `BMON_CRY_ENABLED=false` to turn it off.
Accuracy on real infants, distance and room noise has not been validated.

## Spoken records

After the wake word, "记一下 / 记录 …" (or any care sentence that is not a music command,
such as "宝宝喝了一百五十毫升奶") is sent to the AI assistant as a normal message in a
conversation titled 「语音记录」. Spoken amounts are converted to digits first. Ambiguous
records still wait for confirmation in that conversation.

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
