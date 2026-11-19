"""History is available to the dashboard before a model has been opened."""
import pytest
from aiohttp.test_utils import TestClient, TestServer

from test_barge_in import make_client
from thinksmart_client.web import make_app
from thinksmart_client import config


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


@pytest.mark.asyncio
async def test_local_agenda_survives_restart_and_rejects_cross_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(config,'PATH',tmp_path/'config.json')
    headers={'X-ThinkSmart-Panel':'1'}
    path='/api/panels/agenda?month=2028-02'
    event={'date':'2028-02-29','title':'晚餐','time':'18:30'}
    async with TestClient(TestServer(make_app(make_client()))) as http:
        assert (await http.post(path,json=event)).status == 403
        assert (await http.post(path,json=event,headers={**headers,'Origin':'https://other.test'})).status == 403
        assert (await http.post(path,json={**event,'date':'2028-02-30'},headers=headers)).status == 400
        created=(await (await http.post(path,json=event,headers=headers)).json())['events']
        assert created[0]['date'] == event['date']
    async with TestClient(TestServer(make_app(make_client()))) as http:
        assert (await (await http.get(path,headers=headers)).json())['events'] == created
        key=created[0]['id']
        assert (await (await http.delete(f'/api/panels/agenda/{key}?month=2028-02',headers=headers)).json())['events'] == []
