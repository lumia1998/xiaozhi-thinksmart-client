"""The voice client: wake word → listen → hub replies → follow-up window → idle.

States shown to the page: idle, connecting, listening, thinking, speaking.
- Wake (voice or button): chime, connect if needed, stream the microphone ("realtime": the
  hub cuts sentences with its VAD).
- Once the hub has recognised what was said, the microphone stops uploading until the reply has
  been played: talking during a reply (to someone else, "嗯", a cough) never interrupts it. Only
  the wake word (still listened for meanwhile) or the button does: the reply stops, the hub is told
  how much of it was heard (it keeps only that in the context), and what is said next is a new turn.
  The same while the hub is still recognising what was said (the wake word is listened for again
  once its VAD says the user stopped) or thinking: the question is dropped, whatever of that turn
  still arrives (its text, its reply) is ignored, and the mic opens for a new one at once.
- After a reply the microphone stays open for follow_up_seconds. The device closes the
  connection after conversation_idle_seconds of user silence, including when upload stopped.
  Recognition, model/tool work and reply playback do not count toward that timeout.
- The hub keeps every model's conversation on disk (data/chat.db) and gives the next connection
  its recent part, so the page's threads survive a closed connection and a restart: they are loaded
  from the hub (/kiosk/chat/history) when not connected. Clearing them (settings, or saying
  "清除聊天记录") clears the hub's copy too.
- The screen stays on while not idle and blanks after the user's screen-off (device.Screen); the volume
  keys and the mic-mute switch are handled here too (device.Keys).
- Reports are polled from the hub and wait on the screen during quiet hours.
- Scheduled brightness applies daily and restores the normal brightness after the interval.
- Bluetooth speaker (bt.py): a phone's music is mixed under the replies, lowered from the wake to the
  end of the reply.
"""
import asyncio
import json
import logging
import os
import re
import socket
import time
import urllib.parse
from pathlib import Path

import aiohttp
import numpy as np

from . import brightness_schedule, bt, camera, config, mcp, media, sleep, wake_verify, server_settings
from .audio import OUT_RATE, Audio
from .device import Keys, Screen, normalize_screen_off
from .hub import Hub
from .silence import SilenceTimer
from .kws import Kws
from .opus import Decoder, Encoder

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
# after echo cancellation, above this the user is probably still talking (keeps the mic open).
# Was 0.012 (-38 dBFS): speech from across the room (~-42 dBFS in the wake word evaluation) fell
# under it, the mic closed after 10 s while the user was mid-sentence and the hub never got the
# end of it. Room noise here is ~-57 dBFS.
SPEECH_RMS = 0.005
# halt, not poweroff: with the power cable in, the board powers straight back on after a poweroff;
# halted it stays dark with the file systems unmounted, so the cable can be pulled safely
HALT = ('sudo', '-n', 'systemctl', 'halt')
HALT_AFTER = 3           # seconds to read "正在关机 … 等 10 秒再拔" before the screen goes dark
MAX_LISTEN = 60          # seconds: one wake never keeps the mic open longer (a TV could otherwise)
RECOGNITION_WAIT = 15    # seconds to wait for the hub's text after the user stops (long sentences)
NORMAL_STOP_TAIL = 0.6   # a normal "tts stop" leaves less than this queued; more means aborted
PUNCT = set('，。！？；：、,.!?;:…')
REPORT_POLL = 10         # seconds between asking the hub for pending reports (local HTTP)
HISTORY_LIMIT = 50       # messages per model shown from the hub's saved conversations
MODEL_POLL = 30          # seconds between asking the hub for its models (changes made in its admin page)
MODEL_RETRY = 3          # until the hub answers (it takes over a minute to start after a boot)
BACKGROUND_DUCKED = 0.2  # a phone's music (Bluetooth speaker) under a conversation

# Hub vendor id -> (logo file, company). Unknown vendors are guessed from the model name.
VENDORS = {
    'claude': ('claude-color', 'Anthropic'), 'claude-code': ('claude-color', 'Anthropic'),
    'claude_cli': ('claude-color', 'Anthropic'), 'openai': ('openai', 'OpenAI'),
    'gemini': ('gemini-color', 'Google'), 'deepseek': ('deepseek-color', 'DeepSeek'),
    'qwen': ('qwen-color', '阿里通义'), 'doubao': ('doubao-color', '字节豆包'),
    'glm': ('zhipu-color', '智谱'), 'kimi': ('kimi-color', '月之暗面'),
    'grok_bot': ('grok', 'Grok Bot'), 'grok': ('grok', 'xAI'),
}
BY_MODEL = [
    (('claude',), 'claude'), (('deepseek',), 'deepseek'), (('gemini',), 'gemini'),
    (('qwen', 'qwq'), 'qwen'), (('doubao',), 'doubao'), (('glm',), 'glm'),
    (('kimi', 'moonshot'), 'kimi'), (('grok',), 'grok'), (('gpt', 'o1-', 'o3', 'o4-'), 'openai'),
]


def describe(option):
    vendor = option['vendor']
    if vendor not in VENDORS:
        low = (option.get('model') or '').lower()
        vendor = next((v for keys, v in BY_MODEL if any(k in low for k in keys)), vendor)
    logo, company = VENDORS.get(vendor, ('', option.get('label', '')))
    return {'kind': option['kind'], 'vendor': option['vendor'], 'label': option['label'],
            'entry': option['entry'], 'model': option.get('model', ''), 'logo': logo, 'company': company}


def marimba(notes, gap=0.1, peak=0.2):
    """Soft marimba notes as 48 kHz stereo: every prompt sound (wake, goodbye, didn't catch that,
    no hub, a new report) is from this one family. notes: Hz, one every `gap` seconds."""
    dur = 0.45
    t = np.arange(int(OUT_RATE * dur)) / OUT_RATE
    out = np.zeros(int(OUT_RATE * (gap * (len(notes) - 1) + dur)))
    for i, f in enumerate(notes):
        # a struck bar: the fundamental rings, the 4th and ~10th partials only for the strike
        x = (np.sin(2 * np.pi * f * t) * np.exp(-9 * t)
             + 0.35 * np.sin(2 * np.pi * 4 * f * t) * np.exp(-28 * t)
             + 0.12 * np.sin(2 * np.pi * 9.9 * f * t) * np.exp(-45 * t)) * np.minimum(1, t / 0.0015)
        start = int(OUT_RATE * gap * i)
        out[start:start + len(x)] += x
    out[-int(OUT_RATE * 0.015):] *= np.linspace(1, 0, int(OUT_RATE * 0.015))
    x = (out * peak / np.max(np.abs(out))).astype(np.float32)
    return np.repeat(x[:, None], 2, axis=1)


C5, E5, G5, A5, B5, C6, D6, E6 = 523.25, 659.25, 783.99, 880.0, 987.77, 1046.5, 1174.66, 1318.5


def has_default_route():
    """An IPv4 default route (/proc/net/route: destination 00000000), as a hint whether the device is online."""
    try:
        with open('/proc/net/route') as f:
            return any(line.split()[1] == '00000000' for line in f.readlines()[1:] if len(line.split()) > 1)
    except OSError:
        return True   # can't tell: don't claim it is offline


def local_ip():
    """The LAN address the device reaches the network from (the one to type into a computer's browser), or None offline.
    connect() on a UDP socket only picks the route and source address; nothing is sent."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('1.1.1.1', 53))   # a unicast address: a broadcast one is refused without SO_BROADCAST
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith('127.') or ip == '0.0.0.0' else ip

class Events:
    """Fan-out to the page's Server-Sent Events streams."""

    def __init__(self):
        self.queues = set()

    def publish(self, event, data):
        for q in list(self.queues):
            if q.qsize() < 500:
                q.put_nowait((event, data))


