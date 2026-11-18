from __future__ import annotations

import logging
from datetime import datetime

from pydantic import ValidationError
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.database import Database
from app.errors import WardrobeAIError
from app.llm.client import ModelToolCall
from app.llm.tools import DispatchResponse, ToolDispatcher
from app.services.outfits import OutfitService
from app.services.wardrobe import WardrobeService
from app.telegram.formatting import render_telegram_html

logger = logging.getLogger(__name__)


HELP_TEXT = """I can manage your wardrobe and recommend outfits.

Try:
• Add a navy crewneck sweatshirt.
• Show my shoes.
• Remove the black T-shirt.
• What should I wear today?
• Give me something more sporty.
• I wore it.
• I wore the cream crewneck, Jeans #2, and Adidas Midcity Low.
• What have I worn recently?
• Would this jacket be worth buying?

Commands: /wardrobe, /item <id>, /outfit [today|tomorrow], /history, /cancel"""


class TelegramBotService:
    def __init__(
        self,
        token: str,
        allowed_user_ids: frozenset[int],
        allowed_chat_id: int,
        database: Database,
        wardrobe: WardrobeService,
        outfits: OutfitService,
        dispatcher: ToolDispatcher,
        timezone: object,
    ) -> None:
        self.allowed_user_ids = allowed_user_ids
        self.allowed_chat_id = allowed_chat_id
        self.database = database
        self.wardrobe = wardrobe
        self.outfits = outfits
        self.dispatcher = dispatcher
        self.timezone = timezone
        self.application = Application.builder().token(token).build()
        self._register_handlers()

    @property
    def bot(self):
        return self.application.bot

    def _register_handlers(self) -> None:
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.start_command))
        self.application.add_handler(CommandHandler("wardrobe", self.wardrobe_command))
        self.application.add_handler(CommandHandler("item", self.item_command))
        self.application.add_handler(CommandHandler("outfit", self.outfit_command))
        self.application.add_handler(CommandHandler("history", self.history_command))
        self.application.add_handler(CommandHandler("cancel", self.cancel_command))
        self.application.add_handler(CallbackQueryHandler(self.callback_query))
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.text_message)
        )
        self.application.add_error_handler(self.error_handler)

    async def start(self) -> None:
        await self.application.initialize()
        if self.application.updater is None:
            raise RuntimeError("Telegram updater is unavailable")
        await self.application.updater.start_polling(drop_pending_updates=False)
        await self.application.start()

    async def stop(self) -> None:
        if self.application.updater and self.application.updater.running:
            await self.application.updater.stop()
        if self.application.running:
            await self.application.stop()
        await self.application.shutdown()

    def _authorized(self, update: Update) -> bool:
        user = update.effective_user
        chat = update.effective_chat
        return bool(
            user
            and chat
            and user.id in self.allowed_user_ids
            and chat.id == self.allowed_chat_id
            and chat.type == "private"
        )

    def _claim_update(self, update: Update) -> bool:
        now = datetime.now(self.timezone).isoformat()
        with self.database.transaction() as connection:
            inserted = connection.execute(
                "INSERT OR IGNORE INTO processed_updates(update_id, processed_at) VALUES (?, ?)",
                (update.update_id, now),
            ).rowcount
            if inserted and update.effective_user:
                connection.execute(
                    "UPDATE telegram_users SET last_seen_at=?, updated_at=? WHERE user_id=?",
                    (now, now, update.effective_user.id),
                )
        return bool(inserted)

    async def _prepare(self, update: Update) -> bool:
        if not self._authorized(update):
            logger.warning("telegram_update_rejected", extra={"update_id": update.update_id})
            return False
        return self._claim_update(update)

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await self._prepare(update) and update.effective_message:
            await update.effective_message.reply_text(HELP_TEXT)

    async def wardrobe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._prepare(update):
            return
        await self._dispatch_call(update, ModelToolCall("wardrobe_search", {}))

    async def item_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._prepare(update):
            return
        if not context.args:
            await update.effective_message.reply_text("Usage: /item <item_id>")
            return
        await self._dispatch_call(
            update, ModelToolCall("wardrobe_get", {"item_id": context.args[0]})
        )

    async def outfit_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._prepare(update):
            return
        local_today = datetime.now(self.timezone).date()
        raw = context.args[0].lower() if context.args else None
        target_date = None
        if raw == "today":
            target_date = local_today.isoformat()
        elif raw == "tomorrow":
            from datetime import timedelta

            target_date = (local_today + timedelta(days=1)).isoformat()
        elif raw:
            target_date = raw
        await self._dispatch_call(
            update,
            ModelToolCall("outfit_recommend", {"target_date": target_date}),
        )

    async def history_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._prepare(update):
            return
        await self._dispatch_call(update, ModelToolCall("outfit_history", {"limit": 5}))

    async def cancel_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._prepare(update):
            return
        user = update.effective_user
        chat = update.effective_chat
        cancelled = self.wardrobe.cancel_latest(user.id, chat.id)
        text = "Cancelled the pending change." if cancelled else "There is no pending change."
        await update.effective_message.reply_text(text)

    async def text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._prepare(update):
            return
        message = update.effective_message
        if not message or not message.text:
            return
        try:
            response = await self.dispatcher.handle(
                update.effective_user.id,
                update.effective_chat.id,
                message.text,
                datetime.now(self.timezone).date(),
            )
            await self.send_response(update.effective_chat.id, response)
        except (WardrobeAIError, ValidationError, ValueError) as exc:
            await message.reply_text(str(exc))

    async def _dispatch_call(self, update: Update, call: ModelToolCall) -> None:
        try:
            response = await self.dispatcher.execute(
                call,
                update.effective_user.id,
                update.effective_chat.id,
                datetime.now(self.timezone).date(),
            )
            await self.send_response(update.effective_chat.id, response)
        except (WardrobeAIError, ValidationError, ValueError) as exc:
            await update.effective_message.reply_text(str(exc))

    async def send_response(self, chat_id: int, response: DispatchResponse) -> int:
        keyboard = None
        if response.confirmation_token:
            if response.confirmation_kind == "remove":
                verb = "Remove"
                callback_prefix = "action:confirm"
            elif response.confirmation_kind == "manual_worn":
                verb = "Record outfit"
                callback_prefix = "manual:confirm"
            else:
                verb = "Confirm"
                callback_prefix = "action:confirm"
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            verb,
                            callback_data=f"{callback_prefix}:{response.confirmation_token}",
                        ),
                        InlineKeyboardButton(
                            "Cancel",
                            callback_data=f"action:cancel:{response.confirmation_token}",
                        ),
                    ]
                ]
            )
        elif response.recommendation_id:
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "I wore it",
                            callback_data=f"worn:{response.recommendation_id}",
                        )
                    ]
                ]
            )
        message = await self.bot.send_message(
            chat_id=chat_id,
            text=render_telegram_html(response.text),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
        if response.recommendation_id:
            self.outfits.set_telegram_message_id(response.recommendation_id, message.message_id)
        return message.message_id

    async def callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._prepare(update) or not update.callback_query:
            return
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        try:
            if data.startswith("action:"):
                _, operation, token = data.split(":", 2)
                if operation == "confirm":
                    item = self.wardrobe.confirm_action(
                        token, update.effective_user.id, update.effective_chat.id
                    )
                    verb = "Removed" if item and item.deleted_at else "Saved"
                    text = f"{verb}: {item.name}." if item else "Saved."
                else:
                    self.wardrobe.cancel_action(
                        token, update.effective_user.id, update.effective_chat.id
                    )
                    text = "Cancelled."
            elif data.startswith("manual:confirm:"):
                token = data.split(":", 2)[2]
                self.outfits.confirm_manual_worn(
                    token,
                    update.effective_user.id,
                    update.effective_chat.id,
                )
                text = "Recorded your outfit as worn."
            elif data.startswith("worn:"):
                recommendation_id = data.split(":", 1)[1]
                self.outfits.confirm_worn(
                    update.effective_user.id,
                    update.effective_chat.id,
                    datetime.now(self.timezone).date(),
                    recommendation_id=recommendation_id,
                )
                text = "Recorded as worn."
            else:
                text = "That action is not recognized."
            await query.edit_message_reply_markup(reply_markup=None)
            await query.message.reply_text(text)
        except (WardrobeAIError, ValidationError, ValueError) as exc:
            await query.message.reply_text(str(exc))

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        update_id = update.update_id if isinstance(update, Update) else None
        logger.error(
            "telegram_handler_failed",
            extra={"update_id": update_id},
            exc_info=context.error,
        )
        if isinstance(update, Update) and self._authorized(update) and update.effective_message:
            await update.effective_message.reply_text(
                "Something failed safely. Nothing was changed; please try again."
            )
