from telegram.ext import CallbackQueryHandler, CommandHandler

from app.bot.profile_modes_v44 import register_profile_mode_handlers_v44


class _DummyApplication:
    def __init__(self):
        self.added = []

    def add_handler(self, handler, group=0):
        self.added.append((group, handler))


def test_v44_profiles_and_preferences_commands_register_before_v43():
    app = _DummyApplication()
    register_profile_mode_handlers_v44(app)

    command_handlers = [
        (group, handler)
        for group, handler in app.added
        if isinstance(handler, CommandHandler)
    ]
    assert all(group == -31 for group, _ in command_handlers)
    commands = {command for _, handler in command_handlers for command in handler.commands}
    assert {"profiles", "preferences"}.issubset(commands)


def test_v44_profile_callbacks_cover_new_ui_and_old_message_upgrade():
    app = _DummyApplication()
    register_profile_mode_handlers_v44(app)

    callback_handlers = [
        handler
        for _, handler in app.added
        if isinstance(handler, CallbackQueryHandler)
    ]
    patterns = {handler.pattern.pattern for handler in callback_handlers if handler.pattern is not None}
    assert r"^pf44:" in patterns
    assert r"^pf:(?:new|e:)" in patterns
