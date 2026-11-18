from app.services.conversation import ConversationMemory


def test_conversation_memory_returns_recent_messages_in_order(database) -> None:
    memory = ConversationMemory(database, retained_messages=10)
    for number in range(6):
        memory.append_exchange(1, 10, f"user {number}", f"assistant {number}")

    recent = memory.recent(1, 10, limit=4)

    assert recent == [
        {"role": "user", "content": "user 4"},
        {"role": "assistant", "content": "assistant 4"},
        {"role": "user", "content": "user 5"},
        {"role": "assistant", "content": "assistant 5"},
    ]
    assert database.connect().execute("SELECT count(*) FROM conversation_messages").fetchone()[
        0
    ] == 10


def test_conversation_memory_can_be_cleared(database) -> None:
    memory = ConversationMemory(database)
    memory.append_exchange(1, 10, "I want more variety", "Let's add some color.")

    assert memory.clear(1, 10) == 2
    assert memory.recent(1, 10) == []
