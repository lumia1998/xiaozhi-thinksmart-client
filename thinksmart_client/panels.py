import asyncio
from contextlib import closing
from datetime import date
import re
import sqlite3

import aiohttp
from aiohttp import web

from . import agenda_voice, config


def agenda(month, entry=None, remove=None):
    if not isinstance(month, str) or not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])', month):
        raise ValueError('月份无效')
    if entry is not None:
        day, title, clock = (entry.get(field) for field in ('date', 'title', 'time'))
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day or day[:7] != month:
            raise ValueError('日期无效')
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100:
            raise ValueError('请输入 1 至 100 个字的日程名称')
        if not isinstance(clock, str) or (clock and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', clock)):
            raise ValueError('时间无效')
    config.PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(config.PATH.with_name('agenda.db'))) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, clock TEXT, title TEXT)')
        if entry is not None:
            db.execute('INSERT INTO events(day,clock,title) VALUES(?,?,?)', (day, clock, title.strip()))
        if remove is not None:
            db.execute('DELETE FROM events WHERE id=? AND day LIKE ?', (remove, month+'-%'))
        return [{'id': key, 'date': day, 'time': clock, 'title': title} for key, day, clock, title in db.execute(
            'SELECT id,day,clock,title FROM events WHERE day LIKE ? ORDER BY day,clock,id', (month+'-%',))]


def register(app, client):
    async def view(request):
        def reply(payload, status=200):
            return web.json_response(payload, status=status, headers={'Cache-Control':'no-store'})
        if (request.url.host not in ('127.0.0.1', 'localhost', '::1') or request.headers.get('X-ThinkSmart-Panel') != '1'
                or request.headers.get('Origin') not in (None, f'{request.scheme}://{request.host}')):
            return reply({'error':'仅允许音箱面板访问'}, 403)
        body = None
        if request.method == 'POST':
            if request.content_type != 'application/json': return reply({'error':'需要 JSON 请求'}, 400)
            try: body = await request.json()
            except ValueError: return reply({'error':'请求格式错误'}, 400)
            if not isinstance(body, dict): return reply({'error':'请求格式错误'}, 400)
        kind = request.match_info['kind']
        if kind == 'agenda-voice':
            action = body.get('action')
            try:
                if action == 'start':
                    if not client.keys.mic_on: return reply({'error':'麦克风已关闭'}, 409)
                    if client.state != 'idle' or client.uploading: return reply({'error':'请先结束当前对话'}, 409)
                    if not client.cfg.get('pairing_token'): return reply({'error':'请先配置 Hub 配对令牌，或使用文字添加'}, 403)
                    return reply({'token':client.agenda_recorder.start()})
                if action not in ('finish','cancel'): raise ValueError('录音操作无效')
                pcm = client.agenda_recorder.finish(body.get('token'), cancel=action=='cancel')
                if action == 'cancel': return reply({'ok':True})
            except ValueError as error: return reply({'error':str(error)}, 400)
        if kind == 'agenda':
            try:
                rows = await asyncio.to_thread(agenda, request.query.get('month'), body, request.match_info.get('key'))
                return reply({'events': rows})
            except ValueError as error: return reply({'error':str(error)}, 400)
            except (sqlite3.Error, OSError): return reply({'error':'无法读写本机日程'}, 503)
        token = client.cfg.get('pairing_token')
        if not token: return reply({'error':'请先在音箱设置中配置 Hub 配对令牌'}, 403)
        headers = {'Device-Id': client.cfg['device_id'], 'X-Device-Pairing-Token': token}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=22)) as session:
                if kind == 'agenda-voice':
                    async with session.post(client.cfg['hub_http'].rstrip('/')+'/display/agenda-asr', data=pcm,
                                            headers={**headers, 'Content-Type':'audio/L16; rate=16000'}, allow_redirects=False) as response:
                        if response.status != 200: return reply({'error':'语音识别不可用，请检查 Hub 配对和本机 ASR，或用文字添加'}, 502)
                        text = (await response.json()).get('text')
                        if not isinstance(text, str) or not text.strip(): return reply({'error':'没有听清，请再说一次'}, 422)
                        return reply(agenda_voice.draft(text))
                async with session.request(request.method, client.cfg['hub_http'].rstrip('/')+'/display/'+kind,
                                           headers=headers, json=body, allow_redirects=False) as response:
                    if response.status != 200:
                        message = {403:'设备尚未配对或未获授权', 404:'当前 Hub 不支持此面板'}.get(response.status, '服务暂不可用，请稍后刷新')
                        return reply({'error':message}, 502 if response.status != 403 else 403)
                    return reply(await response.json())
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return reply({'error':'未收到 Hub 响应，请刷新核对状态'}, 502)

    app.router.add_get('/api/panels/{kind:weather|devices|agenda}', view)
    app.router.add_post('/api/panels/{kind:devices|agenda|agenda-voice}', view)
    app.router.add_delete('/api/panels/{kind:agenda}/{key:[0-9]+}', view)
