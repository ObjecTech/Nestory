# Nestory Assistant API Contract v1

Status: **Phase 0 design contract; endpoints are not implemented yet.**
Date: 2026-10-13. Backend baseline: internal `33b0785`, public `e48b835`.
Audience: Codex backend and Gemini frontend. Changes to routes, required fields, status
semantics or enum values require a documented contract revision and notice to the
frontend developer. Additive optional fields may be ignored by clients. Literal `...` never appears in a wire response.

## 1. Baseline, responsibilities and deployment boundary

Verified Mac and Jetson working copies were clean and both at `33b0785`; public
GitHub main was `e48b835`. Backend development uses the separate worktree and
`codex/assistant-backend` branch. Gemini must use its own worktree/checkout.
No branch switching in the other developer's checkout. No frontend source edits
are part of Phase 0. No production main push or running Jetson service restart.

Existing components:

- `server.py`: single-owner sessions, exact Host, optional Tailscale owner check,
  monitoring access gate, Origin and session-bound CSRF checks for POST.
- `baby_monitor/database.py`: SQLite WAL database containing `intervals`,
  `observations`, visual photo `events` and settings. Rows have observation modes
  `infant`, `adult_test`, or `legacy_unknown`.
- `baby_monitor/app.py`: owns visual DB, camera, independent capture thread and VLM observer.
- `model_server.py`: one loaded Qwen3-VL-2B-Instruct and processor, loopback-only
  `/health` and JPEG `/analyze`; currently no chat endpoint.
- `Engine.lock` currently wraps generation but not GPU input preparation. A second
  caller could allocate GPU input tensors before obtaining the lock. It is not
  a complete multi-request GPU admission boundary.
- `app.timeline()` computes durations from existing intervals. It is **not** a
  trustworthy sleep-duration source while monitoring continuity remains unverified.

Assistant components to add in Phase 1:

| Component | Responsibility |
|---|---|
| `assistant_database.py` | additive migration, scope-filtered CRUD, turn claims, idempotency, event audit |
| `assistant_service.py` | bounded job runner, strict extraction validation, commit acknowledgements |
| `assistant_model.py` | bounded loopback `/chat` client and independent chat prompt |
| `assistant_facts.py` | allowlisted read-only visual queries and Python calculations |
| `inference_scheduler.py` | one GPU owner, bounded admission and vision priority |
| `server.py` adapters | shared authentication/gates for GET/POST/PATCH/DELETE; contract serialization |

No cloud AI calls, second model instance, generated SQL/Python/shell execution,
or direct browser access to port 8010. Private runtime DBs, prompts containing
personal data, chat logs and credentials remain outside Git.

## 2. Common protocol and security

Base path `/api/assistant`. JSON UTF-8, `Cache-Control: no-store`, no streaming in
v1. All successful JSON objects contain `api_version: "1"`. Unless stated
otherwise HTTP success is 200. IDs are opaque server-generated UUID strings
shown below as short illustrative labels; clients must not derive filesystem paths
or sequential record counts from them.

Times exposed to the browser are RFC3339 UTC strings ending `Z`. The server
stores UTC epoch milliseconds as INTEGER. `timezone` must be a valid IANA zone;
its default for new conversations is `Asia/Shanghai`. Dates in date filters and
relative user phrases are interpreted in the explicitly selected timezone.
`amount_ml` is integer millilitres; `duration_seconds` is derived, never accepted
as an editable value. All examples below are **synthetic Demo fixtures**.

Security requirements for every Assistant route:

1. Reuse existing authenticated `bmon_session` cookie; never put tokens in URLs.
2. Retain existing trusted-network, Tailscale-owner, exact Host and
   `lan_enabled` access checks. A trusted LAN still needs the session established
   by the existing login/authorized auto-login flow. No new anonymous endpoints.
3. Every POST/PATCH/DELETE requires exact external `Origin` and valid
   `X-CSRF-Token` from the session's `bmon_csrf` cookie. Use same-origin fetch and
   `credentials: "same-origin"`. GET does not mutate state.
4. Writes with a JSON body require `Content-Type: application/json`.
   DELETE is bodyless, but still has Origin/CSRF requirements.
5. Single configured owner only. Sessions belonging to that owner share history;
   logout is not a history deletion. This does not implement multiple owners/children.
6. Every resource lookup/update combines ID and scope. Wrong-scope and absent IDs
   both return identical 404, never disclose the other scope's object.
7. Model output, client text and filenames are untrusted. Render message text with
   textContent or sanitized Markdown; never insert model HTML directly.
8. Assistant route recognition happens before existing helpers can emit legacy HTML/plain
   errors, with equivalent Host/network/session checks returning the unified JSON
   envelope (including 421). Assistant routes use their own serializer: existing `public_payload()` rewrites
   keys containing `error` and would destroy the nested error contract. Reuse
   authentication, not that generic visual-payload transformation.

For ID routes, `?scope=demo|real` is **required**. The conversation's stored scope
is immutable and authoritative; a caller cannot override it in a message body.
Collection GET defaults to `scope=demo` when omitted. New conversation POST
requires explicit `scope`. A write cannot implicitly choose Real. Observation
`adult_test`/`infant` is independent of care `demo`/`real`.

Limits (contract defaults; changing them downward requires compatible frontend handling):

| Limit | Value |
|---|---|
| title | 1–80 Unicode characters, trim whitespace; no control characters |
| message content | 1–4000 Unicode characters, trim for validation only; preserve original text |
| `client_message_id` | 1–128 ASCII letters/digits/`-_.`; frontend generates UUID |
| notes | max 2000 Unicode characters |
| JSON request body | max 32 KiB; reject bad/missing Content-Length, malformed JSON and duplicate keys |
| page size | default 50, allowed 1–100 |
| care/sleep date span | max 31 calendar days per request |
| pending turn per conversation | 1; other sessions receive 409 `conversation_busy` |
| Assistant pending turns globally | 4; bounded worker, reject excess with 429 |
| inference scheduler waiting jobs | 1 vision + 2 chat, plus 1 running job |
| request socket | does not wait for model generation; POST returns 202 |

Unknown write fields, invalid enums, booleans where numbers are expected, and
invalid timestamps are rejected with 422. Unknown paths return JSON 404; methods
not supported by a valid route return JSON 405 with `Allow`.

### Unified error envelope

```json
{
  "api_version": "1",
  "error": {
    "code": "validation_error",
    "message": "amount_ml 必须是实际饮用量，且为 1–2000 的整数。",
    "retryable": false,
    "details": [{"field": "amount_ml", "reason": "out_of_range"}],
    "retry_after_seconds": null
  },
  "request_id": "req-example-01"
}
```

Do not expose paths, stack traces, raw prompts, model response fragments, cookies
or deployment addresses in errors. `request_id` is generated per HTTP request,
not a deduplication key. Never log chat bodies or structured care contents in
access/exception logs.

