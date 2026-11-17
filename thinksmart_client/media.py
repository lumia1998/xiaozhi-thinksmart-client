from urllib.parse import urlsplit


def normalize(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 8:
        return []
    result = []
    for item in items:
        if not isinstance(item, dict) or item.get('kind') not in ('image', 'video', 'audio'):
            return []
        url, title = item.get('url'), item.get('title', '')
        if not isinstance(url, str) or len(url) > 4096 or '\\' in url or any(ord(char) <= 32 for char in url):
            return []
        try:
            parsed = urlsplit(url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                return []
        except ValueError:
            return []
        if not isinstance(title, str) or len(title) > 200:
            return []
        result.append({'kind': item['kind'], 'url': url, 'title': title.strip()})
    return result
