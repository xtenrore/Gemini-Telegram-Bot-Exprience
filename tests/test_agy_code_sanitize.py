from app.agy_console import _normalize_authorization_code


def test_oauth_code_whitespace_is_removed():
    assert _normalize_authorization_code("4/0AT abc\nDEF ghi") == "4/0ATabcDEFghi"


def test_exact_double_paste_is_collapsed_once():
    code = "4/0ATsM-example_code_123"
    assert _normalize_authorization_code(code + code) == code


def test_double_paste_with_visual_spaces_is_collapsed_once():
    code = "4/0ATsM-example_code_123"
    pasted = "4/0ATsM-example_ code_123  4/0ATsM-example_code_123"
    assert _normalize_authorization_code(pasted) == code