| HTTP | Codes | Frontend behavior |
|---|---|---|
| 400 | `invalid_json`, `invalid_cursor`, `scope_required` | fix request |
| 401 | `authentication_required` | login, then reload history |
| 403 | `csrf_failed`, `origin_forbidden`, `access_disabled`, `client_forbidden` | restore authorized access; no automatic write retry |
| 404 | `conversation_not_found`, `message_not_found`, `event_not_found`, `action_not_found`, `route_not_found` | remove stale selection; do not recreate deleted conversation |
| 405 | `method_not_allowed` | use documented method |
| 409 | `idempotency_conflict`, `conversation_busy`, `version_conflict`, `request_in_progress`, `action_expired`, `request_not_retryable` | reload corresponding object; retain draft |
| 413 / 415 | `payload_too_large` / `unsupported_media_type` | reduce input / send JSON |
| 421 | `host_mismatch` | correct external origin |
| 422 | `validation_error`, `invalid_timezone`, `invalid_scope` | show field errors |
| 429 | `queue_full`, `rate_limited` | honor `Retry-After`; retain client message ID |
| 503 | `assistant_disabled`, `model_unavailable`, `service_restarted`, `vision_capacity_reserved`, `storage_unavailable` | retry if explicitly retryable |
| 504 | `model_timeout` | polling/turn error; no false success |
| 502 | `model_failed`, `invalid_model_output` | polling/turn error; no care writes |
| 500 | `internal_error` | generic failure, retain request ID |

Pre-acceptance HTTP errors create no user message, turn or event. After HTTP 202,
a failed turn is fetched with HTTP 200 and has `status: "failed"` plus the same
nested `error` object; a polling 200 is not generation success. Successful endpoint
responses have no `error` property unless shown as nullable turn state.

## 3. SQLite model, transactions and migration

Assistant uses a **separate** ignored `${BMON_DATA_DIR}/assistant.db`, enabled
only by `BMON_ASSISTANT_ENABLED=true` (default false during staged development).
It is still local SQLite; it avoids long Assistant writes on the existing visual
connection. Use dedicated `assistant_schema_migrations`, WAL, foreign_keys=ON,
synchronous=FULL for care acknowledgements, busy_timeout=3000 and an RLock around
short DB transactions. Each worker must use a properly owned connection or the
same locked `check_same_thread=False` connection. Never hold a DB transaction
while waiting for or running inference. Migration failure leaves Assistant
disabled and vision functional. Never reset the visual DB or repurpose its
`events` table. Versioned migrations are transactional and recorded only on commit.

Candidate v1 SQL (epoch milliseconds, JSON columns are encoded with canonical JSON;
backend validates all values before parameterized SQL):

```sql
CREATE TABLE assistant_schema_migrations (
  version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL
);
CREATE TABLE chat_conversations (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  title_source TEXT NOT NULL CHECK(title_source IN ('default','auto','manual')),
  data_scope TEXT NOT NULL CHECK(data_scope IN ('demo','real')),
  timezone TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX assistant_conversations_scope_recent
  ON chat_conversations(data_scope, updated_at DESC, id DESC);
CREATE TABLE chat_turns (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
  client_message_id TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  timezone TEXT NOT NULL,
  reference_at INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed')),
  attempt INTEGER NOT NULL DEFAULT 1,
  error_json TEXT,
  result_actions_json TEXT,
  facts_json TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE(conversation_id, client_message_id)
);
CREATE UNIQUE INDEX assistant_one_active_turn
  ON chat_turns(conversation_id) WHERE status IN ('queued','running');
CREATE TABLE chat_messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
  turn_id TEXT NOT NULL REFERENCES chat_turns(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK(role IN ('user','assistant')),
  content TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed')),
  created_at INTEGER NOT NULL,
  UNIQUE(turn_id, role)
);
CREATE INDEX assistant_messages_conversation_order
  ON chat_messages(conversation_id, created_at, id);
CREATE TABLE care_events (
  id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL CHECK(event_type IN ('feeding','diaper','sleep','activity')),
  data_scope TEXT NOT NULL CHECK(data_scope IN ('demo','real')),
  timezone TEXT NOT NULL,
  occurred_at INTEGER NOT NULL,
  end_at INTEGER,
  amount_ml INTEGER,
  diaper_type TEXT CHECK(diaper_type IN ('wet','dirty','mixed','unknown')),
  notes TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL CHECK(source IN ('parent_chat','parent_manual')),
  source_message_id TEXT REFERENCES chat_messages(id) ON DELETE SET NULL,
  source_reference_id TEXT NOT NULL,
  extraction_key TEXT UNIQUE,
  status TEXT NOT NULL CHECK(status IN ('confirmed','deleted')),
  version INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  deleted_at INTEGER,
  CHECK(end_at IS NULL OR end_at > occurred_at),
  CHECK(amount_ml IS NULL OR amount_ml BETWEEN 1 AND 2000)
);
CREATE INDEX assistant_events_scope_time
  ON care_events(data_scope, occurred_at DESC, id DESC);
CREATE TABLE assistant_write_requests (
  data_scope TEXT NOT NULL CHECK(data_scope IN ('demo','real')),
  client_event_id TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  event_id TEXT NOT NULL REFERENCES care_events(id),
  PRIMARY KEY(data_scope, client_event_id)
);
CREATE TABLE care_event_revisions (
  id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES care_events(id),
  revision INTEGER NOT NULL,
  operation TEXT NOT NULL CHECK(operation IN ('create','update','delete')),
  before_json TEXT,
  after_json TEXT NOT NULL,
  changed_at INTEGER NOT NULL,
  change_source TEXT NOT NULL CHECK(change_source IN ('auto_chat','parent_form','parent_confirmation')),
  UNIQUE(event_id, revision)
);
CREATE TABLE record_proposals (
  id TEXT PRIMARY KEY,
  turn_id TEXT NOT NULL REFERENCES chat_turns(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL,
  data_scope TEXT NOT NULL CHECK(data_scope IN ('demo','real')),
  operation TEXT NOT NULL CHECK(operation IN ('create','update','delete')),
  target_event_id TEXT REFERENCES care_events(id),
  expected_version INTEGER,
  proposed_fields_json TEXT NOT NULL,
  missing_fields_json TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending','confirmed','dismissed','expired')),
  expires_at INTEGER NOT NULL,
  resolution_hash TEXT,
  result_event_id TEXT REFERENCES care_events(id),
  UNIQUE(turn_id, ordinal)
);
CREATE TRIGGER assistant_conversation_scope_immutable
BEFORE UPDATE OF data_scope ON chat_conversations
WHEN NEW.data_scope != OLD.data_scope
BEGIN SELECT RAISE(ABORT, 'scope immutable'); END;
CREATE TRIGGER assistant_event_scope_immutable
BEFORE UPDATE OF data_scope ON care_events
WHEN NEW.data_scope != OLD.data_scope
BEGIN SELECT RAISE(ABORT, 'scope immutable'); END;
CREATE TRIGGER assistant_event_source_scope_insert
BEFORE INSERT ON care_events
WHEN NEW.source_message_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM chat_messages m JOIN chat_conversations c ON c.id=m.conversation_id
  WHERE m.id=NEW.source_message_id AND c.data_scope=NEW.data_scope
)
BEGIN SELECT RAISE(ABORT, 'source scope mismatch'); END;
CREATE TRIGGER assistant_event_source_scope_update
BEFORE UPDATE OF source_message_id ON care_events
WHEN NEW.source_message_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM chat_messages m JOIN chat_conversations c ON c.id=m.conversation_id
  WHERE m.id=NEW.source_message_id AND c.data_scope=NEW.data_scope
)
BEGIN SELECT RAISE(ABORT, 'source scope mismatch'); END;
CREATE TRIGGER assistant_proposal_scope_insert
BEFORE INSERT ON record_proposals
WHEN NOT EXISTS (
  SELECT 1 FROM chat_turns t JOIN chat_conversations c ON c.id=t.conversation_id
  WHERE t.id=NEW.turn_id AND c.data_scope=NEW.data_scope
) OR (NEW.target_event_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM care_events e WHERE e.id=NEW.target_event_id AND e.data_scope=NEW.data_scope
))
BEGIN SELECT RAISE(ABORT, 'proposal scope mismatch'); END;
CREATE TRIGGER assistant_proposal_scope_update
BEFORE UPDATE ON record_proposals
WHEN NEW.data_scope != OLD.data_scope OR NEW.turn_id != OLD.turn_id
  OR (NEW.target_event_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM care_events e WHERE e.id=NEW.target_event_id AND e.data_scope=NEW.data_scope
  )) OR (NEW.result_event_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM care_events e WHERE e.id=NEW.result_event_id AND e.data_scope=NEW.data_scope
  ))
BEGIN SELECT RAISE(ABORT, 'proposal scope mismatch'); END;
CREATE TRIGGER assistant_manual_request_scope_insert
BEFORE INSERT ON assistant_write_requests
WHEN NOT EXISTS (
  SELECT 1 FROM care_events e WHERE e.id=NEW.event_id AND e.data_scope=NEW.data_scope
)
BEGIN SELECT RAISE(ABORT, 'manual request scope mismatch'); END;
```

