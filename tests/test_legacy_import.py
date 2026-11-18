from pathlib import Path

from app.database import Database
from app.legacy_import import import_legacy

ROOT = Path(__file__).resolve().parents[1]


def test_legacy_import_preserves_ids_and_is_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "wardrobe.sqlite3")
    database.migrate()

    first = import_legacy(
        database,
        ROOT / "legacy_data" / "wardrobe.json",
        ROOT / "legacy_data" / "outfit_history.json",
        owner_user_id=123,
        private_chat_id=456,
    )
    second = import_legacy(
        database,
        ROOT / "legacy_data" / "wardrobe.json",
        ROOT / "legacy_data" / "outfit_history.json",
        owner_user_id=123,
        private_chat_id=456,
    )

    connection = database.connect()
    ids = {row["id"] for row in connection.execute("SELECT id FROM wardrobe_items")}
    history_count = connection.execute("SELECT count(*) FROM worn_outfits").fetchone()[0]

    assert first == {"items": 23, "outfits": 7}
    assert second == {"items": 0, "outfits": 0}
    assert {"top_01", "top_17", "jeans_05", "shoe_04"} <= ids
    assert len(ids) == 23
    assert history_count == 7
    assert database.integrity_check()


def test_import_assigns_outerwear_roles(tmp_path: Path) -> None:
    database = Database(tmp_path / "wardrobe.sqlite3")
    database.migrate()
    import_legacy(
        database,
        ROOT / "legacy_data" / "wardrobe.json",
        ROOT / "legacy_data" / "outfit_history.json",
        owner_user_id=1,
        private_chat_id=1,
    )

    roles = (
        database.connect()
        .execute("SELECT roles_json FROM wardrobe_items WHERE id='top_12'")
        .fetchone()[0]
    )
    assert roles == '["outerwear"]'
