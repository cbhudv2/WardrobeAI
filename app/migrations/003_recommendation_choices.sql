ALTER TABLE recommendations ADD COLUMN choice_group_id TEXT;

UPDATE recommendations SET choice_group_id = id WHERE choice_group_id IS NULL;

DROP INDEX IF EXISTS uq_recommendation_pending_chat;

CREATE INDEX idx_recommendation_pending_chat
    ON recommendations(owner_user_id, chat_id, status, created_at DESC);

CREATE INDEX idx_recommendation_choice_group
    ON recommendations(choice_group_id, status);