Additional DB invariants enforced by the service transaction and migration triggers:
source message, proposal target and resulting care event must belong to the same
scope; reject mismatches on insert/update. The candidate SQL includes triggers for source/proposal/manual-request scope
invariants; update paths must keep resolved targets immutable. assistant_write_requests
rows are insert-only (no API update or reuse under a different scope). Never update scope.
Event field validity is type-specific (section 6). Turn results retain **event IDs
and action metadata**, not duplicate full message bodies. Revisions contain only
structured event snapshots, never a deleted conversation's body or model prompt.
`source_reference_id` is a generated immutable random provenance identifier, not
an identifier resolving to archived chat text. `source_message_id` becomes null
when its message is deleted. Existing revisions must not retain a now-deleted
message ID as a readable source; serializers resolve linkage from the live event.

Atomicity boundaries:

- acceptance: conversation check, idempotency lookup, active-turn claim, turn and
  user-message insert, first title and updated_at change in one transaction;
- completion: check conversation/turn still exists and is running, validate all
  actionable events, event writes + revisions + proposals + assistant message +
  turn completion in one transaction. All-or-nothing care writes;
- deleting conversation physically cascades messages/turns/proposals; care events
  survive via SET NULL. A finishing worker checks existence **inside** the
  completion transaction and never upserts a conversation or resurrects a turn;
- event PATCH/DELETE uses expected version and increments it with an audit revision
  in one transaction. Events are soft-deleted and excluded from active queries;
- startup marks previously queued/running turns failed with `service_restarted`,
  no automatic inference replay. Retrying uses the same stored turn and reference
  time. Previously completed turns are replayed, never regenerated.

## 4. Conversation and message objects

```json
{
  "id": "conv-demo-a",
  "title": "今天上午11点宝宝喝了150ml奶",
  "title_source": "auto",
  "scope": "demo",
  "timezone": "Asia/Shanghai",
  "created_at": "2026-10-13T03:05:00Z",
  "updated_at": "2026-10-13T03:06:00Z",
  "last_message_preview": "已记录本次喂奶：11:00，150 ml。",
  "version": 3
}
```

Title is `新对话` on creation. First accepted message creates a deterministic
short title from its first 24 non-control characters; no extra GPU inference.
Manual title is never overwritten by auto title. `version` increases on title,
message acceptance and completion. `last_message_preview` is latest message
text, trimmed to 80 characters, null for an empty conversation. It is not the
model context. Completion timestamps must be at least one millisecond later than the corresponding
user message to preserve role order even under an instant fake model.
Message acceptance/completion moves a conversation to the top;
loading/switching alone does not change ordering. Rename updates updated_at.

```json
{
  "id": "msg-user-a1",
  "conversation_id": "conv-demo-a",
  "role": "user",
  "content": "今天上午11点宝宝喝了150ml奶",
  "created_at": "2026-10-13T03:05:00Z",
  "status": "completed",
  "client_message_id": "frontend-uuid-001",
  "turn_id": "turn-a1"
}
```

`client_message_id` appears on user messages; assistant messages expose null.
Only completed assistant messages have generated content. Queued/running/failed
user messages remain visible with their state and retry UI; no fabricated
assistant bubble on failure. Context includes only earlier completed turns of
that exact conversation, excluding failed/pending messages.

### GET /conversations?scope=demo&limit=50&cursor=...

200, most recently used first, keyset `(updated_at DESC,id DESC)`:

```json
{
  "api_version": "1",
  "scope": "demo",
  "conversations": [{"id":"conv-demo-a","title":"新对话","title_source":"default","scope":"demo","timezone":"Asia/Shanghai","created_at":"2026-10-13T03:05:00Z","updated_at":"2026-10-13T03:05:00Z","last_message_preview":null,"version":1}],
  "next_cursor": null,
  "has_more": false
}
```

Cursors are opaque URL-safe strings, validated for endpoint, scope, filters,
timezone and ordering. Client must not construct them. Concurrent new messages
can reorder conversations; refresh page 1 and deduplicate by ID.

### POST /conversations

```json
{"scope":"demo","timezone":"Asia/Shanghai"}
```

201; `Location: /api/assistant/conversations/conv-demo-a?scope=demo`:

