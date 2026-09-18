from pathlib import Path


def test_agy_dockerfile_installs_script_and_wraps_interactive_sessions():
    text = Path("Dockerfile.agy").read_text(encoding="utf-8")
    assert "util-linux" in text
    assert "script -qefc" in text
    assert 'if [[ "$arg" == "-p" || "$arg" == "--prompt" ]]' in text
    assert 'exec "$REAL" --model "$MODEL" "$@"' in text


def test_agy_wrapper_keeps_paid_api_key_fallback_disabled():
    text = Path("Dockerfile.agy").read_text(encoding="utf-8")
    assert "unset GEMINI_API_KEY GOOGLE_API_KEY GOOGLE_GEMINI_API_KEY GOOGLE_GEMINI_BASE_URL" in text
    assert "gemini-3.1-pro-high" in text
