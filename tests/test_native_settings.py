from pathlib import Path


def test_native_form_is_the_only_magi_settings_page():
    plugin = (Path(__file__).resolve().parents[1] / "desktop" / "plugin.js").read_text()
    assert "registerSettingsPage" not in plugin, "native settings must be the only Magi settings page"