```json
{"api_version":"1","conversation":{"id":"conv-demo-a","title":"新对话","title_source":"default","scope":"demo","timezone":"Asia/Shanghai","created_at":"2026-10-13T03:05:00Z","updated_at":"2026-10-13T03:05:00Z","last_message_preview":null,"version":1}}
```

New-conversation creation is not inferred from sending to a missing ID. No
automatic retry of a timed-out create request; refetch list first. It creates
an empty conversation and zero messages/events.

### PATCH /conversations/{id}?scope=demo

```json
{"title":"宝宝日常记录","expected_version":1}
```

`expected_version` is optional for title renaming: when omitted last write wins;
when present reject mismatch with 409. Cannot edit timezone/scope. 200:

```json
{"api_version":"1","conversation":{"id":"conv-demo-a","title":"宝宝日常记录","title_source":"manual","scope":"demo","timezone":"Asia/Shanghai","created_at":"2026-10-13T03:05:00Z","updated_at":"2026-10-13T03:05:30Z","last_message_preview":null,"version":2}}
```

### DELETE /conversations/{id}?scope=demo

Bodyless. 200:

```json
{"api_version":"1","deleted":true,"conversation_id":"conv-demo-a","care_events_preserved":true}
```

Repeated DELETE of an absent/wrong-scope ID is 404. The UI clears selection and
pending polls for that ID and chooses another existing conversation or shows an
empty view. Deletion racing completion serializes at DB commit: events committed
before deletion survive; completion losing to deletion saves nothing. Memory
queue entries are cancelled, and running inference results are discarded at commit.
There is no promise of immediately interrupting an already running GPU kernel.

### Switching conversations

No global active-conversation API. UI owns `selected_conversation_id` and scope,
then calls GET messages for the selected ID. Cancels obsolete UI polls or checks
response.conversation.id before applying updates. Sending always includes the ID
in its URL, so two browser tabs cannot change each other's model context.
On refresh/login re-fetch conversations and restore the selection only if it
still exists in that scope. Never send an old draft automatically to a new ID.

### GET /conversations/{id}/messages?scope=demo&limit=50&cursor=...

Initial page is latest 50 messages. Messages within every page are chronological
`created_at ASC,id ASC`; `next_cursor` pages toward **older** messages (prepend
older pages in UI). Cursor is exclusive, anchored to the oldest returned row.
200:

```json
{
  "api_version":"1",
  "conversation":{"id":"conv-demo-a","title":"今天上午11点宝宝喝了150ml奶","title_source":"auto","scope":"demo","timezone":"Asia/Shanghai","created_at":"2026-10-13T03:05:00Z","updated_at":"2026-10-13T03:06:00Z","last_message_preview":"已记录本次喂奶：11:00，150 ml。","version":3},
  "messages":[
    {"id":"msg-user-a1","conversation_id":"conv-demo-a","role":"user","content":"今天上午11点宝宝喝了150ml奶","created_at":"2026-10-13T03:05:00Z","status":"completed","client_message_id":"frontend-uuid-001","turn_id":"turn-a1"},
    {"id":"msg-assistant-a1","conversation_id":"conv-demo-a","role":"assistant","content":"已记录本次喂奶：11:00，150 ml。","created_at":"2026-10-13T03:06:00Z","status":"completed","client_message_id":null,"turn_id":"turn-a1"}
  ],
  "pending_turn":null,
  "next_cursor":null,
  "has_more":false
}
```

`pending_turn` when present contains `user_message_id`, `status`, `poll_url` and
`poll_after_ms`, same scope/conversation; only one active turn per conversation.

### POST /conversations/{id}/messages?scope=demo

```json
{"content":"今天上午11点宝宝喝了150ml奶","client_message_id":"frontend-uuid-001","timezone":"Asia/Shanghai"}
```

`timezone` optional, default stored conversation timezone. `retry` optional boolean
(default false), only meaningful for a previously failed turn. Explicit timezone
becomes part of the original request hash; inferred original timezone is retained
on retries. Content/timezone change with same key is 409 `idempotency_conflict`.
The reference time is acceptance time, retained on retry: yesterday/today must
not change meaning when retried after midnight.

First acceptance 202; repeated identical queued/running request 202 without
starting a second job. Completed identical request replays HTTP 200 with the
original messages/actions, `replayed:true`. Failed identical request without
`retry:true` replays HTTP 200 failed result; `retry:true` can claim it again only
if retryable and no competing active turn. Return 202, increment attempt; never
insert a second user message. Frontend must not silently retry generation.

```json
{
  "api_version":"1",
  "status":"queued",
  "user_message":{"id":"msg-user-a1","conversation_id":"conv-demo-a","role":"user","content":"今天上午11点宝宝喝了150ml奶","created_at":"2026-10-13T03:05:00Z","status":"queued","client_message_id":"frontend-uuid-001","turn_id":"turn-a1"},
  "assistant_message":null,
  "record_actions":[],
  "conversation":{"id":"conv-demo-a","title":"今天上午11点宝宝喝了150ml奶","title_source":"auto","scope":"demo","timezone":"Asia/Shanghai","created_at":"2026-10-13T03:05:00Z","updated_at":"2026-10-13T03:05:00Z","last_message_preview":"今天上午11点宝宝喝了150ml奶","version":2},
  "facts":null,
  "error":null,
  "replayed":false,
  "attempt":1,
  "poll_url":"/api/assistant/conversations/conv-demo-a/messages/msg-user-a1?scope=demo",
  "poll_after_ms":1500
}
```

**Required additional polling route**, avoiding long HTTP generation timeouts:
GET `/conversations/{id}/messages/{user_message_id}?scope=demo` returns HTTP 200
with the same envelope above. `status` is queued/running/completed/failed.
`poll_after_ms` is 1500 while pending and null when terminal. After deletion it is
404, not a terminal success. Only user-message IDs are valid poll targets.
On network loss keep polling; do not create another client ID for the same send.

Completed response (also the response of a completed POST replay):

