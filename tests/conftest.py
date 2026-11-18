from pathlib import Path

import pytest

from app.database import Database
from app.legacy_import import import_legacy
from app.services.outfits import OutfitService
from app.services.wardrobe import WardrobeService

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def database(tmp_path: Path) -> Database:
    value = Database(tmp_path / "wardrobe.sqlite3")
    value.migrate()
    import_legacy(
        value,
        ROOT / "legacy_data" / "wardrobe.json",
        ROOT / "legacy_data" / "outfit_history.json",
        owner_user_id=1,
        private_chat_id=10,
    )
    yield value
    value.close()


@pytest.fixture
def wardrobe(database: Database) -> WardrobeService:
    return WardrobeService(database)


@pytest.fixture
def outfits(database: Database, wardrobe: WardrobeService) -> OutfitService:
    return OutfitService(database, wardrobe)
