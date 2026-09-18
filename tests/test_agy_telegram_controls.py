from app.agy_console import TERMINAL_CONTROLS, _controls_keyboard


def test_agy_terminal_control_names_map_to_worker_keys():
    assert TERMINAL_CONTROLS["enter"] == "enter"
    assert TERMINAL_CONTROLS["up"] == "up"
    assert TERMINAL_CONTROLS["down"] == "down"
    assert TERMINAL_CONTROLS["left"] == "left"
    assert TERMINAL_CONTROLS["right"] == "right"
    assert TERMINAL_CONTROLS["space"] == "space"
    assert TERMINAL_CONTROLS["tab"] == "tab"
    assert TERMINAL_CONTROLS["esc"] == "escape"
    assert TERMINAL_CONTROLS["ctrlc"] == "ctrl_c"
    assert TERMINAL_CONTROLS["ctrls"] == "ctrl_s"


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
    assert buttons["agy:key:ctrls"] == "Ctrl+S"