```json
{
  "api_version":"1",
  "status":"completed",
  "user_message":{"id":"msg-user-a1","conversation_id":"conv-demo-a","role":"user","content":"今天上午11点宝宝喝了150ml奶","created_at":"2026-10-13T03:05:00Z","status":"completed","client_message_id":"frontend-uuid-001","turn_id":"turn-a1"},
  "assistant_message":{"id":"msg-assistant-a1","conversation_id":"conv-demo-a","role":"assistant","content":"已记录本次喂奶：11:00，150 ml。","created_at":"2026-10-13T03:06:00Z","status":"completed","client_message_id":null,"turn_id":"turn-a1"},
  "record_actions":[{"action_id":"action-a1-0","operation":"create","status":"saved","reason_code":"explicit_past_event","message":"已记录喂奶","event":{"id":"care-001","event_type":"feeding","scope":"demo","occurred_at":"2026-10-13T03:00:00Z","end_at":null,"amount_ml":150,"diaper_type":null,"notes":"","source":"parent_chat","status":"confirmed","timezone":"Asia/Shanghai","duration_seconds":null,"source_message_id":"msg-user-a1","source_reference_id":"source-001","version":1,"created_at":"2026-10-13T03:06:00Z","updated_at":"2026-10-13T03:06:00Z","deleted_at":null},"proposed_event":null,"missing_fields":[],"target_event_id":null,"expected_version":null,"expires_at":null}],
  "conversation":{"id":"conv-demo-a","title":"今天上午11点宝宝喝了150ml奶","title_source":"auto","scope":"demo","timezone":"Asia/Shanghai","created_at":"2026-10-13T03:05:00Z","updated_at":"2026-10-13T03:06:00Z","last_message_preview":"已记录本次喂奶：11:00，150 ml。","version":3},
  "facts":null,"error":null,"replayed":false,"attempt":1,
  "poll_url":"/api/assistant/conversations/conv-demo-a/messages/msg-user-a1?scope=demo",
  "poll_after_ms":null
}
```

Failed polling response: same envelope, failed user message, assistant_message null,
record_actions empty, facts null, and:

```json
{"status":"failed","error":{"code":"model_unavailable","message":"本地模型暂不可用，请稍后重试。","retryable":true,"details":[],"retry_after_seconds":5}}
```

This small object illustrates the changed fields, not a different top-level
response shape. A failure may not report saved events or “已记录成功”. Replayed
saved actions serialize the **current** event including later edits/deletion,
not a stale event snapshot; `status:saved` describes the historical action and
does not undelete the event. `conversation` is likewise current, while the
original assistant text and action IDs remain immutable.

## 5. Record action cards and ambiguity confirmation

`record_actions` is an ordered array; each action has a stable action_id. Enums:
operation=create/update/delete/none; status=saved/needs_confirmation/not_saved.
`saved` requires committed event+revision. `needs_confirmation` creates no care
event yet. `not_saved` has no pending action to confirm. For update/delete, saved
returns the current event (including deleted state); all cards are scope-bound.

Example: “刚才冲了150ml奶，宝宝好像喝了一些” is not a recorded intake:

```json
{
  "action_id":"action-a2-0","operation":"create","status":"needs_confirmation",
  "reason_code":"actual_intake_unknown","message":"实际喝了多少、什么时间喝完？",
  "event":null,
  "proposed_event":{"event_type":"feeding","scope":"demo","occurred_at":null,"end_at":null,"amount_ml":null,"diaper_type":null,"notes":"","timezone":"Asia/Shanghai"},
  "missing_fields":["occurred_at","amount_ml"],"target_event_id":null,
  "expected_version":null,"expires_at":"2026-10-14T03:06:00Z"
}
```

Future plan example (“明天下午三点准备换尿布”):

```json
{"action_id":"action-a3-0","operation":"none","status":"not_saved","reason_code":"future_plan","message":"这是未来计划，未写入已发生记录。","event":null,"proposed_event":null,"missing_fields":[],"target_event_id":null,"expected_version":null,"expires_at":null}
```

Correction example (“上一条实际喝了120ml，不是150ml”): propose update of a
**uniquely identified event**, including expected version. If no unique target,
ask which event; do not select by proximity alone across conversations. v1 chat
updates/deletes always require parent confirmation or a deliberate form PATCH/
DELETE; clear complete new past events may auto-save. New chat clarification
can reference one explicit pending action with optional `record_action_id` in
message POST. Without an ID, multiple pending proposals are never auto-selected.

Required additional confirmation route:
POST `/conversations/{id}/messages/{user_message_id}/record-actions/{action_id}/confirm?scope=demo`

```json
{"decision":"confirm","fields":{"occurred_at":"2026-10-13T03:00:00Z","amount_ml":120}}
```

`decision` confirm/dismiss. `fields` is a partial merge of user edits into validated
proposed fields; scope/source/target IDs cannot be overridden. Unknown fields are
422. Confirm validates dates and required fields again, applies target version
check, and atomically creates/updates/soft-deletes one event and its revision.
Proposal expires after 24h; deleted conversation/turn is 404. A same-body repeat
replays 200 and never writes twice; differing confirmation body after resolution
is 409 `idempotency_conflict`. Dismiss writes no event. 200 example:

```json
{
  "api_version":"1",
  "resolution":"confirmed",
  "record_action":{"action_id":"action-a2-0","operation":"create","status":"saved","reason_code":"parent_confirmed","message":"已记录喂奶","event":{"id":"care-002","event_type":"feeding","scope":"demo","occurred_at":"2026-10-13T03:00:00Z","end_at":null,"amount_ml":120,"diaper_type":null,"notes":"","source":"parent_chat","status":"confirmed","timezone":"Asia/Shanghai","duration_seconds":null,"source_message_id":"msg-user-a2","source_reference_id":"source-002","version":1,"created_at":"2026-10-13T03:07:00Z","updated_at":"2026-10-13T03:07:00Z","deleted_at":null},"proposed_event":null,"missing_fields":[],"target_event_id":null,"expected_version":null,"expires_at":null}
}
```

Dismiss response has resolution=dismissed, record_action.status=not_saved,
reason_code=parent_dismissed and event=null. Original assistant text is not
rewritten; the action card and subsequent polling reflect resolution.

## 6. Care events CRUD, time rules and audit

Four confirmed types in v1; nullable inapplicable fields are always present:

| Type | Required fields | Rules |
|---|---|---|
| feeding | occurred_at, amount_ml | actual consumed quantity only; 1–2000 integer; no inferred bottle amount; end_at/diaper_type null |
| diaper | occurred_at, diaper_type | wet/dirty/mixed/unknown; unspecified kind explicitly unknown; amount_ml/end_at null |
| sleep | occurred_at, end_at | end > start, max 24 hours, endpoints already occurred; amount_ml/diaper_type null |
| activity | occurred_at, notes | explicit completed activity, nonempty notes; optional end_at > start; amount_ml/diaper_type null |

All write timestamps must include a UTC offset, convert to UTC, and cannot be
later than server now+60 seconds clock tolerance. No guessed timestamps: “刚才”
without a precise time prompts for time; “今天” without hour prompts; “上午11点”
without date can be resolved to today's date only if the wording/context makes
it unambiguous and already past. “昨晚23:40到今天00:20” is valid across midnight;
“昨天23:40到00:20” may resolve end to next day only when the linguistic context
explicitly establishes an overnight period, otherwise confirm. Retain original
acceptance reference time/timezone for relative dates. No v1 unfinished sleeps
with null end_at are saved as complete events. Denials, hypothetical discussion,
questions, negations and future plans are not new events. Evidence text spans
must refer to the current message, not older context as if newly reported.

