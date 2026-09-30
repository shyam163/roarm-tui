import json
import stat

from roarm.config import load_config, save_config


def test_missing_or_corrupt_config_is_empty(tmp_path):
    assert load_config(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    assert load_config(bad) == {}
    bad.write_text("[1, 2]")
    assert load_config(bad) == {}


def test_round_trip_and_permissions(tmp_path):
    path = tmp_path / "sub" / "config.json"
    save_config({"wifi_host": "192.168.1.59"}, path)
    assert load_config(path) == {"wifi_host": "192.168.1.59"}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_save_config_drops_secret_keys(tmp_path):
    path = tmp_path / "config.json"
    save_config({"wifi_host": "h", "sta_password": "pw", "Password": "x", "ap_pawword": "y"}, path)
    assert json.loads(path.read_text()) == {"wifi_host": "h"}
