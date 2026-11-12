"""The window after a reply: a late question is waited for; an empty window ends the conversation.

A question that ends late in the 5 s follow-up window is often still being recognised at its
deadline (the hub's VAD end, then 1-3 s of ASR). It used to go idle there, the text came a moment
later, the hub answered it, and after that answer nothing listened (device 11-04 02:30: stopped
35.14, text 35.78, idle after the reply).

Now the follow-up window waits for a pending recognition like the window after a wake does, and a
question whose text comes after the mic was closed is listened after like any other. A window that
recognised nothing ends the conversation: mic shut and channel closed, so the next thing said has
to start with the wake word. "Didn't catch that" is still only said after a wake, whose channel
stays open.

Run like test_barge_in.py (same stubs).
"""
import time

import pytest

from test_barge_in import ask, make_client, tick, toasts
from thinksmart_client import app

MISS = '没听清，可以再说一次「小麦小麦」'


async def follow_up(c, features=None):
    """A question answered, its reply played: the follow-up window is open."""
    c.hub.features = {'vad_end': True} if features is None else features
    await ask(c, '帮我上网查一下苹果股价多少')
    await c._on_json({'type': 'tts', 'state': 'stop'})
    await c._listen_after_reply()
    assert c.state == 'listening' and c.uploading and c.deadline


async def talked_and_deadline_passed(c, vad_end=True):
    c.speech_end = time.monotonic()                     # the next question, ending late in the window
    if vad_end:
        await c._on_json({'type': 'vad', 'state': 'end'})   # the hub decided they finished: recognising
    c.deadline = time.monotonic() - 0.01                # 5 s after it, before the text is back
    await tick(c)


@pytest.mark.asyncio
async def test_late_question_in_the_follow_up_window_is_answered_and_listened_after():
    c = make_client()
    shown = toasts(c)
    await follow_up(c)
    await talked_and_deadline_passed(c)
    # was: idle here (no "didn't catch that", but the answer that followed was never listened after)
    assert c.state == 'thinking' and c.awaiting_text and not c.uploading
    assert c.deadline > time.monotonic() + app.RECOGNITION_WAIT - 1
    await c._on_json({'type': 'stt', 'text': '再查一下今天 AI 圈有什么新闻'})
    assert c.state == 'thinking' and not c.awaiting_text and c.listen_after_reply
    await c._on_json({'type': 'tts', 'state': 'start'})
    await c._on_json({'type': 'tts', 'state': 'sentence_start', 'text': '今天 AI 圈'})
    assert not c.uploading                              # nothing said during the reply counts
    await c._on_json({'type': 'tts', 'state': 'stop'})
    await c._listen_after_reply()
    assert c.state == 'listening' and c.uploading and c.deadline   # listening again after it
    assert shown == []


@pytest.mark.asyncio
async def test_a_question_recognised_in_the_window_keeps_the_conversation():
    c = make_client()
    shown = toasts(c)
    await follow_up(c)
    await c._on_json({'type': 'stt', 'text': '今天天气怎么样'})   # recognised: the window did its job
    await c._on_json({'type': 'tts', 'state': 'start'})
    assert c.state == 'thinking' and not c.uploading and not c.hub.closed and shown == []


@pytest.mark.asyncio
async def test_follow_up_window_that_hears_nothing_ends_the_conversation():
    c = make_client()
    shown = toasts(c)
    await follow_up(c)
    c.deadline = time.monotonic() - 0.01
    await tick(c)
    assert c.state == 'idle' and not c.uploading and not c.awaiting_text and c.deadline is None
    assert c.hub.closed and not c.hub.connected        # the conversation is over, not just idle
    assert shown == []                                 # no "didn't catch that" in a follow-up window


@pytest.mark.asyncio
async def test_follow_up_noise_the_hub_did_not_take_for_speech_ends_the_conversation():
    c = make_client()
    shown = toasts(c)
    await follow_up(c)
    await talked_and_deadline_passed(c, vad_end=False)   # mic level only: no VAD end from the hub
    assert c.state == 'idle' and not c.awaiting_text and c.hub.closed and shown == []


