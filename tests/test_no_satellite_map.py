import importlib.util
import inspect

from app.worker import notifications


def test_satellite_map_module_and_media_rendering_are_removed():
    assert importlib.util.find_spec("app.telegram_map") is None
    source = inspect.getsource(notifications)
    assert "render_telegram_map" not in source
    assert "send_photo(" not in source
    assert "edit_message_media(" not in source
    assert "Satellite map" not in source
