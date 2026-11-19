"""The page the kiosk shows (127.0.0.1 only): static files, an event stream and button actions."""
import asyncio
import json
import logging
import time

from aiohttp import web

from . import __version__, panels, server_settings
from .app import ROOT, Client, local_ip

log = logging.getLogger(__name__)
WEB = ROOT / 'web'
# changes on every restart (every deploy restarts the client): the page reloads itself when it
# reconnects to a different run, so the kiosk never keeps running an old app.js
BUILD = str(int(time.time()))


def make_app(client: Client):
    app = web.Application()
    panels.register(app, client)

    async def index(_request):
        # the second design (orb with a face) is the UI since 2026-10-31; /v1 keeps the first one
        return web.FileResponse(WEB / 'v2' / 'index.html', headers={'Cache-Control': 'no-cache'})

    async def index_v1(_request):
        raise web.HTTPFound('/')

    async def events(request):
        resp = web.StreamResponse(headers={'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache'})
        await resp.prepare(request)
        q = asyncio.Queue()
        client.events.queues.add(q)
        try:
            q.put_nowait(('snapshot', {**client.snapshot(), 'build': BUILD}))
            while True:
                try:
                    event, data = await asyncio.wait_for(q.get(), timeout=15)
                    await resp.write(f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'.encode())
                except asyncio.TimeoutError:
                    await resp.write(b': keepalive\n\n')
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            client.events.queues.discard(q)
        return resp

    def action(coro_fn):
        async def handler(_request):
            asyncio.create_task(coro_fn())
            return web.json_response({'ok': True})
        return handler

    async def text(request):
        body = await request.json()
        asyncio.create_task(client.say(body.get('text', '')))
        return web.json_response({'ok': True})

    async def choice(request):
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get('id'), str) or not isinstance(body.get('option_id'), str):
            return web.json_response({'ok': False, 'error': '选择参数无效'}, status=400)
        return web.json_response(await client.select_choice(body['id'], body['option_id']))

    async def history(_request):
        return web.json_response({'threads': client.threads}, headers={'Cache-Control': 'no-store'})

    async def media_playback(request):
        body = await request.json()
        if (not isinstance(body, dict) or not isinstance(body.get('playing'), bool)
                or not isinstance(body.get('heartbeat', False), bool)):
            return web.json_response({'ok': False, 'error': '播放状态格式不正确'}, status=400)
        return web.json_response(await client.media_playback(body['playing'], body.get('heartbeat', False)))

    async def model(request):
        body = await request.json()
        return web.json_response(await client.set_model(body.get('kind'), body.get('vendor'), body.get('entry')))

    async def sleep_setting(request):
        body = await request.json()
        try:
            value = await client.set_sleep(**{k: body.get(k) for k in ('enabled', 'start', 'end')})
        except ValueError as e:
            return web.json_response({'ok': False, 'error': str(e)}, status=400)
        return web.json_response({'ok': True, 'sleep': value})

    async def brightness(request):
        body = await request.json()
        try:
            value = await client.set_brightness(body.get('level'))
        except (TypeError, ValueError) as e:
            return web.json_response({'ok': False, 'error': str(e)}, status=400)
        return web.json_response({'ok': True, 'brightness': value})

    async def screen_off(request):
        body = await request.json()
        try:
            value = await client.set_screen_off(**{k: body.get(k) for k in ('enabled', 'minutes')})
        except ValueError as e:
            return web.json_response({'ok': False, 'error': str(e)}, status=400)
        return web.json_response({'ok': True, 'screen_off': value})

    async def brightness_schedule(request):
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError('定时亮度设置格式不正确')
            value = await client.set_brightness_schedule(**body)
        except (ValueError, TypeError) as error:
            return web.json_response({'ok': False, 'error': str(error)}, status=400)
        except OSError:
            return web.json_response({'ok': False, 'error': '保存失败，请检查设备存储空间'}, status=500)
        return web.json_response({'ok': True, 'brightness_schedule': value})

    async def volume(request):
        body = await request.json()
        try:
            value = await client.set_volume(body.get('volume'))
        except (TypeError, ValueError) as e:
            return web.json_response({'ok': False, 'error': str(e)}, status=400)
        return web.json_response({'ok': True, 'volume': value})

    async def clear_history(request):
        body = await request.json()
        return web.json_response(await client.clear_history(bool(body.get('memory_only'))))

    async def bluetooth(request):
        body = await request.json()
        args = {k: body[k] for k in ('enabled', 'window', 'remove', 'disconnect') if k in body}
        ok = await client.bluetooth(**args)
        return web.json_response({'ok': bool(ok), **({} if ok else {'error': '蓝牙没有响应'})})

    async def health(_request):
        return web.json_response({'version': __version__, **client.health()})

    async def power_off(_request):
        return web.json_response(await client.power_off())

    async def network(_request):
        return web.json_response({'ip': local_ip()})

    async def server_get(_request):
        return web.json_response({k: client.cfg[k] for k in ('hub_http', 'hub_ws')}
                                 | {'pairing_configured': bool(client.cfg.get('pairing_token'))})

    async def server_change(request):
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError('请求格式不正确')
            pairing_token = body.get('pairing_token') if 'pairing_token' in body else None
            clear_pairing = body.get('clear_pairing', False)
            if pairing_token is not None and not isinstance(pairing_token, str):
                raise ValueError('配对令牌格式不正确')
            if not isinstance(clear_pairing, bool):
                raise ValueError('清除选项格式不正确')
            values = await client.set_server(
                body.get('hub_http'), body.get('hub_ws', ''),
                pairing_token, clear_pairing,
            )
            return web.json_response({'ok': True, **values})
        except (ValueError, TypeError) as e:
            return web.json_response({'ok': False, 'error': str(e)}, status=400)
        except OSError:
            return web.json_response({'ok': False, 'error': '保存失败，请检查设备存储空间'}, status=500)

    async def server_check(request):
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError('请求格式不正确')
            values = server_settings.normalize(body.get('hub_http'), body.get('hub_ws', ''))
            pairing_token = body.get('pairing_token')
            clear_pairing = body.get('clear_pairing', False)
            if pairing_token is not None and not isinstance(pairing_token, str):
                raise ValueError('配对令牌格式不正确')
            if not isinstance(clear_pairing, bool):
                raise ValueError('清除选项格式不正确')
            pairing_token = (pairing_token or '').strip()
            if pairing_token and (len(pairing_token) > 1000 or any(c.isspace() or ord(c) < 32 for c in pairing_token)):
                raise ValueError('配对令牌格式不正确')
        except (ValueError, TypeError) as e:
            return web.json_response({'ok': False, 'error': str(e)}, status=400)
        check_cfg = dict(client.cfg)
        if clear_pairing:
            check_cfg['pairing_token'] = ''
        elif pairing_token:
            check_cfg['pairing_token'] = pairing_token
        return web.json_response({'ok': True, **await server_settings.check(values, check_cfg, client.hub)})

    app.router.add_get('/', index)
    app.router.add_get('/v2', index)
    app.router.add_get('/v1', index_v1)
    app.router.add_get('/events', events)
    app.router.add_post('/api/wake', action(lambda: client.wake('button')))
    app.router.add_post('/api/end', action(client.end))
    app.router.add_post('/api/abort', action(client.abort))
    app.router.add_post('/api/ptt/start', action(client.ptt_start))
    app.router.add_post('/api/ptt/stop', action(client.ptt_stop))
    app.router.add_post('/api/model', model)
    app.router.add_post('/api/text', text)
    app.router.add_post('/api/choice', choice)
    app.router.add_get('/api/history', history)
    app.router.add_post('/api/media/playback', media_playback)
    app.router.add_post('/api/sleep', sleep_setting)
    app.router.add_post('/api/brightness', brightness)
    app.router.add_post('/api/brightness-schedule', brightness_schedule)
    app.router.add_post('/api/screen-off', screen_off)
    app.router.add_post('/api/volume', volume)
    app.router.add_post('/api/reports/play', action(client.play_reports))
    app.router.add_post('/api/history/clear', clear_history)
    app.router.add_post('/api/bluetooth', bluetooth)
    app.router.add_get('/api/health', health)
    app.router.add_get('/api/network', network)
    app.router.add_post('/api/power-off', power_off)
    app.router.add_get('/api/server', server_get)
    app.router.add_post('/api/server', server_change)
    app.router.add_post('/api/server/check', server_check)
    app.router.add_static('/static/', WEB)

    async def revalidate(request, response):
        # the kiosk keeps a disk cache; without this it goes on running the old app.js/css after
        # an update (heuristic freshness from Last-Modified). no-cache = check the ETag every load.
        if request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-cache'
    app.on_response_prepare.append(revalidate)
    return app
