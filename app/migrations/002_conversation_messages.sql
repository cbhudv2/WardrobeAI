CREATE TABLE conversation_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL REFERENCES telegram_users(user_id),
    chat_id INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 6000),
    created_at TEXT NOT NULL
);

CREATE INDEX idx_conversation_messages_chat
    ON conversation_messages(owner_user_id, chat_id, id DESC);
