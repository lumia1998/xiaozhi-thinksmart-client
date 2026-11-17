import pytest

from thinksmart_client import config, device


@pytest.fixture(autouse=True)
def device_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'PATH', tmp_path / 'config.json')
    monkeypatch.setattr(device, 'HINT_PATH', tmp_path / 'screen.json')
