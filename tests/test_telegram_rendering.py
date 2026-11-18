from app.telegram.formatting import render_telegram_html


def test_common_markdown_is_rendered_as_safe_telegram_html() -> None:
    text = """## Outfit

- **Top:** Cream zip-up
- **Note:** Wear <open> & relaxed
"""

    assert render_telegram_html(text) == (
        "<b>Outfit</b>\n\n"
        "• <b>Top:</b> Cream zip-up\n"
        "• <b>Note:</b> Wear &lt;open&gt; &amp; relaxed\n"
    )


def test_plain_underscores_are_not_treated_as_formatting() -> None:
    assert render_telegram_html("Item top_09 is internal.") == "Item top_09 is internal."
