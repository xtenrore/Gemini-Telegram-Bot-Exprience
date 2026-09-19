from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationHandlerStop

import app.bot.profile_handlers as ui


@pytest.mark.asyncio
async def test_category_selector_paginates_and_keeps_navigation(monkeypatch):
    async def fake_state(user_id):
        return "profile:aircraft", {
            "profile_draft": {
                "preferences": {
                    "aircraft_filter": {
                        "mode": "selected",
                        "selected_categories": ["widebody"],
                        "selected_types": [],
                        "excluded_types": [],
                    }
                }
            }
        }

    shown = []

    async def fake_show(update, text, keyboard=None):
        shown.append((text, keyboard))

    monkeypatch.setattr(ui, "_raw_state", fake_state)
    monkeypatch.setattr(ui, "_show", fake_show)

    await ui._render_category(SimpleNamespace(), 7, "widebody", 0)
    assert shown
    first_keyboard = shown[-1][1]
    first_labels = [button.text for row in first_keyboard.inline_keyboard for button in row]
    assert "Next" in first_labels
    assert len([label for label in first_labels if label.startswith(("✓ ", "○ "))]) <= ui.PAGE_SIZE

    await ui._render_category(SimpleNamespace(), 7, "widebody", 1)
    second_keyboard = shown[-1][1]
    second_labels = [button.text for row in second_keyboard.inline_keyboard for button in row]
    assert "Previous" in second_labels


def test_profile_callback_payloads_stay_within_telegram_limit():
    samples = [
        ("Profile", "pf:o:0123456789"),
        ("Aircraft", "pf:t:classic_rare:123:A35K"),
        ("Category", "pf:c:regional_jet:123"),
        ("Rule", "pf:rf:min"),
    ]
    for text, payload in samples:
        button = ui._button(text, payload)
        assert len(button.callback_data.encode("utf-8")) <= 64


@pytest.mark.asyncio
async def test_stale_profile_button_regenerates_menu_instead_of_crashing(monkeypatch):
    called = []

    async def fake_get_profile(user_id, profile_id):
        return None

    async def fake_stale(update, user_id):
        called.append(user_id)

    class Query:
        data = "pf:o:0123456789"
        message = None

        async def answer(self, *args, **kwargs):
            return None

    update = SimpleNamespace(
        callback_query=Query(),
        effective_user=SimpleNamespace(id=44),
        message=None,
    )

    monkeypatch.setattr(ui, "get_profile", fake_get_profile)
    monkeypatch.setattr(ui, "_stale", fake_stale)

    with pytest.raises(ApplicationHandlerStop):
        await ui.profile_callback(update, SimpleNamespace())
    assert called == [44]


@pytest.mark.asyncio
async def test_malformed_profile_callback_is_treated_as_stale(monkeypatch):
    called = []

    async def fake_stale(update, user_id):
        called.append(user_id)

    class Query:
        data = "pf:c:widebody:not-a-number"
        message = None

        async def answer(self, *args, **kwargs):
            return None

    update = SimpleNamespace(
        callback_query=Query(),
        effective_user=SimpleNamespace(id=9),
        message=None,
    )
    monkeypatch.setattr(ui, "_stale", fake_stale)

    with pytest.raises(ApplicationHandlerStop):
        await ui.profile_callback(update, SimpleNamespace())
    assert called == [9]
