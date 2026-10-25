"""Tests for local and cloud BLID/password retrieval."""

from __future__ import annotations

import json
import socket
import ssl
import threading
from typing import Any

import pytest

from custom_components.irobot_local import auth
from custom_components.irobot_local.auth import (
    CloudAuthError,
    CloudError,
    LocalPasswordError,
    get_credentials_cloud,
    get_password_local,
    get_robot_info,
    parse_password_response,
)

# Real sockets: a fake robot runs on localhost.
pytestmark = pytest.mark.usefixtures("socket_enabled")

PASSWORD = ":1:1600000000:testpassword"
HEADER = bytes.fromhex("efcc3b2900")


def _response(password: bytes) -> bytes:
    body = HEADER + password
    return bytes([0xF0, len(body)]) + body


def test_parse_password_response() -> None:
    assert parse_password_response(_response(PASSWORD.encode())) == PASSWORD
    assert parse_password_response(_response(PASSWORD.encode() + b"\x00\x00")) == PASSWORD
    with pytest.raises(LocalPasswordError, match="pairing mode"):
        parse_password_response(bytes.fromhex("f005efcc3b2903"))


def _tls_server(cert_files, reply: bytes, split: bool = False) -> tuple[socket.socket, int]:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*cert_files)
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers("AES128-SHA256:@SECLEVEL=0")
    sock = socket.create_server(("127.0.0.1", 0))

    def serve() -> None:
        raw, _ = sock.accept()
        with context.wrap_socket(raw, server_side=True) as conn:
            assert conn.recv(7) == bytes.fromhex("f005efcc3b2900")
            if split:  # some robots send the 2-byte header separately
                conn.sendall(reply[:2])
                conn.sendall(reply[2:])
            else:
                conn.sendall(reply)

    threading.Thread(target=serve, daemon=True).start()
    return sock, sock.getsockname()[1]


@pytest.mark.parametrize("split", [False, True])
def test_get_password_local(cert_files, split: bool) -> None:
    sock, port = _tls_server(cert_files, _response(PASSWORD.encode()), split)
    with sock:
        assert get_password_local("127.0.0.1", port=port, timeout=5) == PASSWORD


def test_get_password_local_not_in_pairing_mode(cert_files) -> None:
    sock, port = _tls_server(cert_files, bytes.fromhex("f005efcc3b2903"))
    with sock, pytest.raises(LocalPasswordError):
        get_password_local("127.0.0.1", port=port, timeout=5)


def test_get_password_local_unreachable() -> None:
    with socket.create_server(("127.0.0.1", 0)) as sock:
        port = sock.getsockname()[1]
    with pytest.raises(LocalPasswordError):
        get_password_local("127.0.0.1", port=port, timeout=2)


def test_get_robot_info() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]

    def serve() -> None:
        data, addr = sock.recvfrom(64)
        assert data == b"irobotmcs"
        sock.sendto(b"irobotmcs", addr)  # echo noise is skipped
        sock.sendto(
            json.dumps({"hostname": "iRobot-ABCDEF0123456789", "ip": "127.0.0.1", "sku": "i755840"}).encode(),
            addr,
        )

    threading.Thread(target=serve, daemon=True).start()
    with sock:
        info = get_robot_info("127.0.0.1", port=port, timeout=5)
    assert info["blid"] == "ABCDEF0123456789"
    assert info["sku"] == "i755840"


def test_get_robot_info_timeout() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        with pytest.raises(LocalPasswordError):
            get_robot_info("127.0.0.1", port=port, timeout=0.5)


class _Response:
    def __init__(self, body: dict[str, Any], status: int = 200) -> None:
        self._body = body
        self.status = status

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise auth.aiohttp.ClientResponseError(None, (), status=self.status)

    async def json(self, content_type: str | None = None) -> dict[str, Any]:
        return self._body


class _Session:
    """Fake aiohttp session answering the three cloud calls."""

    def __init__(self, login: dict[str, Any], account: dict[str, Any]) -> None:
        self.login = login
        self.account = account
        self.calls: list[tuple[str, str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append(("GET", url, kwargs.get("params")))
        return _Response(
            {
                "gigya": {"api_key": "KEY", "datacenter_domain": "eu1.gigya.com"},
                "deployments": {
                    "v001": {"httpBase": "https://old.example"},
                    "v005": {"httpBase": "https://new.example"},
                },
            }
        )

    def post(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append(("POST", url, kwargs.get("data") or kwargs.get("json")))
        return _Response(self.login if "gigya.com" in url else self.account)


GOOD_LOGIN = {
    "statusCode": 200,
    "errorCode": 0,
    "UID": "uid",
    "UIDSignature": "sig",
    "signatureTimestamp": "123",
}


async def test_cloud_lists_robots() -> None:
    session = _Session(
        GOOD_LOGIN,
        {
            "robots": {
                "BLID1": {"password": PASSWORD, "name": "Roomba", "sku": "i755840"},
                "BLID2": {"name": "No password"},
            }
        },
    )
    robots = await get_credentials_cloud(session, "a@b.pl", "secret", "PL")
    assert [(r.blid, r.password, r.name) for r in robots] == [("BLID1", PASSWORD, "Roomba")]
    assert session.calls[0][2] == {"country_code": "PL"}
    assert session.calls[1][1] == "https://accounts.eu1.gigya.com/accounts.login"
    assert session.calls[1][2]["apiKey"] == "KEY"
    assert session.calls[2][1] == "https://new.example/v2/login"
    assert session.calls[2][2]["gigya"] == {"signature": "sig", "timestamp": "123", "uid": "uid"}


async def test_cloud_invalid_login() -> None:
    session = _Session({"statusCode": 403, "errorCode": 403042, "errorDetails": "invalid loginID or password"}, {})
    with pytest.raises(CloudAuthError):
        await get_credentials_cloud(session, "a@b.pl", "bad", "PL")


async def test_cloud_unexpected_account_answer() -> None:
    session = _Session(GOOD_LOGIN, {"error": "nope"})
    with pytest.raises(CloudError):
        await get_credentials_cloud(session, "a@b.pl", "secret", "PL")
