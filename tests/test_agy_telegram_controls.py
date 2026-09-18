from app.agy_console import TERMINAL_CONTROLS, _controls_keyboard


def test_agy_terminal_control_sequences():
    assert TERMINAL_CONTROLS["enter"] == "\r"
    assert TERMINAL_CONTROLS["up"] == "\x1b[A"
    assert TERMINAL_CONTROLS["down"] == "\x1b[B"
    assert TERMINAL_CONTROLS["left"] == "\x1b[D"
    assert TERMINAL_CONTROLS["right"] == "\x1b[C"
    assert TERMINAL_CONTROLS["space"] == " "
    assert TERMINAL_CONTROLS["esc"] == "\x1b"
    assert TERMINAL_CONTROLS["ctrlc"] == "\x03"


def test_agy_console_exposes_navigation_buttons():
    markup = _controls_keyboard()
    buttons = {
        button.callback_data: button.text
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    }
    assert buttons["agy:key:up"] == "↑"
    assert buttons["agy:key:down"] == "↓"
    assert buttons["agy:key:enter"] == "↵ Enter"
    assert buttons["agy:key:space"] == "␠ Space"
    assert buttons["agy:key:esc"] == "Esc"
