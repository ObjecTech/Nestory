# Security model

This is single-owner research software. The owner is trusted to configure storage, models,
operating-system permissions and a TLS proxy. Authentication does not protect against a
compromised operating system, malicious local administrator or untrusted model file.

## Implemented boundaries

- No loopback or private-network authentication bypass. Credentials are required at startup.
- PBKDF2-HMAC-SHA256 with random salt; newly generated hashes use 600,000 iterations.
- Random opaque session IDs and session-bound CSRF tokens, stored only in process memory.
- One-hour expiry, logout revocation, and session invalidation on process restart.
- Strict SameSite cookies; session is HttpOnly, and HTTPS origins use Secure cookies.
- Exact Host/Origin checks and a bounded account-level login attempt limiter.
- Every mutation is authenticated and CSRF-checked. Only the owner manages access.
- Access disable closes active video/event sockets; stream loops also check access and expiry.
- Photo endpoints require a current allowed photo record; no arbitrary media-directory URLs.
- Photo paths must resolve within smiles/classified, with JPEG/PNG validation and JPEG re-encoding.
- HTTP JSON and SSE omit filesystem paths, raw errors, device addresses and voice transcripts.
- Private pages, photos and API outputs are not stored in the service worker's cache.
- Local model endpoints must use exact loopback hosts. The model worker refuses non-loopback binding.

## Deployment requirements

Use HTTPS for network access. The Python server is an upstream application, not a hardened
internet-facing TLS server. Bind to loopback and configure a reverse proxy with the exact
external Host, connection/rate limits, TLS, request-size limits and no streaming buffering.
The built-in client read timeout is 10 seconds. Do not expose the model worker or a second
unauthenticated origin. HTTP mode is only for local development.

## Remaining limitations

- No MFA, role separation, per-tenant ACLs or persistent session store.
- The account-level limiter can temporarily lock out the owner after abusive attempts;
  use proxy-level rate limits as well. Session state is process-local, so use one worker process.
- A session evicted by the 32-session cap or expired is detected on the next stream check;
  explicit logout/access disable actively shuts down its sockets.
- Bytes already buffered by clients or external proxies cannot be recalled.
- Access disable stops access, not collection. Stop the service to stop sensing.
- Photos moved to the local recycle area and separate training samples remain on disk.
  No complete retention/erasure workflow or encrypted-at-rest storage is implemented.
- Model text is untrusted observational output; there is no validated clinical performance.
- Hardware/GPU/proxy combinations and third-party model artifacts need independent review.
- Full software-composition/CVE scanning and an external penetration test are not claimed.

Do not put live credentials, personal records or exploit details containing private data into
public issues. Maintainers should configure a private vulnerability-reporting channel when
publishing the repository.

## Optional owner SSO and trusted LAN listener

`BMON_TAILSCALE_USER` enables owner-only auto-session creation behind Tailscale Serve.
The primary listener must bind loopback with an HTTPS origin. Every request must carry
Serve's `Tailscale-User-Login` matching that explicit owner. Trust is limited to Serve
and local OS users; never expose this upstream or put an untrusted local proxy before it.
Funnel and other identities are rejected. Sessions, CSRF and access-off remain enforced.

An optional `BMON_LAN_HOST`, `BMON_LAN_PORT` (default 8080), and `BMON_LAN_SUBNET`
create a separate HTTP listener sharing the same app. Anyone in that explicitly trusted
private /24-or-narrower subnet can enter without a password. This is unencrypted LAN
preview, not a public endpoint; do not port-forward it. Its Host/Origin checks and
sessions are separate from the HTTPS listener. Default deployments enable neither option.
