"""History is available to the dashboard before a model has been opened."""
import pytest
from aiohttp.test_utils import TestClient, TestServer

from test_barge_in import make_client
from thinksmart_client.web import make_app


@pytest.mark.asyncio
async def test_dashboard_history_returns_current_local_threads():
    client = make_client()
    client.threads = {'model-a': [{'id': 1, 'role': 'user', 'text': '之前的提问', 'time': 1}],
                      'model-b': [{'id': 2, 'role': 'bot', 'text': '之前的回答', 'time': 2}]}
    async with TestClient(TestServer(make_app(client))) as http:
        response = await http.get('/api/history')
        assert response.status == 200
        assert response.headers['Cache-Control'] == 'no-store'
        assert (await response.json())['threads'] == client.threads
        client.threads = {}
        response = await http.get('/api/history')
        assert (await response.json())['threads'] == {}