class Client:
    def __init__(self, cfg):
        self.cfg = cfg
        from .agenda_voice import AgendaRecorder
        self.agenda_recorder = AgendaRecorder()
        self.silence = SilenceTimer(cfg.get('conversation_idle_seconds', 20))
        self.events = Events()
        self.camera = camera.Camera(cfg, self.events.publish)
        self.camera_requests = set()
        self.loop = None
        self.state = 'idle'
        self.uploading = False
        self.deadline = None          # monotonic: stop listening if nothing is said by then
        self.listen_since = 0.0       # monotonic: when this wake started listening
        self.listen_mode = None       # 'realtime' (the hub's VAD ends it) or 'manual' (push-to-talk)
        self.ptt_held = False         # the mic button is held down (push-to-talk)
        self.powering_off = False     # 关机 was confirmed: the halt is on its way
        self.awaiting_text = False    # the user spoke, the mic is closed, the hub is still recognising
        self.recognising = False      # the hub heard the user stop (its VAD) and is recognising, mic still open
        self.hub_pending = False      # the hub said "vad end" and hasn't answered it yet (text or "empty")
        self.vad_ended = False        # the hub is recognising, kept while the user talks on: the wake word is listened for
        self.aborted_at = None        # monotonic: the last abort, until listening starts again (logged)
        self.abort_pending = None     # monotonic: abort sent, the hub hasn't confirmed it yet (its "tts stop")
        self.question = None          # the user's message of this turn, until its reply starts
        self.heard = False            # anything recognised since this wake (so a listening window that
                                      # times out after it is a follow-up: it ends quietly)
        self.mute_audio = False       # after an abort, drop the old reply's frames
        self.listen_after_reply = False   # the mic was closed for this reply: reopen it when played
        self.reply_samples = 0        # reply audio received since "tts start" (to tell what was heard)
        self.reply_packets = 0
        self.muted_packets = 0
        self.recognised = 0           # count of "stt" messages
        self.threads = {}             # model entry -> messages (the hub's saved ones, then this connection's)
        self.history_loaded = False
        self.entry = None             # the model this connection talks to
        self.models = {'current': None, 'options': []}
        self.bot_msg = None
        self.media_until = 0.0
        self.decoder = None
        self._seq = 0
        self._send_q = None
        self.encoder = Encoder(16000, 1)
        self.kws = Kws(str(ROOT / 'models' / 'kws-zh'), cfg['wake_words'],
                       cfg['kws_score'], cfg['kws_threshold'], cfg['kws_paths'], cfg['kws_gain_db'])
        self.verifier = wake_verify.make(cfg['wake_verify'], cfg['wake_words'], cfg['hub_http'])
        self.backlog = None           # mic blocks since a wake word hit, uploaded once listening starts
        self.audio = Audio(self._on_block)
        self.hub = Hub(cfg['hub_ws'], cfg['device_id'], cfg['client_id'], cfg['token'],
                       self._on_json, self._on_audio, self._on_closed, cfg.get('pairing_token', ''))
        # Local wake feedback is a neutral chime; spoken greetings use the Hub's selected TTS.
        self.sounds = {'wake': marimba([G5, D6], 0.09), 'bye': marimba([D6, G5], 0.12, 0.16),
                       'miss': marimba([E6, C6], 0.12, 0.16), 'error': marimba([A5, E5, C5], 0.14),
                       'report': marimba([G5, B5, D6], 0.08)}
        self.speech_end = 0.0         # monotonic: last time the user was heard talking
        self.tools_listed = asyncio.Event()   # the hub asked for our device tools on this connection
        self.connecting = asyncio.Lock()      # one connection: a second wake while connecting waits for it
        self.screen = Screen()
        try:
            self.sleep = sleep.normalize(cfg.get('sleep'))
        except ValueError:
            log.warning('invalid sleep setting %r, using the default', cfg.get('sleep'))
            self.sleep = sleep.normalize(None)
        self.sleeping = False
        self.brightness_schedule = brightness_schedule.normalize(None)
        try:
            self.brightness_schedule = brightness_schedule.normalize(cfg.get('brightness_schedule'))
        except ValueError:
            log.warning('invalid brightness schedule, using defaults')
        self.schedule_active = False
        level = cfg.get('brightness')
        if isinstance(level, bool) or not isinstance(level, (int, float)):
            log.warning('invalid brightness %r, using 100', level)
            level = 100
        self.screen.level = max(0, min(100, int(level)))
        self.normal_brightness = self.screen.level
        try:
            self.screen_off = normalize_screen_off(cfg.get('screen_off'))
        except ValueError:
            log.warning('invalid screen_off %r, using the default', cfg.get('screen_off'))
            self.screen_off = normalize_screen_off(None)
        self.screen.idle = self._screen_off_seconds()   # written by start()'s release()
        self.reports = 0              # pending on the hub
        self.hub_seen = 0.0           # monotonic: last answer from the hub's HTTP side (health check)
        self.keys = Keys(on_step=self.volume_step, on_mic=self._on_mic)
        self.volume = None            # the device volume 0-100 (keys, voice, the page), as last read or set
        self.volume_lock = asyncio.Lock()   # one change of the speaker's volume at a time
        self.bt = bt.Bluetooth(self.audio.background, self.events.publish, cfg.get('bluetooth') is not False)

    # ---- startup ----

    async def start(self):
        self.loop = asyncio.get_running_loop()
        self._send_q = asyncio.Queue(maxsize=200)
        self.audio.start()
        self.keys.start()
        self.screen.release()   # a hold left by a previous run must not keep the screen on
        self._apply_sleep()
        self._apply_brightness_schedule()
        asyncio.create_task(self._sender())
        asyncio.create_task(self._ticker())
        self.hub_pollers = [asyncio.create_task(self._model_poller()),
                            asyncio.create_task(self._report_poller())]
        await self.device_volume()
        await self.bt.start()
        asyncio.create_task(self._bluetooth_loop())

    # ---- microphone (worker thread) ----

    def _on_block(self, block, wake_block):
        recorder = getattr(self, 'agenda_recorder', None)
        if recorder and recorder.feed(block):
            return
        # While streaming to the hub the wake word isn't needed (speech already reaches it), and
        # the CPU it would take is better left to the hub's speech recognition. wake_block: what the
        # wake word hears (during a reply, echo-cancelled without suppressing the voice; audio.py)
        # (after the hub's VAD said the user stopped, the wake word interrupts the recognition)
        hit = None if self.uploading and not self.vad_ended else self.kws.feed(wake_block)
        backlog = self.backlog
        if hit:
            log.info('wake word: %s', hit)
            if self.vad_ended:
                self.uploading = False   # keep what follows for the new question, not the old one
            # keep what follows: checking the hit, the chime and connecting take a moment, and
            # "小葵，今天..." is often said in one go
            self.backlog = []
            audio = self.kws.recent()
            self.loop.call_soon_threadsafe(lambda: asyncio.create_task(self.voice_wake(hit, audio)))
        elif backlog is not None and not self.uploading:
            backlog.append(block)
            del backlog[:-200]   # 4 s, what the send queue takes at once
        elif self.uploading:
            self.backlog = None
            for b in (backlog or []) + [block]:
                packet = self.encoder.encode(np.clip(b * 32768, -32768, 32767).astype(np.int16))
                self.loop.call_soon_threadsafe(self._queue_packet, packet)

    def _queue_packet(self, packet):
        if not self._send_q.full():
            self._send_q.put_nowait(packet)

    async def _sender(self):
        while True:
            packet = await self._send_q.get()
            if self.uploading:
                await self.hub.send_audio(packet)

    # ---- state ----

    def set_state(self, state):
        if state != 'idle' and getattr(self, 'agenda_recorder', None):
            self.agenda_recorder.reset()
        if state not in ('thinking', 'speaking'):
            self.tool_status = None
        if state != 'idle' and self.media_active():
            self.media_until = 0.0
            self.events.publish('media_pause', {})
        if state not in ('idle', 'listening'):
            self.silence.reset()
        self.audio.aec_hold = self.hub.connected or self.state == 'connecting'
        if state != self.state:
            self.state = state
            self.recognising = False
            log.info('state: %s', state)
            # only on a real change: set_state(self.state) just refreshes the page
            if state == 'idle' and not self.media_active():
                self.screen.release()
            else:
                self.screen.hold()
        self.events.publish('state', self.status())

    def status(self):
        return {'state': self.state, 'connected': self.hub.connected, 'uploading': self.uploading,
                'recognising': self.recognising or self.awaiting_text,
                'tool_hint': (getattr(self, 'tool_status', None) or ('', ''))[1],
                'mic': self.keys.mic_on, 'sleep': {**self.sleep, 'active': self.sleeping},
                'brightness': self.brightness(), 'screen_off': self.screen_off,
                'brightness_schedule': {**self.brightness_schedule, 'active': self.schedule_active},
                'reports': self.reports, 'volume': self.volume, 'media_playing': self.media_active()}

    def media_active(self):
        return getattr(self, 'media_until', 0.0) > time.monotonic()

    async def media_playback(self, playing, heartbeat=False):
        if playing:
            if heartbeat and not self.media_active():
                return {'ok': False, 'error': '播放已暂停'}
            if self.powering_off:
                return {'ok': False, 'error': '设备正在关机'}
            if not self.media_active():
                interrupt = self.hub.connected and (self.in_turn() or self.uploading or self.listen_after_reply)
                self.listen_after_reply = False
                self.uploading = False
                if interrupt:
                    await self.abort()
                await self.stop_uploading()
            self.media_until = time.monotonic() + 15
            self.screen.hold()
        else:
            self.media_until = 0.0
            if self.state == 'idle':
                self.screen.release()
        self.events.publish('state', self.status())
        return {'ok': True}

    def mic_off_toast(self):
        if self.keys.mic_on:
            return False
        self.play('miss')
        self.events.publish('toast', {'kind': 'info', 'text': '麦克风已关，拨开机身侧面的开关就能说话'})
        return True

    def _on_mic(self, on):
        if not on and getattr(self, 'agenda_recorder', None):
            self.agenda_recorder.reset()
        log.info('mic switch: %s', 'on' if on else 'off')
        self.events.publish('toast', {'kind': 'info', 'text': '麦克风已打开' if on else '麦克风已关'})
        if not on and self.uploading:
            asyncio.create_task(self.stop_uploading())
        else:
            self.set_state(self.state)

    def health(self):
        """Audio streams and successful recent Hub HTTP responses."""
        return {'audio': bool(self.audio._streams) and all(s.active for s in self.audio._streams),
                'hub': self.hub_seen > 0 and time.monotonic() - self.hub_seen < 30}

    def snapshot(self):
        return {'status': self.status(), 'models': self.models, 'bluetooth': self.bt.view(),
                'thread': {'entry': self.view_entry(), 'messages': self.threads.get(self.view_entry(), [])}}

    async def set_server(self, http, websocket='', pairing_token=None, clear_pairing=False):
        values = server_settings.normalize(http, websocket)
        if pairing_token is not None and not isinstance(pairing_token, str):
            raise ValueError('配对令牌格式不正确')
        if not isinstance(clear_pairing, bool):
            raise ValueError('清除选项格式不正确')
        token = None if pairing_token is None else str(pairing_token).strip()
        if token and (len(token) > 1000 or any(c.isspace() or ord(c) < 32 for c in token)):
            raise ValueError('配对令牌格式不正确')
        token_changed = bool(token) or bool(clear_pairing)
        async with self.connecting:
            if self.state != 'idle' or self.powering_off:
                raise ValueError('请在对话和响铃结束后修改服务器')
            same_server = all(self.cfg.get(k) == v for k, v in values.items())
            if same_server and not token_changed:
                return {**values, 'pairing_configured': bool(self.cfg.get('pairing_token'))}
            if same_server:
                pairing = '' if clear_pairing else token
                config.save(pairing_token=pairing)
                self.cfg['pairing_token'] = pairing
                self.hub.pairing_token = pairing
                if pairing:
                    self.hub.headers['X-Device-Pairing-Token'] = pairing
                else:
                    self.hub.headers.pop('X-Device-Pairing-Token', None)
                if self.hub.connected:
                    await self.end()
                return {**values, 'pairing_configured': bool(pairing)}
            # Save first: a disk failure must leave the currently working configuration intact.
            saved = dict(values)
            if clear_pairing:
                saved['pairing_token'] = ''
            elif token:
                saved['pairing_token'] = token
            config.save(**saved)
            for task in self.hub_pollers:
                task.cancel()
            await asyncio.gather(*self.hub_pollers, return_exceptions=True)
            await self.end()
            self.cfg.update(values)
            if clear_pairing:
                self.cfg['pairing_token'] = ''
            elif token:
                self.cfg['pairing_token'] = token
            self.hub.pairing_token = self.cfg.get('pairing_token', '')
            if self.hub.pairing_token:
                self.hub.headers['X-Device-Pairing-Token'] = self.hub.pairing_token
            else:
                self.hub.headers.pop('X-Device-Pairing-Token', None)
            self.hub.url = values['hub_ws']
            self.verifier = wake_verify.make(self.cfg['wake_verify'], self.cfg['wake_words'], values['hub_http'])
            self.hub_seen = 0.0
            self.history_loaded = False
            self.models = {'current': None, 'options': []}
            self.threads = {}
            self.entry = None
            self.reports = 0
            self.events.publish('model', self.models)
            self.events.publish('thread', {'entry': None, 'messages': [], 'reset': True})
            self.set_state('idle')
            self.hub_pollers = [asyncio.create_task(self._model_poller()),
                                asyncio.create_task(self._report_poller())]
            return {**values, 'pairing_configured': bool(self.cfg.get('pairing_token'))}

    def view_entry(self):
        cur = self.models.get('current')
        return self.entry or (cur['entry'] if cur else None)

    def play(self, name):
        sound = self.sounds.get(name)
        if sound is not None:
            self.audio.player.push(sound)

    # ---- actions (from the wake word and the page) ----

    async def ensure_connected(self):
        async with self.connecting:
            return await self._connect()

    async def _connect(self):
        if self.hub.connected:
            return True
        self.set_state('connecting')
        self.tools_listed.clear()
        try:
            await self.hub.connect()
        except Exception as e:
            log.warning('cannot reach the hub: %s', e)
            self.play('error')
            self.events.publish('toast', {'kind': 'error', 'text': '连不上小智网关，请检查网关是否在运行'})
            self.set_state('idle')
            return False
        self.decoder = None
        # a new connection has no old reply to drop: the hub's own announcements (reports) send no
        # "tts start", so a mute left over from the last abort/end would silence them
        self.mute_audio = False
        return True

    async def voice_wake(self, label, audio):
        """The wake word model fired. With a second look (wake_verify.py), which takes 1-2 s here,
        the chime still comes at once, but connecting and listening wait for its answer: what is
        said meanwhile is kept (backlog) and sent on, and a false wake ends quietly instead of
        with the hub answering the TV or a "didn't catch that"."""
        if not self.verifier:
            await self.wake('voice')
        elif self.in_turn():
            # don't cut a reply off for what may not be the wake word
            if (await self._check_wake(label, audio))['ok']:
                await self.wake('voice')
        elif not self.mic_off_toast():
            self.screen.poke()
            self.chime('voice')
            self.set_state('connecting')
            if (await self._check_wake(label, audio))['ok']:
                await self.wake('voice', chimed=True)
            else:
                log.info('not the wake word after all: back to idle')
                self.set_state('idle')
        if not self.uploading:
            self.backlog = None

    async def _check_wake(self, label, audio):
        return await asyncio.get_running_loop().run_in_executor(None, self.verifier.check, audio, label)

    def chime(self, source):
        self.play('wake')
        self.events.publish('woke', {'source': source})

    def in_turn(self):
        """The hub is recognising, thinking or speaking: the wake word interrupts it."""
        return (self.state in ('thinking', 'speaking') or self.vad_ended) and self.hub.connected

    async def wake(self, source='button', chimed=False):
        self.screen.poke()
        if self.mic_off_toast():
            return
        if self.in_turn():
            await self.abort('wake_word_detected')
        if not chimed:
            self.chime(source)
        if not await self.ensure_connected():
            return
        await self.start_listening('realtime')
        self.heard = False
        self.deadline = time.monotonic() + self.cfg['follow_up_seconds']

    async def start_listening(self, mode):
        await self.hub.send({'type': 'listen', 'state': 'start', 'mode': mode})
        self.uploading = True
        self.listen_mode = mode
        self.vad_ended = False
        self.listen_since = time.monotonic()
        self.silence.reset(self.listen_since)
        self.awaiting_text = False
        self.hub_pending = False
        if self.aborted_at is not None:
            log.info('listening again %.0f ms after the abort', (self.listen_since - self.aborted_at) * 1000)
            self.aborted_at = None
        self.set_state('listening')

    async def stop_uploading(self, missed=False):
        self.uploading = False
        self.deadline = None
        if missed:
            self.play('miss')
            self.events.publish('toast', {'kind': 'info', 'text': '没听清，可以再说一次「小葵」'})
        self.set_state('idle')

    async def abort(self, reason=None):
        self.active_trace = None
        self.reply_timing = None
        await self._cancel_camera_requests()
        if self.media_active():
            await self.media_playback(False)
            self.events.publish('media_pause', {})
        now = time.monotonic()
        phase = 'recognising' if self.vad_ended or self.awaiting_text else self.state
        # how much of this reply was actually heard: the hub keeps only that in the context
        played = max(self.reply_samples / OUT_RATE - self.audio.player.buffered(), 0)
        msg = {'type': 'abort', 'played_ms': int(played * 1000)}
        if reason:
            msg['reason'] = reason
        self.audio.player.trim(0)
        self.mute_audio = True
        self.listen_after_reply = False
        if phase == 'recognising':
            # what was just said is dropped (the hub drops it too): stop sending the rest of it
            self.uploading = False
        self.vad_ended = self.recognising = self.awaiting_text = self.hub_pending = False
        self.aborted_at = self.abort_pending = now
        log.info('abort while %s (%s)', phase, reason or 'button')
        await self.hub.send(msg)
        if self.bot_msg:
            self.bot_msg['interrupted'] = True
            self.publish_message(self.bot_msg)
            self.bot_msg = None
        elif self.question:
            # interrupted before any answer: the hub leaves the question out of the context
            self.question['interrupted'] = True
            self.publish_message(self.question)
        self.question = None
        self.set_state('listening' if self.uploading else 'idle')

    async def end(self):
        """End the conversation: stop everything and close the channel, which clears the hub's context."""
        self.silence.reset()
        await self._cancel_camera_requests()
        if self.media_active():
            await self.media_playback(False)
            self.events.publish('media_pause', {})
        if self.in_turn():
            await self.abort()
        self.uploading = False
        self.listen_after_reply = False
        self.deadline = None
        await self.hub.close()
        self.set_state('idle')

    async def say(self, text):
        """Ask in text instead of voice (the hub answers it like recognised speech)."""
        text = (text or '').strip()
        if not text:
            return
        if self.in_turn():
            await self.abort()
        if not await self.ensure_connected():
            return
        try:
            # a question sent right after connecting reaches the model before our tool list does,
            # and then "关掉睡眠模式" can't call anything; speech takes longer than this anyway
            await asyncio.wait_for(self.tools_listed.wait(), 2)
        except asyncio.TimeoutError:
            pass
        if not self.hub.connected:
            return   # ended (or dropped) while we waited: nothing will answer, stay idle
        await self.hub.send({'type': 'listen', 'state': 'detect', 'text': text})
        self.set_state('thinking')

    async def ptt_start(self):
        if self.mic_off_toast():
            return
        self.ptt_held = True
        if self.in_turn():
            await self.abort()
        if not await self.ensure_connected():
            return
        if not self.ptt_held:
            # let go while we were connecting: nothing would ever end a manual listen, so take it
            # as a tap (listen with the hub's VAD)
            await self.wake('button')
            return
        await self.start_listening('manual')
        self.heard = False
        self.deadline = None

    async def select_choice(self, card_id, option_id):
        rows = self.threads.get(self.view_entry(), [])
        message = next((row for row in rows if (row.get('choice') or {}).get('id') == card_id), None)
        if not message or not any(option['id'] == option_id for option in message['choice']['options']):
            return {'ok': False, 'error': '选项不存在，请刷新对话'}
        card = message['choice']
        if card.get('selected'):
            return {'ok': card['selected'] == option_id, 'selected': card['selected']}
        if card.get('expired') or card.get('expires', 0) < time.time():
            return {'ok': False, 'error': '选项已过期，请重新提问'}
        pending = getattr(self, 'choice_pending', None)
        if pending:
            return {'ok': False, 'error': '正在提交选择'}
        if self.in_turn() and not card_id.startswith('ha_'):
            await self.abort()
        if not await self.ensure_connected():
            return {'ok': False, 'error': '语音连接失败，请重试'}
        future = asyncio.get_running_loop().create_future()
        self.choice_pending = (card_id, future)
        try:
            sent = await self.hub.send({'type': 'listen', 'state': 'detect', 'choice': {'id': card_id, 'option_id': option_id}})
            if not sent:
                return {'ok': False, 'error': '连接已断开，请重试'}
            result = await asyncio.wait_for(future, timeout=12)
            if result.get('ok'):
                card['selected'] = result['selected']
                self.publish_message(message)
            return result
        except asyncio.TimeoutError:
            return {'ok': False, 'error': '尚未收到确认，请重试'}
        finally:
            self.choice_pending = None

    async def ptt_stop(self):
        self.ptt_held = False
        if self.uploading:
            await self.hub.send({'type': 'listen', 'state': 'stop'})
            self.uploading = False
            self.awaiting_text = True   # the hub's "nothing recognised" ends it at once
            self.set_state('thinking')
            self.deadline = time.monotonic() + 8   # back to idle if nothing comes back

    # ---- from the hub ----

    def _stale(self, msg):
        """Sent by the hub before it handled our abort (it confirms with "tts stop"): the old turn's
        question or the start of its reply, arriving late. 3 s in case the confirmation got lost."""
        if self.abort_pending is None:
            return False
        if time.monotonic() - self.abort_pending > 3:
            self.abort_pending = None
            return False
        kind, state = msg.get('type'), msg.get('state')
        if kind == 'tts' and state == 'stop' and msg.get('interrupted'):
            log.info('the hub confirmed the abort after %.0f ms', (time.monotonic() - self.abort_pending) * 1000)
            self.abort_pending = None
            return True   # our own abort: what comes next was already decided
        if kind in ('stt', 'media', 'assistant_text', 'choice_card', 'tool_status') or (kind == 'tts' and state in ('start', 'sentence_start')):
            log.info('dropped from the interrupted turn: %s %s', kind, state or msg.get('text'))
            return True
        return False

    async def _on_json(self, msg):
        kind = msg.get('type')
        if self._stale(msg):
            return
        if kind == 'tool_status':
            if msg.get('session_id') != self.hub.session_id or self.state not in ('thinking', 'speaking'):
                return
            if 'timing_id' in msg and msg['timing_id'] != getattr(self, 'active_trace', None):
                return
            labels = {'weather': '正在查询天气', 'search': '正在联网搜索', 'device': '正在联系设备', 'extension': '正在调用扩展服务'}
            status_id = msg.get('id')
            if not isinstance(status_id, str) or not 0 < len(status_id) <= 64:
                return
            if msg.get('state') == 'start' and msg.get('category') in labels:
                self.tool_status = (status_id, labels[msg['category']])
            elif msg.get('state') == 'end' and getattr(self, 'tool_status', None) and self.tool_status[0] == status_id:
                self.tool_status = None
            else:
                return
            self.events.publish('state', self.status())
        elif kind == 'stt':
            text = (msg.get('text') or '').strip()
            if text.startswith('% '):
                # not speech: the hub's note that the model calls a tool (for the screens of ESP32 boards)
                log.info('the model calls %s', text[2:])
            elif text:
                self.tool_status = None
                trace_id = msg.get('timing_id')
                received = time.monotonic()
                self.reply_timing = None
                self.active_trace = None
                if isinstance(trace_id, str) and re.fullmatch('[0-9a-f]{32}', trace_id):
                    self.active_trace = trace_id
                    end = self.speech_end if msg.get('voice_input') and self.speech_end > self.listen_since else None
                    self.reply_timing = (trace_id, received, end)
                    if end is not None:
                        log.info('turn_latency id=%s stage=speech_end_to_stt ms=%d', trace_id, round((received-end)*1000))
                if self.speech_end > self.listen_since:
                    log.info('recognised %.1f s after the user stopped talking: %s',
                             time.monotonic() - self.speech_end, text)
                else:
                    # said before this listening began (right after the wake word, in its backlog):
                    # the last time we heard talking belongs to an earlier turn
                    log.info('recognised: %s', text)
                # a long sentence the client's deadline closed the mic on is still a spoken question:
                # listen again after its reply too (not after push-to-talk, which never does)
                late = self.awaiting_text and self.listen_mode == 'realtime'
                self.heard = True
                self.awaiting_text = False
                self.recognising = self.vad_ended = False
                self.hub_pending = False
                self.deadline = None
                self.recognised += 1
                self.reply_samples = 0    # an abort before this turn's audio has heard nothing of it
                if late:
                    self.listen_after_reply = True
                if self.uploading:
                    # nothing said during the reply counts; the wake word is listened for instead
                    self.uploading = False
                    self.listen_after_reply = True
                # on the page right away: asking the hub for its models first took 0.7-2 s
                m = self.question = self.add_message('user', text)
                self.set_state('thinking')
                asyncio.create_task(self._refresh_after_question(m))
        elif kind == 'vad':
            # VAD events, a feature asked for in our hello. end: the hub decided the user finished and
            # is recognising; empty: it recognised nothing (a cough, too short, only echo)
            state = msg.get('state')
            if state == 'end':
                self.hub_pending = True
                if self.uploading and not self.recognising:
                    self.recognising = self.vad_ended = True
                    self.set_state(self.state)
            elif state == 'empty':
                self.hub_pending = self.vad_ended = False
                log.info('the hub recognised nothing')
                if self.awaiting_text:
                    # the mic is closed and this was all we were waiting for ("didn't catch that"
                    # after a wake; a follow-up window ends quietly, as when nobody talks in it)
                    self.awaiting_text = False
                    await self.stop_uploading(missed=not self.heard)
                elif self.recognising:
                    # the mic is still open: back to listening
                    self.recognising = False
                    self.set_state(self.state)
        elif kind == 'tts':
            state = msg.get('state')
            if state != 'sentence_start':
                log.info('reply %s (%.1f s queued)', state, self.audio.player.buffered())
            if state == 'stop':
                self.active_trace = None
                self.reply_timing = None
                player = self.audio.player
                log.info('reply audio: %d packets (%d dropped muted), %.2f s; buffer ran dry %d times (last sample %s); '
                         'speaker callback flags %s', self.reply_packets, self.muted_packets, self.reply_samples / OUT_RATE,
                         len(player.ran_out), ' '.join(f'{v:.3f}' for v in player.ran_out[-8:]),
                         player.late[-8:])
                levels = self.audio.take_echo_levels()
                if levels:
                    log.info('echo while it played: mic %s dBFS, cancelled %s, for the wake word %s', *levels)
            if state == 'start':
                # The hub says "start" as soon as it has the question; the model is still
                # thinking until the first sentence arrives
                self.mute_audio = False
                self.decoder = None
                self.bot_msg = None
                self.reply_samples = self.reply_packets = self.muted_packets = 0
                self.audio.player.ran_out.clear()
                self.audio.player.late.clear()
                self.deadline = None
                self.set_state('thinking')
            elif state == 'sentence_start':
                text = (msg.get('text') or '').strip()
                if self.mute_audio and self.abort_pending is None:
                    # the hub confirmed our abort, so this is something new it says on its own: a
                    # report (e.g. the interrupted Grok Bot turn finished) sends no "tts start" to unmute
                    self.mute_audio = False
                if self.uploading and self.listen_mode == 'realtime' and not self.mute_audio:
                    # the hub speaks on its own while we listen (a pending report: no stt, no
                    # "tts start" before it). Like any reply, only the wake word interrupts it,
                    # and the follow-up window opens after it
                    self.uploading = False
                    self.listen_after_reply = True
                    self.recognising = self.vad_ended = self.hub_pending = False
                    self.deadline = None
                if not self.mute_audio:
                    self.set_state('speaking')
                if text and not self.mute_audio and msg.get('display_text', True):
                    if self.bot_msg is None:
                        self.bot_msg = self.add_message('bot', text)
                        self.question = None
                    else:
                        # the hub sends sentences with their trailing punctuation stripped
                        prev = self.bot_msg['text']
                        self.bot_msg['text'] = prev + ('' if prev[-1:] in PUNCT else '，') + text
                        self.publish_message(self.bot_msg)
            elif state == 'stop':
                # the hub cut the reply off (a newer question); an older hub doesn't say so, but then
                # more than the usual tail is still queued
                if msg.get('interrupted', self.audio.player.buffered() > NORMAL_STOP_TAIL):
                    await self._cancel_camera_requests()
                    self.audio.player.trim(0)
                    if self.bot_msg:
                        self.bot_msg['interrupted'] = True
                        self.publish_message(self.bot_msg)
                elif self.question is not None and self.bot_msg is None and not self.mute_audio:
                    # over without a word: the model and the hub's spoken apology both failed to reach
                    # their servers (no network yet after a boot, an outage); say so on the page
                    log.warning('the turn ended without a reply')
                    self.events.publish('toast', {'kind': 'error', 'text': '网络还没连上，稍后再试' if not has_default_route()
                                                  else '没收到回答，可能是网络或模型服务连不上，稍后再试'})
                self.bot_msg = self.question = None
                if self.listen_after_reply:
                    asyncio.create_task(self._listen_after_reply())
                elif self.uploading:
                    self.heard = True
                    self.deadline = time.monotonic() + self.cfg['follow_up_seconds']
                    self.set_state('listening')
                else:
                    self.set_state('idle')
        elif kind == 'choice_result':
            if msg.get('session_id') != self.hub.session_id:
                return
            if msg.get('ok'):
                for row in self.threads.get(self.view_entry(), []):
                    if (row.get('choice') or {}).get('id') == msg.get('id'):
                        row['choice']['selected'] = msg.get('selected')
                        self.publish_message(row)
                        break
            pending = getattr(self, 'choice_pending', None)
            if pending and pending[0] == msg.get('id') and not pending[1].done():
                pending[1].set_result(msg)
        elif kind in ('assistant_text', 'choice_card'):
            if msg.get('version') != 1 or msg.get('session_id') != self.hub.session_id or self.mute_audio:
                return
            if kind == 'assistant_text':
                text, rid = msg.get('text'), msg.get('id')
                if not isinstance(text, str) or len(text) > 60000 or not isinstance(rid, str) or len(rid) > 120:
                    return
                key = f'{self.hub.session_id}:{rid}'
                rows = self.threads.get(self.view_entry(), [])
                message = next((row for row in rows if row.get('rich_id') == key), None)
                if message:
                    message['text'] = text
                    self.publish_message(message)
                else:
                    message = self.add_message('bot', text, rich_id=key)
                self.bot_msg = message
            else:
                from .choices import normalize
                card = normalize(msg.get('card'))
                if not card:
                    return
                if any((row.get('choice') or {}).get('id') == card['id'] for row in self.threads.get(self.view_entry(), [])):
                    return
                self.add_message('bot', '', choice=card)
            self.question = None
            self.screen.poke()
        elif kind == 'media':
            if msg.get('version') != 1 or msg.get('session_id') != self.hub.session_id or self.mute_audio:
                return
            items = media.normalize(msg.get('items'))
            media_id = msg.get('id')
            if not items or not isinstance(media_id, str) or not media_id:
                return
            key = f"{self.hub.session_id}:{media_id}"
            if any(item.get('media_id') == key for rows in self.threads.values() for item in rows):
                return
            self.add_message('bot', '', media=items, media_id=key)
            self.question = None
            self.screen.poke()
        elif kind == 'mcp':
            payload = msg.get('payload') or {}
            if (payload.get('method') == 'tools/call'
                    and (payload.get('params') or {}).get('name') == 'self.camera.take_photo'):
                if self.camera_requests or self.abort_pending or self.mute_audio:
                    await self.hub.send({'type': 'mcp', 'payload': {
                        'jsonrpc': '2.0', 'id': payload.get('id'), 'result': {'isError': True,
                        'content': [{'type': 'text', 'text': '摄像头请求已取消或正在处理中，请重新提问'}]}}})
                    return
                task = asyncio.create_task(self._camera_request(payload))
                self.camera_requests.add(task)
                task.add_done_callback(self.camera_requests.discard)
                return
            reply = await mcp.handle(payload, client=self)
            if reply:
                await self.hub.send({'type': 'mcp', 'payload': reply})
            if payload.get('method') == 'tools/list':
                self.tools_listed.set()
        elif kind == 'chat_history' and msg.get('state') == 'cleared':
            # said "清除聊天记录" and confirmed: the hub has forgotten everything
            self.threads = {}
            self.bot_msg = self.question = None
            self.events.publish('thread', {'entry': self.view_entry(), 'messages': [], 'reset': True})
        elif kind == 'server':
            log.info('server message: %s', msg)

    async def _cancel_camera_requests(self):
        tasks = tuple(getattr(self, 'camera_requests', ()))
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if hasattr(self, 'camera'):
            await self.camera.cancel()

    async def _camera_request(self, payload):
        session = self.hub.session_id
        try:
            reply = await mcp.handle(payload, client=self)
            if reply and self.hub.connected and self.hub.session_id == session:
                await self.hub.send({'type': 'mcp', 'payload': reply})
        except asyncio.CancelledError:
            pass
        except Exception:
            log.warning('camera request could not be completed')

    async def _refresh_after_question(self, m):
        """The hub may have switched models for this question (called one by name, or changed in its
        admin page): move the question, and anything said after it, to that model's thread."""
        old = m['entry']
        await self.refresh_models()
        thread = self.threads.get(old, [])
        if self.entry == old or m not in thread:
            return
        moved = thread[thread.index(m):]
        del thread[thread.index(m):]
        for x in moved:
            x['entry'] = self.entry
        self.threads.setdefault(self.entry, []).extend(moved)
        self.events.publish('thread', {'entry': self.entry, 'messages': self.threads[self.entry]})

    async def _listen_after_reply(self):
        """Reopen the mic for a follow-up once the last of the reply has been played (uploading
        earlier would send the hub its tail)."""
        give_up = time.monotonic() + 3
        while self.audio.player.buffered() > 0.02 and time.monotonic() < give_up:
            await asyncio.sleep(0.05)
        if not self.listen_after_reply or not self.hub.connected:
            return   # woken, aborted or ended meanwhile: that already decided what comes next
        self.listen_after_reply = False
        if not self.keys.mic_on:
            # switched off during the reply: nothing would be heard, don't show "listening"
            self.set_state('idle')
            return
        recognised = self.recognised
        await self.start_listening('realtime')
        if self.recognised != recognised:
            # the hub cut this reply off for a newer question that arrived while we reopened
            self.uploading, self.listen_after_reply = False, True
            self.set_state('thinking')
            return
        self.heard = True
        self.deadline = time.monotonic() + self.cfg['follow_up_seconds']

    def _on_audio(self, packet):
        if self.mute_audio:
            self.muted_packets += 1
            return
        if self.decoder is None:
            # Opus decodes to any rate whatever it was encoded at: straight to the output rate
            self.decoder = Decoder(OUT_RATE, 1)
        try:
            mono = self.decoder.decode(packet)
            self.reply_samples += len(mono)
            self.reply_packets += 1
            samples = np.repeat(mono[:, None], 2, axis=1)
            timing = getattr(self, 'reply_timing', None)
            if timing:
                self.reply_timing = None
                trace_id, received, end = timing
                loop = asyncio.get_running_loop()
                def report(stamp):
                    log.info('turn_latency id=%s stage=stt_to_output ms=%d', trace_id, round((stamp-received)*1000))
                    if end is not None:
                        log.info('turn_latency id=%s stage=speech_end_to_output ms=%d', trace_id, round((stamp-end)*1000))
                self.audio.player.push(samples, on_output=lambda stamp: loop.call_soon_threadsafe(report, stamp))
            else:
                self.audio.player.push(samples)
        except Exception:
            log.exception('cannot decode reply audio')

    async def _on_closed(self):
        self.active_trace = None
        self.reply_timing = None
        await self._cancel_camera_requests()
        if hasattr(self, 'camera'):
            self.camera.configure({})
        # the conversation is over (the hub's idle timeout, an exit word, the end button)
        self.silence.reset()
        self.play('bye')
        self.uploading = False
        self.vad_ended = False
        self.abort_pending = self.question = None
        self.listen_after_reply = False
        self.deadline = None
        self.awaiting_text = False
        self.mute_audio = False
        self.bot_msg = None
        self.entry = None
        self.set_state('idle')
        self.events.publish('thread', {'entry': self.view_entry(), 'messages': self.threads.get(self.view_entry(), [])})
        # the hub's copy has interrupted replies cut to what was heard: show that one
        asyncio.create_task(self.load_history())

    # ---- conversation threads (one per model, like the hub keeps them) ----

    def add_message(self, role, text, **extra):
        entry = self.view_entry()
        if entry != self.entry:
            self.entry = entry
            self.events.publish('thread', {'entry': entry, 'messages': self.threads.get(entry, [])})
        self._seq += 1
        m = {'id': self._seq, 'role': role, 'text': text, 'time': time.time(), 'entry': entry, **extra}
        self.threads.setdefault(entry, []).append(m)
        del self.threads[entry][:-200]
        self.publish_message(m)
        return m

    def publish_message(self, m):
        self.events.publish('message', m)

    async def load_history(self):
        """Show the recent part of the conversations the hub saved. Not while connected: this
        connection's messages are being added then (it reloads once the connection closes)."""
        if self.hub.connected:
            return
        try:
            data = await self._hub_http('/kiosk/chat/history?' + urllib.parse.urlencode(
                {'device': self.cfg['device_id'], 'limit': HISTORY_LIMIT}))
        except Exception:
            return   # the hub is down or too old to keep conversations
        if not data.get('ok') or self.hub.connected:
            return
        from .choices import normalize as normalize_choice
        self.threads = {entry: [{'id': f"h{m['id']}", 'role': m['role'], 'text': m['text'], 'time': m['time'],
                                 'entry': entry, **({'interrupted': True} if m.get('interrupted') else {}),
                                 **({'choice': normalize_choice(m['choice'])} if normalize_choice(m.get('choice')) else {}),
                                 **({'media': media.normalize(m['media'])} if m.get('media') else {})}
                                for m in items]
                        for entry, items in (data.get('models') or {}).items()}
        self.history_loaded = True
        self.events.publish('thread', {'entry': self.view_entry(), 'messages': self.threads.get(self.view_entry(), []),
                                       'reset': True})

    async def power_off(self):
        """The settings' 关机: end any conversation and halt, so the power can be unplugged without
        leaving /boot (ext2, mounted read-write) to be repaired on the next boot."""
        if self.powering_off:
            return {'ok': True}   # a second tap while the first one is still on its way
        self.powering_off = True
        self.events.publish('power', {'state': 'off'})   # cover the page before ending a conversation (up to 3 s)
        if self.hub.connected:
            await self.end()
        asyncio.create_task(self._halt())
        return {'ok': True}

    async def _halt(self):
        await asyncio.sleep(HALT_AFTER)
        log.info('halting the system (关机 in the settings)')
        try:
            p = await asyncio.create_subprocess_exec(*HALT, stdout=asyncio.subprocess.DEVNULL,
                                                     stderr=asyncio.subprocess.PIPE)
            _, err = await p.communicate()
            failed = p.returncode != 0 and (err.decode(errors='replace').strip() or f'exit {p.returncode}')
        except OSError as e:
            failed = str(e)
        if failed:
            log.error('cannot halt: %s', failed)
            self.powering_off = False
            self.events.publish('power', {'state': 'failed'})
            self.events.publish('toast', {'kind': 'error', 'text': '没能关机，请稍后再试'})

    async def clear_history(self, memory_only=False):
        """Clear every model's conversation on the hub (and the page). memory_only: just the summary
        the hub made of the older part, the messages stay."""
        try:
            result = await self._hub_http('/kiosk/chat/clear', {'device': self.cfg['device_id'],
                                                                'memory_only': bool(memory_only)})
        except Exception as e:
            log.warning('cannot clear the chat history: %s', e)
            return {'ok': False, 'error': '连不上小智网关'}
        if result.get('ok') and not memory_only:
            self.threads = {}
            self.bot_msg = self.question = None
            self.events.publish('thread', {'entry': self.view_entry(), 'messages': [], 'reset': True})
        return result

    # ---- sleep hours ----

    def _apply_sleep(self):
        self.sleeping = sleep.active(self.sleep)

    async def set_sleep(self, **changes):
        """Change the sleep setting (page or voice); raises ValueError for a bad time."""
        self.sleep = sleep.normalize(self.sleep, changes)
        config.save(sleep=self.sleep)
        log.info('sleep setting: %s', self.sleep)
        self._apply_sleep()
        self.set_state(self.state)
        await self.poll_reports()     # tell the hub now, not at the next poll
        return {**self.sleep, 'active': self.sleeping}

    # ---- screen brightness (the blanker sets the backlight: device.Screen, idle-blanker.py) ----

    def brightness(self):
        return {'level': self.screen.level, 'normal': self.normal_brightness,
                'dimmed': self.schedule_active}

    def _apply_brightness_schedule(self, now=None):
        active = brightness_schedule.active(self.brightness_schedule, now)
        level = self.brightness_schedule['level'] if active else self.normal_brightness
        changed = active != self.schedule_active or level != self.screen.level
        self.schedule_active = active
        self.screen.brightness(level)
        if changed:
            self.events.publish('state', self.status())

    async def set_brightness_schedule(self, **changes):
        setting = brightness_schedule.normalize(self.brightness_schedule, changes)
        config.save(brightness_schedule=setting)
        self.brightness_schedule = setting
        self._apply_brightness_schedule()
        self.events.publish('state', self.status())
        return {**setting, 'active': self.schedule_active}

    async def set_brightness(self, level=None, change=None):
        """Set the normal brightness restored outside the scheduled interval."""
        if level is None:
            if change is None:
                raise ValueError('level or change is needed')
            level = self.normal_brightness + int(change)
        level = max(0, min(100, int(level)))
        config.save(brightness=level)
        self.normal_brightness = level
        self._apply_brightness_schedule()
        log.info('brightness: %s', self.screen.level)
        self.set_state(self.state)
        return self.brightness()

    # ---- auto screen-off (the blanker blanks: device.Screen, idle-blanker.py) ----

    def _screen_off_seconds(self):
        return self.screen_off['minutes'] * 60 if self.screen_off['enabled'] else 0

    async def set_screen_off(self, **changes):
        """Page: enabled and/or minutes (one of device.SCREEN_OFF_MINUTES); raises ValueError."""
        self.screen_off = normalize_screen_off(self.screen_off, changes)
        config.save(screen_off=self.screen_off)
        self.screen.blank_after(self._screen_off_seconds())
        log.info('screen-off: %s', self.screen_off)
        self.set_state(self.state)
        return self.screen_off

    # ---- reports (the hub's local-only /kiosk/reports endpoint) ----

    async def poll_reports(self, play=False):
        try:
            data = await self._hub_http('/kiosk/reports', {'device': self.cfg['device_id'],
                                                           'quiet': self.sleeping, 'play': play})
        except Exception:
            return None
        self.hub_seen = time.monotonic()
        count = int(data.get('count') or 0)
        new = count > self.reports
        if count != self.reports:
            self.reports = count
            self.set_state(self.state)
        if new:
            self._on_new_report()
        return data

    def _on_new_report(self):
        if self.sleeping:
            # just light the screen with the notice; it blanks again after the user's screen-off
            self.screen.poke()
        elif self.state == 'idle' and not self.hub.connected:
            # connected and idle, the hub reads it by itself
            self.play('report')
            asyncio.create_task(self.play_reports())

    async def play_reports(self):
        """Have the hub read the pending reports now (also in the sleep hours: the user asked)."""
        self.screen.poke()
        if not await self.ensure_connected():
            return
        if self.state == 'connecting':
            self.set_state('idle')
        # a fresh connection gets them from the hub itself after its greeting delay (~6 s)
        await self.poll_reports(play=True)

    async def _report_poller(self):
        while True:
            if sleep.active(self.sleep) != self.sleeping:
                self._apply_sleep()
                log.info('sleep hours %s', 'begin' if self.sleeping else 'end')
                self.set_state(self.state)
            await self.poll_reports()
            await asyncio.sleep(REPORT_POLL)

    # ---- volume ----

    async def device_volume(self):
        volume = await mcp.get_volume()
        if volume is not None:
            self.volume = volume
        return volume

    async def set_volume(self, volume, source='page'):
        async with self.volume_lock:
            volume = await self._apply_volume(volume)
        self.events.publish('volume', {'volume': volume, 'source': source})
        return volume

    async def adjust_volume(self, volume=None, change=None):
        if (volume is None) == (change is None):
            raise ValueError('请提供一个音量值或一个调整量')
        value = volume if volume is not None else change
        if type(value) is not int or not (-100 if change is not None else 0) <= value <= 100:
            raise ValueError('音量须为 0 至 100，调整量须为 -100 至 100 的整数')
        async with self.volume_lock:
            if change is not None:
                current = await mcp.get_volume()
                if current is None:
                    raise ValueError('无法读取当前音量')
                volume = current + change
            volume = await self._apply_volume(volume)
        self.events.publish('volume', {'volume': volume, 'source': 'voice'})
        return volume

    async def _apply_volume(self, volume):
        """set_volume under volume_lock."""
        volume = max(0, min(100, int(volume)))
        await mcp.set_volume(volume)
        self.volume = volume
        return volume

    async def volume_step(self, delta):
        async with self.volume_lock:   # read and write as one step, or two quick presses make one
            current = await mcp.get_volume()
            if current is None:
                return
            volume = await self._apply_volume(current + delta)
        self.events.publish('volume', {'volume': volume, 'source': 'key'})

    # ---- Bluetooth speaker (bt.py) ----

    def background_target(self):
        if self.state != 'idle' or self.audio.player.buffered() > 0:
            return BACKGROUND_DUCKED
        return 1.0

    async def _bluetooth_loop(self):
        while True:
            await asyncio.sleep(0.1)
            self.audio.background.target = self.background_target()

    async def bluetooth(self, enabled=None, window=None, remove=None, disconnect=None):
        """The page's Bluetooth controls: the switch (stored), the "add a phone" window, forgetting a phone."""
        ok = True
        if enabled is not None:
            ok = await self.bt.set_enabled(bool(enabled))
            config.save(bluetooth=bool(enabled))
        if window is not None:
            ok = await self.bt.open_window(bool(window))
        if remove:
            ok = await self.bt.remove(remove)
        if disconnect:
            ok = await self.bt.disconnect(disconnect)
        return ok

    # ---- models (the hub's local-only /kiosk/llm endpoints) ----

    async def _hub_http(self, path, body=None):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            if body is None:
                async with s.get(self.cfg['hub_http'] + path) as r:
                    r.raise_for_status()
                    return await r.json(content_type=None)
            async with s.post(self.cfg['hub_http'] + path, json=body) as r:
                r.raise_for_status()
                return await r.json(content_type=None)

    async def refresh_models(self):
        """Ask the hub for its models; False when it didn't answer (down, or still starting)."""
        try:
            data = await self._hub_http('/kiosk/llm?' + urllib.parse.urlencode({'device': self.cfg['device_id']}))
            if not isinstance(data, dict) or not isinstance(data.get('options'), list):
                return False
        except Exception:
            return False
        self.hub_seen = time.monotonic()
        self._apply_models(data)
        return True

    def _apply_models(self, data):
        options = [describe(o) for o in data.get('options', [])]
        models = {'current': next((o for o in options if o['entry'] == data.get('selected')), None),
                  'options': options}
        if models != self.models:
            self.models = models
            self.events.publish('model', models)
            if self.hub.connected and self.entry and models['current'] and models['current']['entry'] != self.entry:
                self.entry = models['current']['entry']
                self.events.publish('thread', {'entry': self.entry, 'messages': self.threads.get(self.entry, [])})
            elif not self.entry:
                # also while connected with nothing said yet (e.g. just after an interrupt reconnected)
                self.events.publish('thread', {'entry': self.view_entry(), 'messages': self.threads.get(self.view_entry(), [])})

    async def set_model(self, kind, vendor, entry=None):
        # entry names the model on the hub; a Grok Bot is only identified by it (its vendor is shared)
        if self.in_turn():
            # the answer in flight belongs to the old model: stop it rather than file it under the new one
            await self.abort()
        try:
            result = await self._hub_http('/kiosk/llm/use', {'kind': kind, 'vendor': vendor, 'entry': entry,
                                                             'device': self.cfg['device_id']})
        except Exception as e:
            result = {'ok': False, 'error': str(e)}
        if result.get('ok') and 'options' in result:
            self._apply_models(result)   # the hub answers with the new list (an older hub doesn't)
        else:
            await self.refresh_models()
        return {k: v for k, v in result.items() if k not in ('options', 'selected', 'default', 'kind', 'vendor')}

    async def _model_poller(self):
        # often while the hub is down (after a boot the page would show no models for over a minute),
        # then only to notice changes made in the hub's admin page
        while True:
            up = await self.refresh_models()
            if up and not self.history_loaded:
                await self.load_history()   # at start, or once the hub is up
            await asyncio.sleep(MODEL_POLL if up else MODEL_RETRY)

    # ---- timers ----

    def _silent_expired(self, now):
        waiting = (self.state in ('idle', 'listening') and not self.ptt_held
                   and not self.awaiting_text and not self.recognising and not self.hub_pending
                   and not self.listen_after_reply and not self.abort_pending
                   and self.audio.player.buffered() <= 0.02 and not self.media_active())
        return self.silence.observe(now, connected=self.hub.connected, waiting=waiting,
                                    speech=self.uploading and self.audio.mic_level > SPEECH_RMS)

    async def _end_if_silent(self, now):
        if not self._silent_expired(now):
            return False
        # Serialize with a fresh wake/reconnect, then recheck in case state changed while waiting.
        async with self.connecting:
            if not self._silent_expired(time.monotonic()):
                return False
            log.info('conversation idle for %.0f seconds: closing hub connection', self.silence.seconds)
            await self.end()
            return True

    async def _ticker(self):
        last_level = 0.0
        last_brightness = 0.0
        while True:
            await asyncio.sleep(0.1)
            now = time.monotonic()
            if now - last_brightness >= 1:
                self._apply_brightness_schedule()
                last_brightness = now
            if getattr(self, 'media_until', 0.0) and not self.media_active():
                await self.media_playback(False)
                self.events.publish('media_pause', {})
            stalled = self.audio.stalled()
            if stalled:
                # PipeWire restarted (or died): the streams won't come back by themselves. systemd
                # restarts the service, which waits for PipeWire's socket and opens them again.
                log.error('audio is dead (%s): exiting to be restarted', stalled)
                os._exit(1)
            if self.uploading and self.audio.mic_level > SPEECH_RMS:
                self.speech_end = now
                if self.recognising:   # talking again: the hub listens on
                    self.recognising = False
                    self.set_state(self.state)
                if self.deadline:   # still talking
                    if self.listen_mode == 'realtime':
                        self.deadline = max(self.deadline, now + self.cfg['follow_up_seconds'])
                    else:
                        self.deadline = min(max(self.deadline, now + 2.5), self.listen_since + MAX_LISTEN)
            if await self._end_if_silent(now):
                continue
            if self.uploading and self.listen_mode == 'manual' and now > self.listen_since + MAX_LISTEN:
                # push-to-talk held (or a release lost) for too long: the hub buffers all of it
                log.info('push-to-talk open for %d s: stopping it', MAX_LISTEN)
                await self.ptt_stop()
            if self.deadline and now > self.deadline:
                if self.state == 'listening' and self.uploading:
                    # a hub that sends VAD events has answered everything it heard unless one is pending.
                    # In a follow-up window (heard) only that counts: an older hub would make any noise
                    # after a reply wait 15 s
                    waiting = self.hub_pending or (not self.heard and not self.hub.features.get('vad_end'))
                    if self.speech_end > self.listen_since and waiting:
                        # they did talk; a long sentence takes the hub a while to recognise, so
                        # close the mic but wait for its text before saying "didn't catch that"
                        # (or, in a follow-up window, before going idle: its answer would be lost)
                        self.uploading = False
                        self.awaiting_text = True
                        self.deadline = now + RECOGNITION_WAIT
                        self.set_state('thinking')
                    else:
                        await self.stop_uploading(missed=not self.heard)
                elif self.state == 'thinking' and not self.uploading:
                    missed, self.awaiting_text = self.awaiting_text and not self.heard, False
                    await self.stop_uploading(missed=missed)
            if self.state in ('listening', 'speaking') and now - last_level >= 0.1:
                last_level = now
                self.events.publish('level', {'mic': round(self.audio.mic_level, 4),
                                              'out': round(self.audio.player.level, 4)})


def dumps(data):
    return json.dumps(data, ensure_ascii=False)
