import numpy as np
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from test_barge_in import make_client
from thinksmart_client.agenda_voice import AgendaRecorder, draft
from thinksmart_client.web import make_app


@pytest.mark.parametrize('text,clock,title', [('九点叫我起床。','09:00','叫我起床'),('10:00打开电视','10:00','打开电视'),
    ('十一点提醒我吃药','11:00','提醒我吃药'),('下午三点半给花浇水','15:30','给花浇水'),('散步','','散步'),
    ('25点散步','','25点散步')])
def test_voice_draft(text,clock,title):
    assert draft(text) == {'time':clock,'title':title}


@pytest.mark.asyncio
async def test_record_only_transcribes_no_chat_or_write(tmp_path,monkeypatch):
    from thinksmart_client import config
    monkeypatch.setattr(config,'PATH',tmp_path/'config.json')
    client=make_client(); client.agenda_recorder=AgendaRecorder()
    client.cfg.update(pairing_token='fixture',device_id='dev')
    heard=[]
    async def transcribe(request):
        assert request.headers.get('X-Device-Pairing-Token')=='fixture'
        heard.append(await request.read()); return web.json_response({'text':'十点打开电视'})
    upstream=web.Application(); upstream.router.add_post('/display/agenda-asr',transcribe)
    async with TestServer(upstream) as hub, TestClient(TestServer(make_app(client))) as http:
        client.cfg['hub_http']=str(hub.make_url('/')).rstrip('/')
        async def post(action,token=None):
            return await http.post('/api/panels/agenda-voice',json={'action':action,'token':token},headers={'X-ThinkSmart-Panel':'1'})
        first=await (await post('start')).json()
        assert (await post('start')).status==400
        assert (await post('finish','other')).status==400
        samples=np.ones(4000,dtype=np.float32)*.1
        client._on_block(samples,samples)
        assert await (await post('finish',first['token'])).json()=={'time':'10:00','title':'打开电视'}
        assert (await post('finish',first['token'])).status==400
        second=await (await post('start')).json(); client._on_block(samples,samples)
        assert (await post('cancel',second['token'])).status==200
        assert len(heard)==1 and len(heard[0])==8000 and not client.hub.sent
        assert not (tmp_path/'agenda.db').exists()
