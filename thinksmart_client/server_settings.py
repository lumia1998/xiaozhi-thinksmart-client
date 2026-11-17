"""Device-owned Hub addresses and explicit, audio-free connection checks."""
import asyncio
import json
import urllib.parse

import aiohttp
from websockets.asyncio.client import connect


def _url(value, schemes, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'请填写{label}')
    value = value.strip()
    if len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value) or '\\' in value:
        raise ValueError(f'{label}格式不正确')
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
        if (parsed.scheme not in schemes or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or port == 0 or parsed.netloc.endswith(':')):
            raise ValueError()
        host = parsed.hostname.encode('idna').decode('ascii')
        netloc = f'[{host}]' if ':' in host else host
        if port is not None:
            netloc += f':{port}'
        return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, '', ''))
    except (ValueError, UnicodeError):
        raise ValueError(f'{label}格式不正确，请包含协议、主机和端口') from None


def normalize(http, websocket=''):
    http = _url(http, ('http', 'https'), '服务器地址').rstrip('/')
    if not websocket:
        p = urllib.parse.urlsplit(http)
        host = f'[{p.hostname}]' if ':' in p.hostname else p.hostname
        # The stock Hub uses a separate 16689 port; HTTPS deployments normally proxy both paths.
        netloc = f'{host}:16689' if p.port == 16688 else p.netloc
        websocket = urllib.parse.urlunsplit(('wss' if p.scheme == 'https' else 'ws',
                                              netloc, p.path + '/xiaozhi/v1/', '', ''))
    websocket = _url(websocket, ('ws', 'wss'), '语音连接地址')
    if not urllib.parse.urlsplit(websocket).path:
        websocket += '/'
    return {'hub_http': http, 'hub_ws': websocket}


async def check(settings, cfg, live_hub=None):
    async def http_check():
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(settings['hub_http'] + '/kiosk/llm', allow_redirects=False) as r:
                    if r.status == 403:
                        return {'ok': False, 'message': '服务器限制本机访问，模型选择和聊天记录暂不可用'}
                    if r.status != 200:
                        return {'ok': False, 'message': f'屏幕接口返回 HTTP {r.status}'}
                    data = await r.json()
                    if not isinstance(data, dict) or not isinstance(data.get('options'), list):
                        return {'ok': False, 'message': '地址可访问，但不是 Hub 屏幕接口'}
                    return {'ok': True, 'message': '屏幕接口已连接'}
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return {'ok': False, 'message': '无法访问屏幕接口，请检查地址和网络'}

    async def voice_check():
        if live_hub and live_hub.connected and live_hub.url == settings['hub_ws']:
            return {'ok': True, 'message': '语音已连接'}
        try:
            headers = {'Authorization': 'Bearer ' + cfg.get('token', ''), 'Protocol-Version': '1',
                       'Device-Id': cfg['device_id'], 'Client-Id': cfg['client_id']}
            if cfg.get('pairing_token'):
                headers['X-Device-Pairing-Token'] = cfg['pairing_token']
            async with asyncio.timeout(7):
                async with connect(settings['hub_ws'], additional_headers=headers, open_timeout=4,
                                   close_timeout=1, compression=None, max_size=65536) as ws:
                    await ws.send(json.dumps({'type': 'hello', 'version': 1, 'transport': 'websocket',
                                             'features': {}, 'audio_params': {'format': 'opus',
                                             'sample_rate': 16000, 'channels': 1, 'frame_duration': 20}}))
                    async for message in ws:
                        if isinstance(message, str) and json.loads(message).get('type') == 'hello':
                            return {'ok': True, 'message': '语音接口已连接'}
            return {'ok': False, 'message': '服务器未返回语音握手'}
        except Exception:
            return {'ok': False, 'message': '无法连接语音接口，请检查地址和端口'}

    http, voice = await asyncio.gather(http_check(), voice_check())
    return {'http': http, 'voice': voice, 'connected': http['ok'] and voice['ok']}
