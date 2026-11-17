import asyncio
import json
import struct
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from test_barge_in import make_client
from thinksmart_client import camera, device, mcp


@pytest.fixture(autouse=True)
def default_v4l_backend(monkeypatch):
    monkeypatch.setattr(camera, 'libcamera_available', lambda: False)


def test_capture_device_probe_excludes_codecs(monkeypatch):
    monkeypatch.setattr(camera.Path, 'glob', lambda *_: ['/dev/video0', '/dev/video1', '/dev/video2'])
    monkeypatch.setattr(camera.os, 'open', lambda path, flags: int(path[-1]))
    closed = []
    monkeypatch.setattr(camera.os, 'close', closed.append)

    def query(descriptor, operation, buffer):
        assert operation == 0x80685600
        flags = [0x80004000, 0x80008000, 0x80000001][descriptor]
        struct.pack_into('II', buffer, 84, flags, flags & ~0x80000000)

    monkeypatch.setattr(camera.fcntl, 'ioctl', query)
    assert camera.capture_devices() == ['/dev/video2']
    assert closed == [0, 1, 2]


@pytest.mark.asyncio
async def test_initialize_stores_vision_token():
    client = make_client()
    result = await mcp.handle({'id': 1, 'method': 'initialize', 'params': {
        'capabilities': {'vision': {'token': 'test-vision-token', 'url': 'http://unused/vision'}}}}, client)
    assert result['result']['capabilities'] == {'tools': {}}
    assert client.camera.token == 'test-vision-token'
    assert any(tool['name'] == 'self.camera.take_photo' for tool in mcp.TOOLS)


@pytest.mark.asyncio
async def test_photo_upload_and_mcp_result(monkeypatch):
    received = {}

    async def vision(request):
        received['auth'] = request.headers['Authorization']
        received['device'] = request.headers['Device-Id']
        reader = await request.multipart()
        question = await reader.next()
        received['question'] = await question.text()
        image = await reader.next()
        received['image'] = await image.read()
        return web.json_response({'success': True, 'response': '桌上有一本书'})

    app = web.Application()
    app.router.add_post('/mcp/vision/explain', vision)
    monkeypatch.setattr(camera, 'capture_devices', lambda: ['/dev/video2'])
    monkeypatch.setattr(camera, 'capture', AsyncMock(return_value=b'\xff\xd8test\xff\xd9'))
    async with TestServer(app) as server:
        client = make_client()
        client.cfg.update(hub_http=str(server.make_url('/')).rstrip('/'), device_id='device', client_id='client')
        client.camera.configure({'vision': {'token': 'test-token'}})
        result = await mcp.handle({'id': 2, 'method': 'tools/call', 'params': {
            'name': 'self.camera.take_photo', 'arguments': {'question': '我面前是什么？'}}}, client)
    assert not result['result']['isError']
    assert json.loads(result['result']['content'][0]['text'])['description'] == '桌上有一本书'
    assert received == {'auth': 'Bearer test-token', 'device': 'device', 'question': '我面前是什么？',
                        'image': b'\xff\xd8test\xff\xd9'}
    assert client.camera.task is None


@pytest.mark.asyncio
async def test_missing_camera_returns_error_without_capture(monkeypatch):
    client = make_client()
    client.cfg.update(hub_http='http://127.0.0.1:1')
    client.camera.configure({'vision': {'token': 'test-token'}})
    monkeypatch.setattr(camera, 'capture_devices', lambda: [])
    capture = AsyncMock()
    monkeypatch.setattr(camera, 'capture', capture)
    result = await mcp.handle({'id': 2, 'method': 'tools/call', 'params': {
        'name': 'self.camera.take_photo', 'arguments': {'question': '看看前面'}}}, client)
    assert result['result']['isError']
    assert '尚未识别' in result['result']['content'][0]['text']
    capture.assert_not_called()


