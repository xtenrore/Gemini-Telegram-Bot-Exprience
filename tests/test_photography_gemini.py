import pytest

from app.photography.gemini import GeminiPhotographyError, _extract_output_text, _parse_json_text


def test_extract_interactions_output_text():
    payload = {
        "steps": [
            {"type": "thought", "signature": "x"},
            {
                "type": "model_output",
                "content": [{"type": "text", "text": '{"ok":true}'}],
            },
        ]
    }
    assert _extract_output_text(payload) == '{"ok":true}'


def test_parse_json_text_handles_fenced_json():
    assert _parse_json_text('```json\n{"camera":"R7"}\n```') == {"camera": "R7"}


def test_parse_json_rejects_non_object():
    with pytest.raises(GeminiPhotographyError):
        _parse_json_text('[1,2,3]')
