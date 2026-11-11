"""The Hub address form: what normalize() accepts, what it fills in, and what it refuses.

Run like test_barge_in.py (same stubs).
"""
import pytest

from thinksmart_client import server_settings


def test_stock_pair_ports_are_kept_apart():
    # the stock Hub serves HTTP on 16688 and the voice WebSocket on 16689
    assert server_settings.normalize('http://hub.example.com:16688') == {
        'hub_http': 'http://hub.example.com:16688',
        'hub_ws': 'ws://hub.example.com:16689/xiaozhi/v1/'}


def test_https_with_a_path_and_a_trailing_slash():
    assert server_settings.normalize('https://hub.example.com/xz/') == {
        'hub_http': 'https://hub.example.com/xz',
        'hub_ws': 'wss://hub.example.com/xz/xiaozhi/v1/'}


def test_an_explicit_voice_address_wins():
    assert server_settings.normalize('http://hub.example.com', 'ws://hub.example.com:9000/v1')['hub_ws'] \
        == 'ws://hub.example.com:9000/v1'
    assert server_settings.normalize('http://hub.example.com', 'ws://hub.example.com:9000')['hub_ws'] \
        == 'ws://hub.example.com:9000/'


@pytest.mark.parametrize('http, ws', [
    ('', ''),                                       # empty
    ('hub.example.com:16688', ''),                  # no scheme
    ('ftp://hub.example.com', ''),                  # wrong scheme
    ('http://user:pw@hub.example.com', ''),         # credentials in the URL
    ('http://hub.example.com?q=1', ''),             # query
    ('http://hub.example.com:0', ''),               # port 0
    ('http://hub.example.com/a b', ''),             # whitespace
    ('http://hub.example.com', 'http://hub.example.com'),   # voice must be ws/wss
])
def test_refused(http, ws):
    with pytest.raises(ValueError):
        server_settings.normalize(http, ws)
