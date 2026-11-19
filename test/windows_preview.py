"""Loopback UI preview. No Linux audio, device controls, or upstream connections."""
import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from thinksmart_client import config
from thinksmart_client.panels import agenda


def application(data_dir):
    config.PATH = data_dir / 'config.json'
    app = web.Application(client_max_size=16384)
    listeners = set()
    model = dict(kind='llm', entry='preview', vendor='preview', label='Windows 预览', model='模拟客户端', logo='openai', company='本机')
    status = dict(state='idle', connected=False, uploading=False, volume=60, brightness={'level':75}, mic=True)
    messages = []
    devices = [dict(id=str(index), name=name, room=room, on=on, writable=on is not None)
               for index, (room,name,on) in enumerate([('客厅','筒灯',True),('客厅','电视',False),('卧室','台灯',False),('卧室','壁灯',None)])]
    recording = None
    reply_handle = None
    build = uuid.uuid4().hex
    if not config.PATH.with_name('agenda.db').exists():
        today = date.today().isoformat()
        for clock,title in [('09:00','叫我起床'),('10:00','打开电视'),('11:00','提醒我吃药')]:
            agenda(today[:7],dict(date=today,title=title,time=clock))

    def emit(kind, payload):
        for queue in tuple(listeners): queue.put_nowait((kind,payload.copy()))

    def state(value):
        status.update(state=value, connected=value!='idle', uploading=value=='listening')
        emit('state',status)

    def message(role, text):
        item=dict(id=uuid.uuid4().int >> 80, entry='preview', role=role, text=text, time=time.time())
        messages.append(item); del messages[:-100]; emit('message',item)

    async def index(request):
        return web.FileResponse(ROOT/'web/v2/index.html',headers={'Cache-Control':'no-store'})

    async def events(request):
        response=web.StreamResponse(headers={'Content-Type':'text/event-stream','Cache-Control':'no-store'})
        await response.prepare(request)
        queue=asyncio.Queue(); listeners.add(queue)
        queue.put_nowait(('snapshot',{'build':build,'status':status,'models':{'current':model,'options':[model]},
                                      'thread':{'entry':'preview','messages':messages}}))
        try:
            while True:
                try: kind,payload=await asyncio.wait_for(queue.get(),10)
                except asyncio.TimeoutError:
                    await response.write(b': keepalive\n\n'); continue
                await response.write(f'event: {kind}\ndata: {json.dumps(payload)}\n\n'.encode())
        except (ConnectionError, asyncio.CancelledError): pass
        finally: listeners.discard(queue)
        return response

    async def api(request):
        nonlocal recording, reply_handle
        kind=request.match_info['kind']; method=request.method
        if request.url.host not in ('127.0.0.1','localhost') or request.headers.get('Origin') not in (None,'http://'+request.host):
            raise web.HTTPForbidden()
        if method not in ('GET','POST','DELETE'): raise web.HTTPMethodNotAllowed(method,['GET','POST','DELETE'])
        if method=='POST' and request.can_read_body and request.content_type!='application/json': raise web.HTTPUnsupportedMediaType()
        body=await request.json() if method=='POST' and request.can_read_body else {}
        if not isinstance(body,dict): raise web.HTTPBadRequest()
        result={'ok':True}
        if kind=='preview-health' and method=='GET': result={'service':'thinksmart-ui-preview','build':build}
        elif kind=='history' and method=='GET': result={'threads':{'preview':messages}}
        elif kind=='network' and method=='GET': result={'ip':'127.0.0.1'}
        elif kind=='server' and method=='GET': result={'hub_http':'','hub_ws':'','pairing_configured':False}
        elif kind=='panels/weather' and method=='GET':
            result={'place':'示例城市','temperature':16,'humidity':58,'condition':'晴','days':[
                {'date':(date.today()+timedelta(days=offset)).isoformat(),'condition':['晴','多云','小雨'][offset%3],
                 'low':8+offset%3,'high':18+offset%4} for offset in range(7)]}
        elif kind=='panels/devices' and method=='GET': result={'devices':devices}
        elif kind=='panels/devices' and method=='POST':
            device=next((row for row in devices if row['id']==body.get('id')),None)
            if not device or not device['writable'] or not isinstance(body.get('on'),bool): raise web.HTTPBadRequest()
            device['on']=body['on']; result={'confirmed':True,'message':'模拟设备状态已更新'}
        elif kind=='panels/agenda-voice' and method=='POST':
            action=body.get('action')
            if action=='start': recording=uuid.uuid4().hex; result={'token':recording}
            elif recording and recording==body.get('token') and action in ('finish','cancel'):
                recording=None
                if action=='finish': result={'time':'15:30','title':'给花浇水'}
            else: return web.json_response({'error':'请重新按住说日程'},status=409)
        elif (kind=='panels/agenda' and method in ('GET','POST')) or (method=='DELETE' and kind.startswith('panels/agenda/') and kind.split('/')[-1].isdigit()):
            try: rows=agenda(request.query.get('month'), body if method=='POST' else None, kind.split('/')[-1] if method=='DELETE' else None)
            except ValueError as error: return web.json_response({'error':str(error)},status=400)
            result={'events':rows}
        elif kind in ('text','ptt/stop') and method=='POST':
            if reply_handle: reply_handle.cancel()
            message('user',str(body.get('text','模拟语音输入'))[:8000]); state('thinking')
            def answer():
                message('bot','这是 Windows 界面预览，不会调用真实模型。'); state('idle')
            reply_handle=asyncio.get_running_loop().call_later(.6,answer)
        elif kind in ('wake','ptt/start','abort','end') and method=='POST':
            if reply_handle: reply_handle.cancel()
            state('listening' if kind in ('wake','ptt/start') else 'idle')
        else: return web.json_response({'ok':False,'error':'此操作需要 Ubuntu 音箱，预览不会执行'},status=501)
        return web.json_response(result,headers={'Cache-Control':'no-store'})

    app.router.add_get('/',index); app.router.add_get('/v2',index); app.router.add_get('/events',events)
    app.router.add_route('*','/api/{kind:.*}',api); app.router.add_static('/static/',ROOT/'web')
    return app


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=18090)
    parser.add_argument('--data-dir',type=Path,default=ROOT/'test/.local/windows-preview')
    args=parser.parse_args()
    web.run_app(application(args.data_dir),host='127.0.0.1',port=args.port,access_log=None)
