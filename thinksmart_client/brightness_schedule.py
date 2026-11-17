from . import sleep


DEFAULTS = {'enabled': False, 'start': '00:00', 'end': '06:00', 'level': 30}


def normalize(setting=None, changes=None):
    result = dict(DEFAULTS)
    for values in (setting, changes):
        if values is None:
            continue
        if not isinstance(values, dict) or set(values) - set(DEFAULTS):
            raise ValueError('定时亮度设置格式不正确')
        result.update(values)
    if not isinstance(result['enabled'], bool):
        raise ValueError('启用状态必须为开或关')
    for key in ('start', 'end'):
        value = result[key]
        if not isinstance(value, str) or not sleep.TIME.fullmatch(value):
            raise ValueError('时间须使用 24 小时制 HH:MM')
    if result['start'] == result['end']:
        raise ValueError('开始和结束时间须不同')
    if type(result['level']) is not int or not 0 <= result['level'] <= 100:
        raise ValueError('亮度须为 0 至 100 的整数')
    return result


def active(setting, now=None):
    return sleep.active(setting, now)
