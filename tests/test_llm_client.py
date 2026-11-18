import json

from app.llm.client import MistralClient


def test_parse_reply_uses_final_text_chunks_and_tool_calls() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "private reasoning"},
                        {"type": "text", "text": "Done."},
                    ],
                    "tool_calls": [
                        {
                            "id": "abc",
                            "function": {
                                "name": "wardrobe_get",
                                "arguments": json.dumps({"item_id": "top_01"}),
                            },
                        }
                    ],
                }
            }
        ]
    }

    result = MistralClient.parse_reply(payload)

    assert result.text == "Done."
    assert result.tool_calls[0].name == "wardrobe_get"
    assert result.tool_calls[0].arguments == {"item_id": "top_01"}


def test_extract_final_text_drops_thinking_chunks() -> None:
    content = [
        {"type": "thinking", "thinking": "do not expose"},
        {"type": "output_text", "text": "Visible"},
    ]
    assert MistralClient.extract_final_text(content) == "Visible"
