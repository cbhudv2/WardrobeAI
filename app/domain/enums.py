from enum import StrEnum


class ItemCategory(StrEnum):
    T_SHIRT = "t_shirt"
    POLO = "polo"
    CREWNECK = "crewneck"
    HOODIE = "hoodie"
    QUARTER_ZIP = "quarter_zip"
    JEANS = "jeans"
    SHOES = "shoes"
    LIGHT_JACKET = "light_jacket"
    PUFFER = "puffer"
    OTHER = "other"


class WearRole(StrEnum):
    MAIN_TOP = "main_top"
    BOTTOM = "bottom"
    FOOTWEAR = "footwear"
    OUTERWEAR = "outerwear"
    LIGHT_LAYER = "light_layer"


class PendingActionKind(StrEnum):
    ADD_ITEM = "add_item"
    UPDATE_ITEM = "update_item"
    REMOVE_ITEM = "remove_item"
    RESTORE_ITEM = "restore_item"
    LOG_MANUAL_OUTFIT = "log_manual_outfit"


class PendingStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class RecommendationStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class ScheduledSendStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"