### GET /care-events?scope=demo&from=2026-10-13&to=2026-10-13&timezone=Asia%2FShanghai&limit=50

from/to are inclusive local calendar days. Internally use `[from 00:00, day after
to 00:00)` with timezone-aware day boundaries (including DST), not `+86400`.
If both absent use today in selected/default timezone; if only one is present
422. `event_type` optional single enum filter. Active confirmed records only;
no route for unbounded deleted/audit data. Keyset `(occurred_at DESC,id DESC)`.
Sleep/activity intervals are included on overlap; instantaneous records use
occurred_at within the half-open window. Return original event endpoints and
derived full duration; window-clipped durations are only facts-summary fields.

```json
{
  "api_version":"1","scope":"demo","timezone":"Asia/Shanghai",
  "from":"2026-10-13","to":"2026-10-13",
  "events":[{"id":"care-003","event_type":"sleep","scope":"demo","occurred_at":"2026-10-13T05:30:00Z","end_at":"2026-10-13T06:20:00Z","amount_ml":null,"diaper_type":null,"notes":"午睡","source":"parent_chat","status":"confirmed","timezone":"Asia/Shanghai","duration_seconds":3000,"source_message_id":"msg-user-a4","source_reference_id":"source-003","version":1,"created_at":"2026-10-13T06:25:00Z","updated_at":"2026-10-13T06:25:00Z","deleted_at":null}],
  "next_cursor":null,"has_more":false
}
```

Required additional manual-create route POST `/care-events` allows form entry
and corrected values when a conversation has already been deleted. Requires
body scope, timezone, event_type and type-specific fields:

```json
{"scope":"demo","timezone":"Asia/Shanghai","event_type":"diaper","occurred_at":"2026-10-13T06:00:00Z","diaper_type":"unknown","notes":"换尿布","client_event_id":"form-uuid-001"}
```

201 response (same field set as other event responses):

```json
{"api_version":"1","replayed":false,"event":{"id":"care-004","event_type":"diaper","scope":"demo","occurred_at":"2026-10-13T06:00:00Z","end_at":null,"amount_ml":null,"diaper_type":"unknown","notes":"换尿布","source":"parent_manual","status":"confirmed","timezone":"Asia/Shanghai","duration_seconds":null,"source_message_id":null,"source_reference_id":"source-004","version":1,"created_at":"2026-10-13T06:05:00Z","updated_at":"2026-10-13T06:05:00Z","deleted_at":null}}
```

Event object follows section 6, source=parent_manual, source_message_id=null. `client_event_id` is required and
uses same format limits as client_message_id; unique `(scope,client_event_id)`
is encoded into `extraction_key` with a distinct `manual:` prefix. Same body
replay 200 with `replayed:true`, conflict 409. Auto `extraction_key` is
`turn:<turn_id>:<ordinal>`; confirmations reuse that slot. Hash exact validated
original request for idempotency; persist hash in an additive
`assistant_write_requests(scope,client_event_id,request_hash,event_id)` table.
No derived field (status/source/version/source_reference_id) is accepted from clients.

### PATCH /care-events/{id}?scope=demo

```json
{"expected_version":1,"amount_ml":120,"notes":"更正实际饮用量"}
```

Partial update, expected_version **required**, event_type and scope immutable.
Editable fields: occurred_at, end_at, amount_ml, diaper_type, notes, timezone.
Validate the merged full event; changing timezone never reinterprets already
stored UTC instants. Updating a deleted/absent/wrong-scope event returns 404.
Version mismatch returns 409; reload records before retry. 200:

```json
{"api_version":"1","event":{"id":"care-001","event_type":"feeding","scope":"demo","occurred_at":"2026-10-13T03:00:00Z","end_at":null,"amount_ml":120,"diaper_type":null,"notes":"更正实际饮用量","source":"parent_chat","status":"confirmed","timezone":"Asia/Shanghai","duration_seconds":null,"source_message_id":"msg-user-a1","source_reference_id":"source-001","version":2,"created_at":"2026-10-13T03:06:00Z","updated_at":"2026-10-13T03:10:00Z","deleted_at":null}}
```

### DELETE /care-events/{id}?scope=demo&expected_version=2

Bodyless, expected_version **required** query integer. No inference.
Records a deletion revision; no cascade to chat. 200:

```json
{"api_version":"1","deleted":true,"event_id":"care-001","version":3,"deleted_at":"2026-10-13T03:11:00Z"}
```

Repeat delete returns 200 for an already deleted same-scope event if the supplied
version equals the current deleted version or the immediately prior version
used to delete it; other versions return 409. Deleted event never reappears in
list/summaries. No restore/purge endpoint in v1. A subsequent chat “forget that”
can propose a deletion, but cannot quietly destroy other saved events.

## 7. Sleep and bounded facts retrieval

**Owner-requested night window (2026-10-15):** “昨晚” / “昨天晚上” means
20:00 yesterday through 10:00 today in the selected IANA timezone. “前晚” uses
the same window beginning one day earlier. Only confirmed parent-reported sleep
overlapping that window is counted; clip both edges and take the interval union.
Awake gaps and unrecorded periods do not become sleep, and camera duration is
not added. Stored event endpoints and ordinary calendar-day summaries are unchanged.

The additive optional query field `period=night` is supported by `sleep_summary`
intents and GET `/sleep-summary`. `from=to` identifies the night's **starting
date**; multi-date night requests are rejected with 422. Without `period`, or
with `period=day`, existing calendar-day semantics remain. Night responses add
`period: "night"` and `window: {start_at, end_at, has_ended}`; timestamps remain
RFC3339 UTC. Before 10:00, return confirmed records so far and an unfinished-window
warning. Night chat answers state the actual local window and reported duration.
This changes query interpretation only, not recording start/end automatically
and not direct model tool calling.

Facts tools are internal Python functions, not arbitrary model SQL. The service
may accept a validated query intent with allowed kind=sleep_summary/care_list/
feeding_summary, exact scope and bounded local dates; it computes facts before
asking Qwen to explain them. The model never gets the full DB. Limit returned
fact evidence to 100 events/50 observations per turn and at most 31 days; paginate
or flag truncation, never produce a complete total from truncated rows. Aggregate
with bounded indexed SQL, not only a page of care events.

Demo queries only Demo care rows. Do not read live infant/adult visual data into
Demo. There is no synthesized Demo visual history unless a future explicit
fixture source is added. Real queries only Real care rows and visual
`observation_mode='infant'`; exclude adult_test/legacy_unknown. Do not default to
the active visual mode. Query visual DB via read-only short connections; never
call a visual method which resets intervals or writes settings.

