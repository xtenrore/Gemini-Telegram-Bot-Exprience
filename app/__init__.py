"""Aircraft Alert Telegram Bot — Core application package."""

# Plane? v3.8 applies one outbound rendering layer to every Telegram message,
# caption and inline keyboard. Import-time installation is intentional: the
# package is initialised before bot/worker modules construct Telegram Bot
# instances, so legacy and future code paths inherit the same visual language.
from app.ui_symbols import install_telegram_monochrome

install_telegram_monochrome()
