import pytest

from app.photography.gemini import (
    GeminiPhotographyError,
    _extract_output_text,
    _gemini_json_schema,
    _parse_json_text,
)
from app.photography.models import CameraProfile


def test_extract_generate_content_output_text():
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": '{"brand":"Canon","model":"R7"}'},
                    ]
                }
            }
        ]
    }
    assert _extract_output_text(payload) == '{"brand":"Canon","model":"R7"}'


def test_extract_interactions_output_text_for_backwards_compatibility():
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


def test_gemini_json_schema_strips_pydantic_defaults_but_keeps_constraints():
    schema = _gemini_json_schema(CameraProfile)
    serialized = str(schema)
    assert "'default'" not in serialized
    assert "'minimum': 0.0" in serialized
    assert "'maximum': 1.0" in serialized
