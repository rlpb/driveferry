"""The local server is reachable by anything running on this machine, and by
any web page the user visits. These tests are the fence."""

import json
import urllib.error
import urllib.request

import pytest
from conftest import FakeDaemon

from driveferry.server import Api, DriveFerryServer


@pytest.fixture()
def server():
    instance = DriveFerryServer(Api(FakeDaemon()), port=0)
    instance.serve_in_background()
    yield instance
    instance.shutdown()
    instance.server_close()


def request(server, path, method="POST", token=None, host=None, origin=None, body=b"{}"):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-DriveFerry-Token"] = token
    if origin is not None:
        headers["Origin"] = origin
    url = server.url.rstrip("/") + path
    req = urllib.request.Request(url, data=body if method == "POST" else None, method=method, headers=headers)
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_api_without_a_token_is_refused(server):
    status, _ = request(server, "/api/state")
    assert status == 401


def test_api_with_a_wrong_token_is_refused(server):
    status, _ = request(server, "/api/state", token="not-the-token")
    assert status == 401


def test_api_with_the_session_token_works(server):
    status, body = request(server, "/api/state", token=server.token)
    assert status == 200
    assert json.loads(body)["rclone_version"] == "v1.75.1"


def test_a_foreign_origin_is_refused_even_with_the_token(server):
    status, _ = request(server, "/api/state", token=server.token, origin="https://evil.example")
    assert status == 403


def test_our_own_origin_is_accepted(server):
    status, _ = request(
        server, "/api/state", token=server.token, origin="http://127.0.0.1:{}".format(server.server_port)
    )
    assert status == 200


def test_a_non_loopback_host_header_is_refused(server):
    # This is the DNS-rebinding case: a hostile domain resolved to 127.0.0.1.
    status, _ = request(server, "/api/state", token=server.token, host="evil.example")
    assert status == 403


def test_unknown_operations_are_not_dispatched(server):
    status, body = request(server, "/api/op_state", token=server.token)
    assert status == 404
    status, body = request(server, "/api/remotes", token=server.token)
    assert status == 404
    assert b"unknown operation" in body


def test_an_oversized_body_is_refused(server):
    payload = b'{"x":"' + b"a" * (2 << 20) + b'"}'
    status, _ = request(server, "/api/state", token=server.token, body=payload)
    assert status == 413


def test_invalid_json_is_refused(server):
    status, _ = request(server, "/api/state", token=server.token, body=b"{not json")
    assert status == 400


def test_a_json_array_body_is_refused(server):
    status, _ = request(server, "/api/state", token=server.token, body=b"[1,2,3]")
    assert status == 400


def test_the_page_is_served_with_the_token_substituted(server):
    status, body = request(server, "/", method="GET")
    assert status == 200
    assert b"__DRIVEFERRY_TOKEN__" not in body
    assert server.token.encode() in body


def test_unknown_static_paths_404_rather_than_reading_the_disk(server):
    for path in ["/../server.py", "/app.py", "/web/app.js", "/etc/passwd"]:
        status, _ = request(server, path, method="GET")
        assert status == 404, path


def test_responses_carry_the_hardening_headers(server):
    url = server.url
    with urllib.request.urlopen(url, timeout=10) as response:
        headers = dict(response.headers)
    assert "'none'" in headers["Content-Security-Policy"]
    assert "style-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "Access-Control-Allow-Origin" not in headers
