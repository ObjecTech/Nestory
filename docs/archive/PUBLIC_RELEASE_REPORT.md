# Public Release Preparation Report

Prepared: 2026-09-10
Project: Edge AI Infant Monitoring Research Platform
Scope: a separate local public-release candidate; no GitHub upload or publication performed.

## Changes Made

- Created a new public checkout from an explicit source-file allowlist.
- Initialized an independent Git repository on `codex/public-release`; no original `.git`,
  commits, objects, references, author metadata or diff snapshots were copied.
- No initial commit was created, so a release maintainer can choose the public author identity.
- Retained the Jetson camera adapter, frame buffering, temporal filtering, optional vision
  and Qwen pipelines, offline speech processing, audio playback and web interface.
- Added a hardware-free synthetic camera adapter and an explicitly synthetic example response.
- Rewrote README, added generic environment/setup and user-service examples, Apache-2.0 LICENSE,
  SECURITY.md, MODEL_LICENSE.md, THIRD_PARTY.md and optional audio instructions.
- Removed legacy device-specific installation, disk-mount, mDNS and destructive cleanup scripts.
  Replaced the separate remote proxy with one authenticated application path.

## Removed Private Information

- Personal names, personalized titles/notifications, deployment usernames and personal paths.
- Family/private photographs, portraits used as icons, original audio, and photo metadata.
- Actual private-network addresses, receiver identifiers and disk-volume configuration.
- Python bytecode, test caches, system files, runtime data and training/media/model artifacts.
- Thirteen public images were created from geometric primitives; no input photographs or
  third-party artwork were used. No EXIF, GPS, XMP, MakerNote or camera metadata is present.
- All private-image/audio/cache hashes and known private identity/device markers were checked
  against the public content, with no matching remnants detected.

The report intentionally does not reproduce the original identities, addresses or sensitive paths.

## Security Improvements

- Mandatory username/password authentication for all clients, including loopback.
- Random server-side sessions with one-hour expiry, revocation on logout/restart, and a bounded
  session count. Test credentials are explicitly TEST_ONLY and are not deployment defaults.
- PBKDF2-SHA256 password hashes with random salt; generated hashes use 600,000 iterations.
- Session-bound CSRF tokens and exact Host/Origin checks on state-changing operations.
- Account-level login attempt limiting, HttpOnly session cookies, Strict SameSite and Secure
  cookies for HTTPS origins. Missing/invalid credential configuration fails at startup.
- Disabling monitoring shuts down active MJPEG/SSE sockets and rejects future data requests;
  the authenticated owner retains settings access. Logout also closes that session's streams.
- Photo delivery now uses `/api/photos/{id}/original` or `/thumbnail`. A current allowed photo
  record, isolated path, allowed image type and successful decoding are required. Delivery
  re-encodes to metadata-free JPEG; arbitrary media paths and trashed records are rejected.
- HTTP JSON/SSE remove filesystem paths, raw error details, device addresses and recent
  voice-command details. Private data is not stored by the service-worker cache.
- Model service configuration is restricted to exact loopback origins and model-worker
  startup refuses network-wide binding. Runtime file creation uses a restrictive umask.

## Verification Results

| Check | Result |
|---|---|
| Original project integrity | All 88 original file paths, SHA-256 hashes, sizes and modification times matched the pre-preparation baseline |
| Unit and integration tests | 56 tests: 55 passed; 1 optional pinyin test skipped because the optional library is not installed |
| Authentication regressions | Anonymous/loopback denial, login cookies, CSRF/origin enforcement, logout revocation and expiry passed |
| Access-disable regression | Existing video and event sockets reached EOF within the one-second test bound; future data access was denied |
| Photo authorization regressions | Allowed photo ID passed; arbitrary media URLs, traversal, symlink escape, invalid types and trashed records rejected |
| Synthetic application smoke | Synthetic camera, public response shapes, empty photo library and microphone-off state passed |
| Python, JavaScript and JSON syntax | Passed |
| Shell syntax and static asset references | Passed |
| Release file/secret/privacy/image scan | No findings in release content; this is a heuristic result, not a proof of absence |
| Known private marker/media fingerprint comparison | No matches in release content |
| New Git object/history check | Empty newly initialized repository; no inherited history, objects, remotes or author identity |
| Browser automation | Login page observed; full UI login/navigation was blocked by the browser automation environment. Security checks were not weakened. Direct HTTP login, cookies and authenticated navigation passed |

Tests operated on temporary synthetic data. The temporary demonstration service was stopped,
its browser tabs closed, and its generated runtime data removed from the public checkout.
No live credentials were configured, rotated or revoked on the original deployment.

## Remaining Limitations

- This is a source-release candidate, not a production-security or medical certification.
- Physical Jetson, camera, GPU inference, audio devices and production HTTPS proxy were not
  available for deployment acceptance tests. Complete browser interaction still needs validation
  outside the browser-automation restriction described above.
- Single-owner access only; no tenant separation, MFA or production-scale session service.
- Use a TLS reverse proxy, upstream network isolation and proxy connection/rate limits.
  The Python server itself does not implement TLS or comprehensive denial-of-service controls.
- Access disable stops access, not sensing or recording. Already delivered bytes cannot be recalled.
- There is no complete retention/erasure workflow or at-rest encryption; training copies and
  recycle-area files can remain on the deployment device.
- Spoken feedback recordings were excluded. Supply an authorized feedback asset if needed.
- Some optional model-specific licenses/revisions are unresolved and explicitly marked in
  MODEL_LICENSE.md. No weights are redistributed or downloaded by this preparation.
- Full third-party dependency/CVE analysis and independent penetration testing remain separate work.
- No GitHub repository was created or made public, and no commit/push was performed.

## Third-party Licenses

The project uses Apache-2.0 for its original content and geometric artwork. Optional dependencies
and weights retain their own terms. See MODEL_LICENSE.md for version/source/license/download
entries and THIRD_PARTY.md for attribution references. Library licenses are not substituted for
unverified model or dataset redistribution permissions.

## How to Run

From this public checkout:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -B scripts/configure-auth.py
bash scripts/run.sh
```

Open `http://localhost:8080` using the username/password you chose. The generated environment
starts a geometric synthetic demo with camera hardware, vision models and microphone processing
disabled. Follow README and docs/AUDIO.md for optional hardware integrations.

Before creating a release commit, rerun:

```bash
python -B -m unittest discover -s tests -v
python -B scripts/scan-release.py
```

The scanner intentionally flags runtime files left in a working checkout. Only publish the
reviewed source/assets, never the ignored environment, media, models or database directories.

## Final Status

- Original project: **UNCHANGED**
- Public copy: **SANITIZED**
- Git history: **NEW CLEAN REPOSITORY — initialized, no commits**
- Private data: **EXCLUDED; temporary synthetic runtime data removed**
- Secrets: **NONE DETECTED; explicit test-only fixtures remain**
- Images: **PUBLIC SAFE geometric assets; no people or image metadata**
- README: **PUBLIC READY with deployment and validation limitations documented**

Public release preparation completed.

Original private project was not modified.

A separate sanitized public copy was created.
