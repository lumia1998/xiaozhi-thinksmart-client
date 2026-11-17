"""Device tools for volume, quiet hours, brightness and scheduled brightness."""
import asyncio
import json
import logging
import re

log = logging.getLogger(__name__)

TOOLS = [
    {
        'name': 'self.audio_speaker.set_volume',
        'description': '设置音箱自身扬声器音量。音量调到50传volume=50，声音大一点传change=10，小一点传change=-10。两个参数只传一个。',
        'inputSchema': {'type': 'object',
                        'properties': {'volume': {'type': 'integer', 'minimum': 0, 'maximum': 100},
                                       'change': {'type': 'integer', 'minimum': -100, 'maximum': 100}}},
    },
    {
        'name': 'self.audio_speaker.get_volume',
        'description': '读取设备扬声器当前的音量（0-100）。',
        'inputSchema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'self.sleep_mode.set',
        'description': '设置设备的睡眠时段：时段内后台任务做完的汇报不主动念，只在屏幕上提示，'
                       '等用户点一下或唤醒再念；唤醒和对话照常，音量不变。'
                       '用户说「晚上十点到早上七点别念了」时 enabled=true, start="22:00", end="07:00"；'
                       '说「关掉睡眠模式」时 enabled=false。时间用 24 小时制 HH:MM，可以跨午夜；只改开关时不用传时间。',
        'inputSchema': {'type': 'object',
                        'properties': {'enabled': {'type': 'boolean'},
                                       'start': {'type': 'string', 'description': '开始时间 HH:MM'},
                                       'end': {'type': 'string', 'description': '结束时间 HH:MM'}},
                        'required': ['enabled']},
    },
    {
        'name': 'self.sleep_mode.get',
        'description': '读取睡眠时段的设置，以及现在是否在睡眠时段内。',
        'inputSchema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'self.screen.set_brightness',
        'description': '调节设备屏幕亮度，两个参数只传一个。亮度 0-100：0 是最暗（仍看得清），100 最亮。'
                       '「屏幕亮一点」change=20，「暗一点」change=-20（「再亮一点」同样传 change，不用先读）；'
                       '「调到最亮」brightness=100，「调到最暗」brightness=0，「亮度调到 60」brightness=60。'
                       '设置常规亮度；定时亮度生效时，常规亮度在时段结束后恢复。',
        'inputSchema': {'type': 'object',
                        'properties': {'brightness': {'type': 'integer', 'minimum': 0, 'maximum': 100},
                                       'change': {'type': 'integer', 'minimum': -100, 'maximum': 100,
                                                  'description': '在当前亮度上加减'}}},
    },
    {
        'name': 'self.screen.get_brightness',
        'description': '读取屏幕亮度：level 当前亮度，normal 常规亮度，dimmed 表示定时亮度生效。',
        'inputSchema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'self.screen.set_brightness_schedule',
        'description': '设置每天的定时亮度，支持跨午夜。时段结束恢复常规亮度。'
                       '例如零点到六点亮度30%，传 enabled=true,start="00:00",end="06:00",level=30。',
        'inputSchema': {'type': 'object', 'properties': {
            'enabled': {'type': 'boolean'}, 'start': {'type': 'string', 'description': 'HH:MM'},
            'end': {'type': 'string', 'description': 'HH:MM'},
            'level': {'type': 'integer', 'minimum': 0, 'maximum': 100}},
            'additionalProperties': False},
    },
    {
        'name': 'self.screen.get_brightness_schedule',
        'description': '查询定时亮度的时间、亮度、开关及当前是否生效。',
        'inputSchema': {'type': 'object', 'properties': {}},
    },
    {
        'name': 'self.camera.take_photo',
        'description': '临时打开音箱摄像头拍摄前方一帧并进行视觉识别，采集后立即释放摄像头。'
                       '用户要求拍照、打开摄像头看看、看我手里是什么、读眼前文字、描述前方画面时调用。'
                       'question填写要识别的问题。需要查看实际画面才能回答时使用此工具，依据返回结果回答。'
                       '用户仅讨论摄像头知识或明确说不要拍照时不调用。',
        'inputSchema': {'type': 'object', 'properties': {
            'question': {'type': 'string', 'minLength': 1, 'maxLength': 2000}},
            'required': ['question'], 'additionalProperties': False},
    },
]


