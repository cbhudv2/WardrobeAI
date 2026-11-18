from __future__ import annotations

import html
import re


_HEADING = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*$")
_BOLD = re.compile(r"\*\*([^*\n]+?)\*\*")
_STRIKE = re.compile(r"~~([^~\n]+?)~~")
_INLINE_CODE = re.compile(r"`([^`\n]+?)`")
_BULLET = re.compile(r"(?m)^[ \t]*[-*][ \t]+")


def render_telegram_html(text: str) -> str:
    """Convert the small Markdown subset used by the model to safe Telegram HTML."""
    rendered = html.escape(text, quote=False)
    rendered = _HEADING.sub(r"<b>\1</b>", rendered)
    rendered = _BOLD.sub(r"<b>\1</b>", rendered)
    rendered = _STRIKE.sub(r"<s>\1</s>", rendered)
    rendered = _INLINE_CODE.sub(r"<code>\1</code>", rendered)
    rendered = _BULLET.sub("• ", rendered)
    return rendered
