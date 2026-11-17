import time

import pytest

from test_barge_in import make_client
from thinksmart_client import media


ITEMS = [{'kind': 'image', 'url': 'https://example.com/a.png', 'title': '图片'},
         {'kind': 'video', 'url': 'https://example.com/a.mp4', 'title': '视频'},
         {'kind': 'audio', 'url': 'https://example.com/a.mp3', 'title': '音频'}]


@pytest.mark.asyncio
async def test_media_received_once_and_snapshot_ready():
    client = make_client()
    client.hub.session_id = 'session'
    message = {'type': 'media', 'version': 1, 'session_id': 'session', 'id': 'call', 'items': ITEMS}
    await client._on_json(message)
    await client._on_json(message)
    rows = client.threads[client.view_entry()]
    assert len(rows) == 1 and rows[0]['media'] == ITEMS
    await client._on_json({**message, 'session_id': 'old', 'id': 'other'})
    assert len(rows) == 1
    client.abort_pending = time.monotonic()
    await client._on_json({**message, 'id': 'other'})
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_playback_suspends_upload_and_wake_pauses():
    client = make_client()
    client.uploading = True
    client.state = 'listening'
    client.listen_after_reply = True
    assert (await client.media_playback(True))['ok']
    assert client.media_active() and not client.uploading and not client.listen_after_reply
    assert client.state == 'idle'
    assert not client._silent_expired(time.monotonic() + 100)
    client.set_state('listening')
    assert not client.media_active()
    assert not (await client.media_playback(True, heartbeat=True))['ok']
    assert client.state == 'listening'
    await client.media_playback(False)
    assert not client.media_active()


@pytest.mark.asyncio
async def test_history_load_preserves_media():
    client = make_client()
    client.cfg['device_id'] = 'device'
    client.hub.connected = False

    async def history(*args):
        return {'ok': True, 'models': {'model': [{'id': 1, 'role': 'bot', 'text': '', 'time': 1, 'media': ITEMS}]}}

    client._hub_http = history
    await client.load_history()
    assert client.threads['model'][0]['media'] == ITEMS


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'file:///etc/passwd', 'https://[',
                                'https://user:password@example.com/a', '//example.com/a'])
def test_invalid_media(url):
    assert media.normalize([{'kind': 'image', 'url': url}]) == []


@pytest.mark.asyncio
async def test_playback_http_endpoint_and_abort():
    from aiohttp.test_utils import TestClient, TestServer
    from thinksmart_client.web import make_app

    client = make_client()
    async with TestClient(TestServer(make_app(client))) as http:
        response = await http.post('/api/media/playback', json={'playing': True})
        assert response.status == 200 and (await response.json())['ok']
        assert client.media_active()
        response = await http.post('/api/media/playback', json={'playing': 'yes'})
        assert response.status == 400
        await client.abort()
        assert not client.media_active()
        response = await http.post('/api/media/playback', json={'playing': False})
        assert (await response.json())['ok']


@pytest.mark.asyncio
async def test_shutdown_blocks_media_and_heartbeat_refreshes():
    client = make_client()
    client.powering_off = True
    assert not (await client.media_playback(True))['ok']
    assert not client.media_active()
    client.powering_off = False
    await client.media_playback(True)
    sent = len(client.hub.sent)
    await client.media_playback(True)
    assert len(client.hub.sent) == sent
    client.media_until = time.monotonic() - 1
    assert not client.media_active()