@pytest.mark.asyncio
async def test_libcamera_capture_uses_temporary_memory_and_cleans_up(monkeypatch, tmp_path):
    monkeypatch.setattr(camera, 'LIBCAMERA_ROOT', tmp_path)
    output = {}

    async def start(*arguments, **options):
        path = camera.Path(next(value.removeprefix('--file=') for value in arguments if value.startswith('--file=')))
        output['path'] = path
        assert str(path).startswith('/dev/shm/thinksmart-camera-')
        assert '--capture=60' in arguments
        path.write_bytes(b'P6\n1 1\n255\n\x01\x02\x03')
        return SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(None, b'')))

    async def convert(path, from_file=False):
        assert from_file and camera.Path(path).exists()
        return b'\xff\xd8test\xff\xd9'

    monkeypatch.setattr(camera.asyncio, 'create_subprocess_exec', start)
    monkeypatch.setattr(camera, 'capture', convert)
    assert await camera.capture_libcamera() == b'\xff\xd8test\xff\xd9'
    assert not output['path'].parent.exists()


@pytest.mark.asyncio
async def test_capture_cancellation_releases_process(monkeypatch):
    started = asyncio.Event()

    async def communicate():
        started.set()
        await asyncio.Event().wait()

    process = SimpleNamespace(returncode=None, communicate=communicate, kill=Mock(), wait=AsyncMock())
    monkeypatch.setattr(camera.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    task = asyncio.create_task(camera.capture('/dev/video2'))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    process.kill.assert_called_once()
    process.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_camera_call_does_not_block_abort():
    client = make_client()
    client.hub.session_id = 'session'
    started = asyncio.Event()
    released = asyncio.Event()

    async def explain(question):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    client.camera.explain = explain
    await client._on_json({'type': 'mcp', 'payload': {'id': 3, 'method': 'tools/call', 'params': {
        'name': 'self.camera.take_photo', 'arguments': {'question': '看看前面'}}}})
    await asyncio.wait_for(started.wait(), 1)
    await client.abort()
    assert released.is_set()
    assert all(item.get('type') != 'mcp' for item in client.hub.sent)


@pytest.mark.asyncio
async def test_volume_voice_absolute_relative_and_failure(monkeypatch):
    client = make_client()
    volume = {'value': 40}

    async def set_volume(value):
        volume['value'] = value

    monkeypatch.setattr(mcp, 'get_volume', AsyncMock(side_effect=lambda: volume['value']))
    monkeypatch.setattr(mcp, 'set_volume', set_volume)
    for arguments, expected in [({'volume': 50}, 50), ({'change': 10}, 60), ({'change': -20}, 40)]:
        result = await mcp.handle({'id': 4, 'method': 'tools/call', 'params': {
            'name': 'self.audio_speaker.set_volume', 'arguments': arguments}}, client)
        assert not result['result']['isError']
        assert volume['value'] == expected
    monkeypatch.setattr(mcp, 'set_volume', AsyncMock(side_effect=ValueError('音频服务不可用')))
    result = await mcp.handle({'id': 5, 'method': 'tools/call', 'params': {
        'name': 'self.audio_speaker.set_volume', 'arguments': {'volume': 70}}}, client)
    assert result['result']['isError'] and client.volume == 40


@pytest.mark.asyncio
async def test_brightness_voice_tools():
    client = make_client()
    client.screen = device.Screen()
    for arguments, expected in [({'brightness': 50}, 50), ({'change': 20}, 70), ({'change': -20}, 50)]:
        result = await mcp.handle({'id': 6, 'method': 'tools/call', 'params': {
            'name': 'self.screen.set_brightness', 'arguments': arguments}}, client)
        assert not result['result']['isError']
        assert client.screen.level == expected
    result = await mcp.handle({'id': 6, 'method': 'tools/call', 'params': {
        'name': 'self.screen.set_brightness', 'arguments': {'brightness': 50, 'change': 20}}}, client)
    assert result['result']['isError']


@pytest.mark.asyncio
async def test_disconnect_cancels_pending_camera_request():
    client = make_client()
    client.hub.session_id = 'session'
    client.camera.configure({'vision': {'token': 'vision-test'}})
    client.load_history = AsyncMock()
    client.play = Mock()
    pending = asyncio.create_task(asyncio.sleep(30))
    client.camera_requests.add(pending)
    await client._on_closed()
    assert pending.cancelled()
    assert client.camera.token == ''
