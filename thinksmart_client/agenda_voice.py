import re
import threading
import time
import unicodedata
import uuid

import numpy as np


class AgendaRecorder:
    def __init__(self):
        self.lock = threading.Lock()
        self.token = None
        self.buffer = bytearray()
        self.started = 0

    def start(self):
        with self.lock:
            if self.token and time.monotonic() - self.started < 12:
                raise ValueError('已有日程正在录音')
            self.token = uuid.uuid4().hex
            self.started = time.monotonic()
            self.buffer.clear()
            return self.token

    def feed(self, samples):
        with self.lock:
            if not self.token: return False
            if time.monotonic() - self.started >= 12:
                self.token = None
                self.buffer.clear()
                return False
            remaining = (320000-len(self.buffer)) // 2
            if remaining:
                self.buffer.extend(np.clip(samples[:remaining]*32768, -32768, 32767).astype('<i2').tobytes())
            return True

    def finish(self, token, cancel=False):
        with self.lock:
            if not self.token or token != self.token: raise ValueError('录音已结束，请重新按住说话')
            self.token = None
            pcm = bytes(self.buffer) if not cancel and time.monotonic()-self.started < 12 else b''
            self.buffer.clear()
            if not cancel and len(pcm) < 6400: raise ValueError('录音太短或已过期，请重新按住说话')
            return pcm

    def reset(self):
        with self.lock:
            self.token = None
            self.buffer.clear()


def number(text):
    if text.isdecimal(): return int(text)
    digits = '零一二三四五六七八九'
    text = text.replace('两','二').replace('〇','零')
    if '十' not in text: return int(''.join(str(digits.index(char)) for char in text))
    tens, units = text.split('十')
    return (digits.index(tens) if tens else 1)*10 + (digits.index(units) if units else 0)


NUM = r'[0-9零〇一二两三四五六七八九十]{1,3}'
CLOCK = re.compile(rf'^(?:请(?:帮我)?)?(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上)?\s*({NUM})(?:[:：](\d{{2}})|[点时](?:(半|一刻|三刻)|({NUM})分?|钟)?)\s*[,，、]?\s*(.+)$')


def draft(text):
    original = unicodedata.normalize('NFKC', text).strip().rstrip('。！？!?').strip()
    result = {'title':original, 'time':''}
    match = CLOCK.fullmatch(original)
    if not match: return result
    period, hours, digital, fraction, spoken, title = match.groups()
    try:
        hour = number(hours)
        minute = int(digital) if digital else {'半':30,'一刻':15,'三刻':45}[fraction] if fraction else number(spoken) if spoken else 0
    except (ValueError, KeyError): return result
    if not (0 <= hour < 24 and 0 <= minute < 60) or not title.strip(): return result
    if period in ('下午','傍晚','晚上') and 1 <= hour <= 11: hour += 12
    if period == '中午' and 1 <= hour <= 3: hour += 12
    if period in ('凌晨','晚上') and hour == 12: hour = 0
    return {'time':f'{hour:02d}:{minute:02d}','title':title.strip()}
