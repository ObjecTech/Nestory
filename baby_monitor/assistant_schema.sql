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
