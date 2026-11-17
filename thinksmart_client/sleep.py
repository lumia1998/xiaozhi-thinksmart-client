"""Quiet hours for automatic reports, stored in the user configuration."""
import datetime
import re

TIME = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')


def normalize(setting, changes=None):
    """A complete, valid setting: {'enabled': bool, 'start': 'HH:MM', 'end': 'HH:MM'}.
    Raises ValueError for a time that isn't HH:MM."""
    s = {'enabled': False, 'start': '22:00', 'end': '07:00'}
    s.update({k: v for k, v in (setting or {}).items() if k in s})
    s.update({k: v for k, v in (changes or {}).items() if k in s and v is not None})
    s['enabled'] = bool(s['enabled'])
    for key in ('start', 'end'):
        value = str(s[key]).strip()
        if re.match(r'^\d:', value):
            value = '0' + value
        if not TIME.match(value):
            raise ValueError(f'{key} must be HH:MM, got {s[key]!r}')
        s[key] = value
    return s


def active(setting, now=None):
    """Whether now falls in the sleep hours; the range may cross midnight (22:00-07:00)."""
    if not setting['enabled'] or setting['start'] == setting['end']:
        return False
    t = (now or datetime.datetime.now()).strftime('%H:%M')
    start, end = setting['start'], setting['end']
    return start <= t < end if start < end else (t >= start or t < end)