@pytest.mark.asyncio
async def test_follow_up_noise_the_hub_already_dropped_ends_the_conversation():
    c = make_client()
    shown = toasts(c)
    await follow_up(c)
    c.speech_end = time.monotonic()
    await c._on_json({'type': 'vad', 'state': 'end'})
    await c._on_json({'type': 'vad', 'state': 'empty'})   # a cough: recognised nothing, mic still open
    assert c.state == 'listening' and c.uploading and not c.hub.closed
    c.deadline = time.monotonic() - 0.01
    await tick(c)
    assert c.state == 'idle' and c.hub.closed and shown == []


@pytest.mark.asyncio
async def test_follow_up_with_an_older_hub_does_not_wait_on_noise():
    # an older hub sends no VAD events: after a wake any talking waits (unchanged); after a reply
    # that would make every noise "think" for 15 s, so the window ends instead
    c = make_client()
    shown = toasts(c)
    await follow_up(c, features={})
    await talked_and_deadline_passed(c, vad_end=False)
    assert c.state == 'idle' and not c.awaiting_text and c.hub.closed and shown == []


@pytest.mark.asyncio
async def test_follow_up_wait_that_recognises_nothing_ends_the_conversation():
    c = make_client()
    shown = toasts(c)
    await follow_up(c)
    await talked_and_deadline_passed(c)
    assert c.awaiting_text
    await c._on_json({'type': 'vad', 'state': 'empty'})
    assert c.state == 'idle' and not c.awaiting_text and not c.listen_after_reply
    assert c.hub.closed and shown == []


@pytest.mark.asyncio
async def test_follow_up_wait_that_times_out_ends_the_conversation():
    c = make_client()
    shown = toasts(c)
    await follow_up(c)
    await talked_and_deadline_passed(c)
    assert c.awaiting_text
    c.deadline = time.monotonic() - 0.01               # RECOGNITION_WAIT over, no text, no "empty"
    await tick(c)
    assert c.state == 'idle' and not c.awaiting_text and c.hub.closed and shown == []
    assert ('abort', None) in c.hub.kinds()            # the hub's pending recognition was stopped


@pytest.mark.asyncio
async def test_wake_word_during_the_follow_up_wait_interrupts():
    c = make_client()
    await follow_up(c)
    await talked_and_deadline_passed(c)
    c.chime = lambda source: None
    await c.wake('voice')
    assert c.hub.kinds()[-2:] == [('abort', None), ('listen', 'start')]
    assert c.state == 'listening' and not c.awaiting_text and not c.heard


@pytest.mark.asyncio
async def test_after_a_wake_hearing_nothing_keeps_the_channel_for_a_retry():
    # after a wake the window keeps its own ending: "didn't catch that", channel still open
    c = make_client()
    shown = toasts(c)
    c.hub.features = {'vad_end': True}
    await c.start_listening('realtime')
    c.deadline = time.monotonic() - 0.01
    await tick(c)
    assert c.state == 'idle' and not c.uploading and shown == [MISS] and not c.hub.closed


@pytest.mark.asyncio
async def test_after_a_wake_unchanged_but_a_late_text_is_listened_after_too():
    # after a wake: waits, and "didn't catch that" when nothing comes, as before
    c = make_client()
    shown = toasts(c)
    c.hub.features = {'vad_end': True}
    await c.start_listening('realtime')
    await talked_and_deadline_passed(c)
    assert c.state == 'thinking' and c.awaiting_text
    await c._on_json({'type': 'vad', 'state': 'empty'})
    assert c.state == 'idle' and shown == [MISS] and not c.hub.closed
    # the same wait with the text coming back: its reply is listened after (was: idle after it)
    c = make_client()
    c.hub.features = {'vad_end': True}
    await c.start_listening('realtime')
    await talked_and_deadline_passed(c)
    await c._on_json({'type': 'stt', 'text': '今天天气怎么样'})
    assert c.listen_after_reply


@pytest.mark.asyncio
async def test_push_to_talk_is_still_not_listened_after():
    c = make_client()
    await c.ptt_start()
    await c.ptt_stop()
    assert c.awaiting_text
    await c._on_json({'type': 'stt', 'text': '几点了'})
    assert not c.listen_after_reply
