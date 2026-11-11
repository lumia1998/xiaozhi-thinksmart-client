"""The hub's HTTP answers are checked before they are used: an error or a wrong shape is not a model list.

Run like test_barge_in.py (same stubs).
"""
from contextlib import asynccontextmanager

from aiohttp import web
import pytest

from test_barge_in import make_client

OPTIONS = [{'kind': 'api', 'vendor': 'deepseek', 'label': 'DeepSeek', 'entry': 'DeepSeekLLM', 'model': 'deepseek-chat'}]


@asynccontextmanager
async def hub(body, status=200):
    """A real HTTP server on 127.0.0.1 (port 0); yields its base URL."""
    async def llm(_request):
        return web.json_response(body, status=status)

    app = web.Application()
    app.router.add_get('/kiosk/llm', llm)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '127.0.0.1', 0).start()
    try:
        yield f'http://127.0.0.1:{runner.addresses[0][1]}'
    finally:
        await runner.cleanup()


def client(url):
    c = make_client()
    del c.refresh_models   # the real one, asking the server above
    c.cfg.update(device_id='dev-1', hub_http=url)
    c.hub_seen = 0.0
    return c


@pytest.mark.asyncio
async def test_an_http_error_is_not_a_model_list():
    # an error page that happens to carry {"options": []} must not be applied as "no models"
    async with hub({'options': []}, status=500) as url:
        c = client(url)
        assert await c.refresh_models() is False
        assert c.models == {'current': None, 'options': []} and c.hub_seen == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [[], {'options': 'nope'}])
async def test_a_body_of_the_wrong_shape_is_not_a_model_list(body):
    async with hub(body) as url:
        c = client(url)
        assert await c.refresh_models() is False
        assert c.models == {'current': None, 'options': []} and c.hub_seen == 0


@pytest.mark.asyncio
async def test_a_good_answer_is_applied_and_feeds_the_health_clock():
    async with hub({'ok': True, 'selected': 'DeepSeekLLM', 'options': OPTIONS}) as url:
        c = client(url)
        assert await c.refresh_models() is True
        assert c.models['current']['entry'] == 'DeepSeekLLM' and c.hub_seen > 0
