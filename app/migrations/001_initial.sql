CREATE TABLE telegram_users (
    user_id INTEGER PRIMARY KEY,
    private_chat_id INTEGER NOT NULL UNIQUE,
    is_authorized INTEGER NOT NULL DEFAULT 1 CHECK (is_authorized IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_seen_at TEXT
);

CREATE TABLE wardrobe_items (
    id TEXT PRIMARY KEY,
    owner_user_id INTEGER NOT NULL REFERENCES telegram_users(user_id),
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    category TEXT NOT NULL,
    color TEXT NOT NULL CHECK (length(color) BETWEEN 1 AND 120),
    roles_json TEXT NOT NULL,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    legacy_section TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE INDEX idx_wardrobe_owner_active
    ON wardrobe_items(owner_user_id, deleted_at, category);

CREATE TABLE recommendations (
    id TEXT PRIMARY KEY,
    owner_user_id INTEGER NOT NULL REFERENCES telegram_users(user_id),
    chat_id INTEGER NOT NULL,
    target_date TEXT NOT NULL,
    top_item_id TEXT NOT NULL REFERENCES wardrobe_items(id),
    bottom_item_id TEXT NOT NULL REFERENCES wardrobe_items(id),
    footwear_item_id TEXT NOT NULL REFERENCES wardrobe_items(id),
    outerwear_item_id TEXT REFERENCES wardrobe_items(id),
    weather_json TEXT NOT NULL,
    occasion TEXT NOT NULL,
    mood TEXT,
    constraints_json TEXT NOT NULL DEFAULT '{}',
    rationale TEXT NOT NULL,
    styling_adjustment TEXT,
    status TEXT NOT NULL,
    telegram_message_id INTEGER,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX uq_recommendation_pending_chat
    ON recommendations(chat_id) WHERE status = 'pending';

CREATE TABLE worn_outfits (
    id TEXT PRIMARY KEY,
    owner_user_id INTEGER NOT NULL REFERENCES telegram_users(user_id),
    worn_date TEXT NOT NULL,
    top_item_id TEXT NOT NULL REFERENCES wardrobe_items(id),
    bottom_item_id TEXT NOT NULL REFERENCES wardrobe_items(id),
    footwear_item_id TEXT NOT NULL REFERENCES wardrobe_items(id),
    outerwear_item_id TEXT REFERENCES wardrobe_items(id),
    mood TEXT,
    occasion TEXT,
    recommendation_id TEXT UNIQUE REFERENCES recommendations(id),
    confirmed_at TEXT NOT NULL,
    legacy_key TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_worn_owner_date ON worn_outfits(owner_user_id, worn_date DESC);
CREATE INDEX idx_worn_pair_date
    ON worn_outfits(owner_user_id, top_item_id, bottom_item_id, worn_date DESC);

CREATE TABLE pending_actions (
    id TEXT PRIMARY KEY,
    callback_token TEXT NOT NULL UNIQUE,
    owner_user_id INTEGER NOT NULL REFERENCES telegram_users(user_id),
    chat_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    target_item_id TEXT REFERENCES wardrobe_items(id),
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_pending_action_token ON pending_actions(callback_token, status);

CREATE TABLE scheduled_sends (
    id TEXT PRIMARY KEY,
    schedule_kind TEXT NOT NULL,
    local_date TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    telegram_message_id INTEGER,
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(schedule_kind, local_date, chat_id)
);

CREATE TABLE processed_updates (
    update_id INTEGER PRIMARY KEY,
    processed_at TEXT NOT NULL
);

CREATE TABLE audit_events (
    id TEXT PRIMARY KEY,
    actor_user_id INTEGER,
    event_type TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    result TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX idx_audit_created_at ON audit_events(created_at DESC);

CREATE TABLE legacy_imports (
    source_name TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    record_count INTEGER NOT NULL,
    PRIMARY KEY(source_name, source_sha256)
);

