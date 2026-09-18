from app.agy_console import TERMINAL_CONTROLS, _controls_keyboard


def test_agy_enter_control_is_bare_carriage_return():
    assert TERMINAL_CONTROLS["enter"] == "\r"


def test_agy_console_exposes_enter_button():
    markup = _controls_keyboard()
    button = markup.inline_keyboard[0][0]
    assert button.text == "↵ Enter"
    assert button.callback_data == "agy:key:enter"
