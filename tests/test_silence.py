"""The idle-silence timer: the device closes a conversation nobody is talking in, also after the
follow-up window has stopped the upload (the connection used to stay until the hub gave up).

Run like test_barge_in.py (same stubs).
"""
import asyncio

import pytest

import test_barge_in
from thinksmart_client.silence import SilenceTimer


def observe(t, now, **kw):
    return t.observe(now, **{'connected': True, 'waiting': True, **kw})


def test_expires_after_the_silence_and_speech_starts_it_over():
    t = SilenceTimer(20)
    assert not observe(t, 100) and not observe(t, 119.9)
    assert observe(t, 120)
    assert not observe(t, 130, speech=True) and not observe(t, 149.9)
    assert observe(t, 150)


def test_work_playback_and_a_dropped_connection_are_not_silence():
    t = SilenceTimer(20)
    observe(t, 100)
    assert not observe(t, 130, waiting=False)         # thinking, recognising, speaking, push-to-talk
    assert not observe(t, 140) and not observe(t, 159.9) and observe(t, 160)   # counts from the user's turn
    assert not observe(t, 200, connected=False) and t.since is None


@pytest.mark.parametrize('bad', [0, 601, -5, '20', None, True])
def test_a_bad_setting_means_the_default(bad):
    assert SilenceTimer(bad).seconds == 20 and SilenceTimer(30).seconds == 30


@pytest.mark.asyncio
async def test_the_ticker_closes_a_connection_nobody_is_talking_in():
    c = test_barge_in.make_client()
    closed = []

    async def close():
        closed.append(1)
        c.hub.connected = False
    c.hub.close = close
    c.silence.seconds = 0.5
    ticker = asyncio.create_task(c._ticker())
    await asyncio.sleep(0.3)
    assert not closed                                  # inside the silence: still open
    await asyncio.sleep(0.9)
    ticker.cancel()
    assert closed == [1] and c.state == 'idle'         # closed once, though nothing was uploading