async def _wpctl(*args):
    p = await asyncio.create_subprocess_exec('wpctl', *args, stdout=asyncio.subprocess.PIPE,
                                             stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout=5)
    finally:
        if p.returncode is None:
            p.kill()
            await p.wait()
    if p.returncode:
        raise ValueError('设备音量控制失败，请检查音频服务')
    return out.decode()


async def get_volume():
    m = re.search(r'([\d.]+)', await _wpctl('get-volume', '@DEFAULT_AUDIO_SINK@'))
    return round(float(m.group(1)) * 100) if m else None


async def set_volume(volume):
    await _wpctl('set-volume', '@DEFAULT_AUDIO_SINK@', f'{max(0, min(100, int(volume))) / 100:.2f}')


def _dumps(value):
    return json.dumps(value, ensure_ascii=False)


async def handle(payload, client=None):
    """One JSON-RPC request from the hub; returns the reply payload (None for notifications)."""
    method, req_id, params = payload.get('method'), payload.get('id'), payload.get('params') or {}
    if req_id is None:
        return None

    def result(value):
        return {'jsonrpc': '2.0', 'id': req_id, 'result': value}

    def error(message):
        return {'jsonrpc': '2.0', 'id': req_id, 'error': {'code': -32601, 'message': message}}

    if method == 'initialize':
        if client:
            client.camera.configure(params.get('capabilities') or {})
        return result({'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                       'serverInfo': {'name': 'thinksmart-client', 'version': '0.1.0'}})
    if method == 'tools/list':
        return result({'tools': TOOLS})
    if method == 'tools/call':
        name, args = params.get('name'), params.get('arguments') or {}
        try:
            if name == 'self.audio_speaker.set_volume' and client:
                volume = await client.adjust_volume(volume=args.get('volume'), change=args.get('change'))
                text = json.dumps({'success': True, 'volume': volume})
            elif name == 'self.audio_speaker.get_volume' and client:
                text = json.dumps({'volume': await client.device_volume()})
            elif name == 'self.sleep_mode.set' and client:
                text = json.dumps({'success': True, 'sleep': await client.set_sleep(
                    enabled=args.get('enabled'), start=args.get('start'), end=args.get('end'))},
                    ensure_ascii=False)
            elif name == 'self.sleep_mode.get' and client:
                text = json.dumps({**client.sleep, 'active': client.sleeping}, ensure_ascii=False)
            elif name == 'self.screen.set_brightness' and client:
                if (args.get('brightness') is None) == (args.get('change') is None):
                    raise ValueError('请提供一个亮度值或一个调整量')
                text = json.dumps({'success': True, **await client.set_brightness(
                    level=args.get('brightness'), change=args.get('change'))})
            elif name == 'self.screen.get_brightness' and client:
                text = json.dumps(client.brightness())
            elif name == 'self.screen.set_brightness_schedule' and client:
                text = _dumps({'success': True, 'brightness_schedule': await client.set_brightness_schedule(**args)})
            elif name == 'self.screen.get_brightness_schedule' and client:
                text = _dumps({**client.brightness_schedule, 'active': client.schedule_active})
            elif name == 'self.camera.take_photo' and client:
                text = _dumps(await client.camera.explain(args.get('question')))
            else:
                return error(f'Unknown tool: {name}')
        except Exception as e:
            log.exception('tool %s failed', name)
            return result({'content': [{'type': 'text', 'text': str(e)}], 'isError': True})
        return result({'content': [{'type': 'text', 'text': text}], 'isError': False})
    return error(f'Method not implemented: {method}')
