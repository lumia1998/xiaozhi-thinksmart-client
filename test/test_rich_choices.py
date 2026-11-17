import asyncio
import time

import pytest

from test_barge_in import make_client


@pytest.mark.asyncio
async def test_full_text_then_spoken_caption_without_duplicate():
    client = make_client()
    client.hub.session_id = 'session'
    base = {'type': 'assistant_text', 'version': 1, 'session_id': 'session', 'id': 'turn:0', 'sentence_id': 'turn'}
    await client._on_json({**base, 'text': '示例'})
    await client._on_json({**base, 'text': '示例\n```py\nprint(42)\n```', 'final': True})
    await client._on_json({'type': 'tts', 'state': 'sentence_start', 'text': '代码已显示在屏幕上。', 'display_text': False})
    rows = client.threads[client.view_entry()]
    assert len(rows) == 1 and 'print(42)' in rows[0]['text']


@pytest.mark.asyncio
async def test_choice_receipt_and_selection():
    client = make_client()
    client.hub.session_id = 'session'
    sid = 'b' * 32
    card = {'id': sid, 'question': '选哪个？', 'options': [{'id': '1', 'label': 'A'}, {'id': '2', 'label': 'B'}],
            'expires': time.time() + 60}
    message = {'type': 'choice_card', 'version': 1, 'session_id': 'session', 'card': card}
    await client._on_json(message)
    await client._on_json(message)
    assert len(client.threads[client.view_entry()]) == 1
    async def connected(): return True
    client.ensure_connected = connected
    async def send(payload):
        assert payload['choice'] == {'id': sid, 'option_id': '2'}
        await client._on_json({'type': 'choice_result', 'id': sid, 'session_id': 'session', 'ok': True, 'selected': '2'})
        return True
    client.hub.send = send
    assert (await client.select_choice(sid, '2'))['ok']
    assert not (await client.select_choice(sid, '1'))['ok']