Manual sleep total is Python interval **union**, clipped to date window, so
multiple parent reports overlapping each other do not double-count. Return
parent_reported_seconds separately from camera_observed_seconds. Never add
manual and camera durations. No correlation/causal claim about feeding and sleep
from sparse observational data; explain insufficient evidence and sample coverage.

Known safety boundary: current visual intervals can span offline/unobserved
time. For v1, even closed infant intervals are not certified reliable by mere
mode/end_ts filtering. Return camera_observed_seconds=null,
reliability=unverified, reason_code=monitoring_continuity_unverified. Open intervals
are excluded from definite totals. Selected observations may support qualified
narrative about **observations**, not precise continuous sleep duration. Return
coverage/unknown gaps as unknown, never zero sleep. If manual events are absent,
parent_reported_seconds=null and reliability=insufficient_data, not a fabricated
0. With confirmed parent records the number is a reported total, not complete-day
clinical truth. Do not reuse `/api/timeline` totals as verified facts.

Required additional inspectable GET `/sleep-summary?scope=real&from=2026-10-12&to=2026-10-12&timezone=Asia%2FShanghai`
returns the same facts object that can appear in a completed chat response:

```json
{
  "api_version":"1",
  "scope":"real","timezone":"Asia/Shanghai","from":"2026-10-12","to":"2026-10-12",
  "parent_sleep":{"seconds":null,"event_count":0,"reliability":"insufficient_data","source":"parent_reported","method":"clipped_interval_union"},
  "camera_sleep":{"seconds":null,"reliability":"unverified","source":"camera_observation","observation_mode":"infant","reason_code":"monitoring_continuity_unverified"},
  "combined_sleep_seconds":null,
  "coverage":{"complete_day":false,"unknown_seconds":null},
  "warnings":["缺少家长确认的睡眠记录，摄像头监测连续性尚未验证，无法可靠计算全天睡眠时长。"]
}
```

For Demo camera_sleep.reason_code=demo_visual_data_unavailable, observation_mode=null.
An empty Real archive is not permission to import Demo rows. facts in chat uses
this bounded structure (without repeating api_version) and declared query/evidence
metadata. All displayed numbers come from backend calculation. v1 does not
provide medical diagnosis, individualized treatment or clinical thresholds.

## 8. Internal Qwen /chat contract and generation safety

**Owner-approved adjustment (2026-10-15, Plan A — rule-first records):** the backend
(`assistant_rules.py`) recognizes common record and query phrasings and builds the same
action/query proposals without calling the model; they still pass every `plan_actions`
guard below. Only unrecognized messages reach `/chat`, and the model is used for chat
answers only: its `actions`/`query` are discarded after a structure check, and fact answers
are rendered by Python without a second model call. Public APIs, fields and DB semantics are unchanged.

Loopback worker only; default existing 127.0.0.1:8010. `/analyze` remains JPEG
with the same fields, headers and Infant/Adult prompt semantics. No changes to
camera capture, observation modes or their scheduling intervals in Phase 0.
`/chat` is accessed only by AssistantService. Browser must not invoke it.

Internal POST /chat, JSON, body max 32KiB:

```json
{
  "request_id":"turn-a1-attempt-1",
  "messages":[{"role":"user","content":"今天上午11点宝宝喝了150ml奶"}],
  "scope":"demo","timezone":"Asia/Shanghai",
  "reference_at":"2026-10-13T03:05:00Z",
  "facts":null,
  "max_new_tokens":256
}
```

Only user/assistant history accepted from this internal client; no caller-defined
system role. Worker owns fixed chat system prompt independent of visual prompt.
Service loads up to 6 completed recent turns from that exact conversation and
current user input (max 13 messages). Tokenize on CPU, limit entire input to
2048 tokens including system prompt, facts and current user message. Drop oldest
complete turn pairs first. If current message alone cannot fit, fail validation;
never truncate it silently. Max output 256 tokens, deterministic generation.
Structured extraction/actions limited to 4 per turn. Oversized/partial/truncated
JSON fails `invalid_model_output`; no care writes and no pretend answer. A future
larger budget requires scheduling and latency validation first.

Internal 200:

```json
{
  "model":"Qwen3-VL-2B-Instruct",
  "request_id":"turn-a1-attempt-1",
  "queue_wait_ms":120,
  "infer_ms":4200,
  "input_tokens":380,"output_tokens":132,
  "result":{
    "answer":"可以帮你整理这条喂奶记录。",
    "actions":[{"operation":"create","event_type":"feeding","occurred_at":"2026-10-13T03:00:00Z","end_at":null,"amount_ml":150,"diaper_type":null,"notes":"","event_state":"past","evidence":"宝宝喝了150ml奶","missing_fields":[],"target_event_id":null}],
    "query":null
  }
}
```

Numeric timings are illustrative, **not measured chat latency**. Model JSON
cannot choose scope, source, DB IDs, execute tools or assert persistence. Backend
validates evidence/time/quantity and enum semantics; model confidence alone is
not a write authorization. Model-generated persistence claims are forbidden in
its answer and rejected/replaced by backend-generated acknowledgement text only
after commit. Extraction is not successful merely because JSON parses. Strict
field schema plus conservative semantic guards reject future/negated/preparation
claims and ask clarification rather than guessing. Unsupported claims do not
become care rows. Facts query intents may invoke only allowlisted Python queries;
no recursive tool loop, at most one bounded facts query and one additional model
explanation call (each call is a separately admitted scheduler job).

Internal failures use the unified error.code/message/retryable structure, no
HTTP session/CSRF (loopback IPC). `/analyze` keeps its existing error shape for
compatibility, but scheduler overload/timeouts add safe status 429/503/504.
Do not serialize model outputs into ordinary logs; metrics expose counts/timings only.

## 9. Independent inference scheduling review

Single serial GPU owner, separate queues `vision` and `chat`. Acquire a scheduler
slot **before** processor/model input GPU transfer and retain it through generate,
CUDA synchronization, decode/copy-to-CPU and tensor cleanup. CPU image decode and
request validation may occur before admission with bounded HTTP concurrency.
One loaded processor/model; protect processor operations too if they mutate
shared state. No second model or overlapping GPU allocations. Read-only health
never acquires the GPU slot and remains responsive.

Admission/dispatch rules (owner-approved coursework prototype revision, 2026-10-15):

The owner has replaced Vision First / safe-gap admission with **Chat First,
latest pending vision, and a 120-second vision starvation guard**. Only this
section's internal scheduling/deployment rules change; all 13 public APIs,
request/response fields, authentication, scopes and SQLite semantics remain v1.
No `unprofiled_chat` flag is used. Existing 30/60/15-second observation intervals
and the 5-second failure retry remain unchanged.

1. One non-preemptible model owner. Waiting capacities: one latest vision and
   two chats. A newly submitted vision request replaces the waiting vision
   request (internal `/analyze` returns 409 for the superseded caller); it never
   cancels the active job. Accepted chats remain FIFO with bounded wait times.
