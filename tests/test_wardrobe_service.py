from app.domain.enums import ItemCategory, WearRole
from app.domain.schemas import WardrobeItemDraft, WardrobeItemPatch
from app.services.wardrobe import WardrobeService


def test_add_update_remove_use_confirmations(wardrobe: WardrobeService) -> None:
    action = wardrobe.draft_add(
        1,
        10,
        WardrobeItemDraft(
            name="Navy Crewneck Sweatshirt",
            category=ItemCategory.CREWNECK,
            color="Navy",
            roles={WearRole.MAIN_TOP},
        ),
    )
    item = wardrobe.confirm_action(action["token"], 1, 10)
    assert item is not None
    assert item.name == "Navy Crewneck Sweatshirt"

    update = wardrobe.draft_update(
        1,
        10,
        item.id,
        WardrobeItemPatch(color="Deep Navy"),
    )
    updated = wardrobe.confirm_action(update["token"], 1, 10)
    assert updated is not None
    assert updated.color == "Deep Navy"

    removal = wardrobe.draft_remove(1, 10, item.id)
    removed = wardrobe.confirm_action(removal["token"], 1, 10)
    assert removed is not None
    assert removed.deleted_at is not None
    assert all(result.id != item.id for result in wardrobe.list_items(1))

    restore = wardrobe.draft_restore(1, 10, item.id)
    restored = wardrobe.confirm_action(restore["token"], 1, 10)
    assert restored is not None
    assert restored.deleted_at is None


def test_search_by_role(wardrobe: WardrobeService) -> None:
    shoes = wardrobe.list_items(1, role=WearRole.FOOTWEAR)
    assert len(shoes) == 4
    assert {item.id for item in shoes} == {"shoe_01", "shoe_02", "shoe_03", "shoe_04"}
