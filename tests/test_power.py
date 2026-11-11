"""关机 in the settings: halt (not poweroff, which powers straight back on with the cable in).

Run like test_barge_in.py (same stubs).
"""
import asyncio

import pytest

import test_barge_in
from thinksmart_client import app as app_mod


def make_client(monkeypatch, rc=0):
    c = test_barge_in.make_client()
    c.play = lambda name: None

    async def close():
        c.hub.connected = False
    c.hub.close = close
    monkeypatch.setattr(app_mod, 'HALT_AFTER', 0)
    ran = []

    class Proc:
        returncode = rc
        async def communicate(self):
            return b'', b'sudo: a password is required' if rc else b''

    async def exec_(*cmd, **kw):
        ran.append(cmd)
        p = Proc()
        p.cmd = cmd
        return p
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', exec_)
    events = []
    c.events.publish = lambda kind, data: events.append((kind, data))
    return c, ran, events


@pytest.mark.asyncio
async def test_power_off_shows_the_screen_then_halts(monkeypatch):
    c, ran, events = make_client(monkeypatch)
    assert await c.power_off() == {'ok': True}
    assert ('power', {'state': 'off'}) in events
    await asyncio.sleep(0.05)
    assert ran == [('sudo', '-n', 'systemctl', 'halt')]


@pytest.mark.asyncio
async def test_power_off_ends_a_conversation_first(monkeypatch):
    c, ran, events = make_client(monkeypatch)
    await c.start_listening('realtime')
    assert c.hub.connected
    await c.power_off()
    assert not c.hub.connected and not c.uploading and c.state == 'idle'


@pytest.mark.asyncio
async def test_second_tap_does_not_halt_twice(monkeypatch):
    c, ran, events = make_client(monkeypatch)
    await asyncio.gather(c.power_off(), c.power_off())
    await asyncio.sleep(0.05)
    assert ran == [('sudo', '-n', 'systemctl', 'halt')]
    assert [d for k, d in events if k == 'power'] == [{'state': 'off'}]


@pytest.mark.asyncio
async def test_failed_halt_takes_the_screen_away_again(monkeypatch):
    c, ran, events = make_client(monkeypatch, rc=1)
    await c.power_off()
    await asyncio.sleep(0.05)
    assert ('power', {'state': 'failed'}) in events
    assert any(k == 'toast' and d['kind'] == 'error' for k, d in events)
    assert not c.powering_off   # it can be tried again
