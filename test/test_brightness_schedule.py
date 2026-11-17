import datetime
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from test_barge_in import make_client
from thinksmart_client import brightness_schedule, config, device, mcp
from thinksmart_client.web import make_app


def scheduled_client():
    client = make_client()
    client.screen = device.Screen()
    client.normal_brightness = 80
    client.brightness_schedule = brightness_schedule.normalize(changes={'enabled': True})
    return client


@pytest.mark.parametrize('hour,minute,active', [(23, 59, False), (0, 0, True), (5, 59, True), (6, 0, False)])
def test_boundaries(hour, minute, active):
    setting = brightness_schedule.normalize(changes={'enabled': True})
    assert brightness_schedule.active(setting, datetime.datetime(2026, 11, 17, hour, minute)) == active


def test_overnight():
    setting = brightness_schedule.normalize(changes={'enabled': True, 'start': '22:00'})
    for hour, expected in [(21, False), (22, True), (0, True), (5, True), (6, False)]:
        assert brightness_schedule.active(setting, datetime.datetime(2026, 11, 17, hour)) == expected


@pytest.mark.parametrize('changes', [{'level': True}, {'level': -1}, {'level': 101}, {'level': 30.5},
                                    {'start': '24:00'}, {'end': '06:99'}, {'enabled': 'false'},
                                    {'start': '06:00'}, {'other': 1}, {'level': None}])
def test_validation(changes):
    with pytest.raises(ValueError):
        brightness_schedule.normalize(changes=changes)


def test_hint_restores_normal_and_does_not_wake_screen():
    client = scheduled_client()
    client.screen.release()
    initial = json.loads(device.HINT_PATH.read_text())
    client._apply_brightness_schedule(datetime.datetime(2026, 11, 17, 0))
    hint = json.loads(device.HINT_PATH.read_text())
    assert hint['brightness'] == 30 and not hint['dim']
    assert hint['seq'] == initial['seq'] and hint['hold'] == initial['hold']
    assert client.brightness() == {'level': 30, 'normal': 80, 'dimmed': True}
    client._apply_brightness_schedule(datetime.datetime(2026, 11, 17, 6))
    assert json.loads(device.HINT_PATH.read_text())['brightness'] == 80
    client._apply_brightness_schedule(datetime.datetime(2026, 11, 18, 0))
    assert client.screen.level == 30


@pytest.mark.asyncio
async def test_save_manual_change_and_disable(monkeypatch):
    client = scheduled_client()
    monkeypatch.setattr(brightness_schedule, 'active', lambda setting, now=None: setting['enabled'])
    await client.set_brightness_schedule(enabled=True, start='00:00', end='06:00', level=30)
    assert client.screen.level == 30
    await client.set_brightness(75)
    assert client.screen.level == 30 and client.normal_brightness == 75
    await client.set_brightness_schedule(enabled=False)
    assert client.screen.level == 75
    saved = json.loads(config.PATH.read_text())
    assert saved['brightness'] == 75
    assert saved['brightness_schedule'] == {**brightness_schedule.DEFAULTS, 'enabled': False}
    restored = scheduled_client()
    restored.normal_brightness = saved['brightness']
    restored.brightness_schedule = brightness_schedule.normalize(saved['brightness_schedule'])
    restored._apply_brightness_schedule()
    assert restored.screen.level == 75


@pytest.mark.asyncio
async def test_failed_save_leaves_setting_unchanged(monkeypatch):
    client = scheduled_client()
    before = dict(client.brightness_schedule)

    def failed(**values):
        raise OSError('disk full')

    monkeypatch.setattr(config, 'save', failed)
    with pytest.raises(OSError):
        await client.set_brightness_schedule(level=20)
    assert client.brightness_schedule == before


@pytest.mark.asyncio
async def test_api_and_removed_alarm_routes():
    client = scheduled_client()
    async with TestClient(TestServer(make_app(client))) as http:
        response = await http.post('/api/brightness-schedule', json={'enabled': True, 'level': 30})
        assert response.status == 200 and (await response.json())['ok']
        for body in ({'level': 101}, [], {'start': '24:00'}, {'start': '06:00'}):
            response = await http.post('/api/brightness-schedule', json=body)
            assert response.status == 400
        for path in ('/api/alarm-volume', '/api/alarm-tone', '/api/alarms/stop', '/api/alarms/delete'):
            assert (await http.post(path, json={})).status == 404


@pytest.mark.asyncio
async def test_device_tools():
    client = scheduled_client()
    assert all(not tool['name'].startswith('self.alarm.') for tool in mcp.TOOLS)
    response = await mcp.handle({'id': 1, 'method': 'tools/call', 'params': {
        'name': 'self.screen.set_brightness_schedule', 'arguments': {'enabled': True, 'level': 25}}}, client)
    assert not response['result']['isError']
    assert client.brightness_schedule['level'] == 25
    response = await mcp.handle({'id': 2, 'method': 'tools/call', 'params': {'name': 'self.alarm.create'}}, client)
    assert response['error']['code'] == -32601
    assert not hasattr(client, '_alarm_loop')