2. At each idle boundary choose chat first, unless a vision request is waiting
   and 120 seconds have elapsed since the last successful, parsed visual result.
   Then choose vision once. Before the first successful result, a waiting vision
   request also has precedence. A failed vision attempt does not refresh success
   time. With no waiting vision, chat can proceed even when vision is overdue.
3. Running generation is never preempted. A newly arriving vision can wait for
   the current chat, and chat can wait for a running vision. This is a prototype
   priority rule, not a hard deadline or a promise of unchanged observed cadence.
4. Enforce queue wait deadline: vision 10s, chat 30s, and measured runtime budgets:
   vision <= existing observer timeout; chat 8s target cap with stopping criteria
   and max_new_tokens. Maximum total chat job duration 45s; queued expiration removes
   the queue entry. A socket timeout never releases a still-running generation slot.
5. Generation is non-preemptible at the kernel level. Stopping criteria are checked
   between token steps; first-token/prefill and CUDA kernels may exceed budget.
   Therefore these limits are **soft runtime guards**, not verified hard realtime
   guarantees. On deadline expiry return safe failure once generation stops, retaining
   GPU ownership until cleanup. A stuck GPU operation fails admission/health; no
   simultaneous fallback generation or force-killing the production model.
6. Assistant jobs use their own background executor (pending 4, one worker), short
   transactions and a separate DB. A slow chat neither blocks camera callbacks nor
   occupies the visual DB lock. Conversation deletion/access revocation must be
   checked before dispatch and again before completion. Revocation does not erase
   already committed data; a discarded running job writes nothing.
7. `/health` may add optional scheduler counters (`running_kind`, pending counts,
   cumulative failures/timing histograms) without changing existing `ok/model/error`.
   Never report prompts, chat text or child records there.

Relative next-vision deadline hints are no longer used for admission. The camera
keeps its existing recent-frame selection; only submitted waiting model requests
are replaced, not every captured video frame. Queue expiration removes the job;
an expired or superseded frame never starts GPU work or writes a visual result.

**Deployment gate (owner revised 2026-10-15):** explicit maintenance start is still
required. Use one real Qwen3-VL-2B instance, representative Demo-only chat/record
cases and a few observed chat waits. Verify camera/vision, LAN/Tailscale login and
one actual phone browser; no stress test or P50/P95 campaign is required. Keep
login/CSRF, private data and credentials off Git, and the model on loopback. Retain
the working candidate after acceptance; otherwise use the rehearsed restore
command. Never force-kill a model or load another before the previous one exits.

## 10. Gemini integration checklist and frozen route inventory

| Method | Path after `/api/assistant` | Scope | Purpose |
|---|---|---|---|
| GET | `/conversations` | query, demo default | history list |
| POST | `/conversations` | body required | empty conversation |
| PATCH | `/conversations/{id}` | query required | rename |
| DELETE | `/conversations/{id}` | query required | delete conversation/messages, retain events |
| GET | `/conversations/{id}/messages` | query required | load/switch, messages pagination |
| POST | `/conversations/{id}/messages` | query required | accept idempotent async turn |
| GET | `/conversations/{id}/messages/{user_message_id}` | query required | poll accepted turn |
| POST | `/conversations/{id}/messages/{user_message_id}/record-actions/{action_id}/confirm` | query required | confirm/dismiss ambiguous create/correction/delete |
| GET | `/care-events` | query, demo default | bounded event list |
| POST | `/care-events` | body required | manual event create with idempotency |
| PATCH | `/care-events/{id}` | query required | versioned correction |
| DELETE | `/care-events/{id}` | query required | versioned soft delete |
| GET | `/sleep-summary` | query, demo default | inspectable conservative sleep facts |

Frontend must support queued/running/completed/failed; 202 is not a final answer.
Keep scope in every resource URL and retain client_message_id until resolved.
Poll interval defaults to 1500ms; pause in hidden tabs and resume on focus.
Do not infer successful record cards from assistant free text; use record_actions.
For card confirmation show exact time/quantity and event source. Handle 409 without
silently overwriting another tab's edits. After conversation deletion preserve
care-event list; after event deletion refresh cards and summaries. Show Demo badge
persistently, never use the visual subject switch to decide care scope. Display UTC
instants in the user's selected timezone. Refresh scope list before restoring a
stored selected ID. Historical context and current pending polls never cross scopes.

New routes beyond the requested minimum are explicitly frozen here because async
polling, ambiguity confirmation, standalone form entry and auditable sleep facts
otherwise lack complete UI interfaces. Backend changes require a contract revision;
Gemini should not implement an alternate implicit message route or undocumented
long-held streaming connection.

## 11. Phase 1 acceptance plan (not completed in Phase 0)

- Migration on an existing visual DB layout without deleting/rebuilding it; re-open
  Assistant DB preserves conversations/messages/events; failed migration is isolated.
- A/B creation, independent contexts, chronological paging, stable recent ordering,
  rename, delete A preserving B, owner logout/service restart persistence.
- Delete queued/running conversation in a coordinated test; delayed inference
  completion must not resurrect it or write its uncommitted events.
- Repeated client_message_id before/during/after completion and concurrent retries;
  conflicting body 409; one event/revision only. Restarted failed turns need explicit retry.
- Confirm/reconfirm/dismiss/expire proposals, version conflicts, stale poll and deleted
  source linkage; corrections/deletions preserve structured audit without chat bodies.
- Feeding actual consumed vs prepared amount; diaper unknown; explicit sleep range,
  cross-midnight, missing time, future plans, negations, ambiguous targets and confirmation.
- Demo/Real isolation for all endpoints, DB triggers, context, summaries and wrong-scope 404.
- Empty/incomplete visual data and open intervals never yield misleading sleep totals;
  overlapping manual sleeps union correctly, DST dates, no manual+camera sum.
- Model not ready/timeout/invalid JSON/too many actions/storage commit failure: no false
  acknowledgement and no partial care writes. Retryability matches documented states.
- Fake Engine scheduling: maximum GPU critical-section concurrency 1, GPU input
  preparation included, bounded queues, vision priority, chat progress under spare
  capacity, explicit rejection under saturation, token/context budgets and cleanup.
- HTTP integration: unauthenticated/invalid Host/forbidden Origin/missing CSRF denied
  for POST/PATCH/DELETE; access gate, malformed JSON, large bodies, invalid scope/cursors,
  query bounds and unsupported methods preserve the error envelope.
- Real Qwen Mandarin multi-turn/extraction and latency/GPU-memory/camera cadence acceptance
  remain separate hardware validation. Mock tests are not evidence of model quality.

No Phase 1 endpoints, model changes or live deployment are claimed by this contract.
