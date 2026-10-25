"""Get a robot's BLID and password, locally or from the iRobot cloud.

Local: UDP 5678 "irobotmcs" returns public info (BLID in the hostname), and
while the robot is in pairing mode (HOME held ~2 s until it beeps) a magic
packet on TLS 8883 returns the password.

Cloud: iRobot account login (Gigya, then iRobot /v2/login) lists every robot
of the account with its password. Only the robot password is kept, never the
account credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import socket
import ssl
from typing import Any

import aiohttp

from .client import CIPHER_PROFILES, DEFAULT_PORT, ssl_context

DISCOVERY_PORT = 5678
PASSWORD_MAGIC = bytes.fromhex("f005efcc3b2900")
LOCAL_TIMEOUT = 5

DISCOVERY_URL = "https://disc-prod.iot.irobotapi.com/v1/discover/endpoints"
APP_ID = "ANDROID-C7FB240E-DF34-42D7-AE4E-A8C17079A294"
CLOUD_TIMEOUT = aiohttp.ClientTimeout(total=20)


class AuthError(Exception):
    """Base error."""


class LocalPasswordError(AuthError):
    """Robot unreachable or not in pairing mode."""


class CloudAuthError(AuthError):
    """iRobot account rejected the email or password."""


class CloudError(AuthError):
    """iRobot cloud unreachable or returned something unexpected."""


@dataclass(frozen=True)
class RobotCredentials:
    blid: str
    password: str
    name: str | None = None
    sku: str | None = None


def get_robot_info(
    host: str, port: int = DISCOVERY_PORT, timeout: float = LOCAL_TIMEOUT
) -> dict[str, Any]:
    """Ask the robot for its public info over UDP. Blocking."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.sendto(b"irobotmcs", (host, port))
            while True:
                data, _ = sock.recvfrom(4096)
                try:
                    info = json.loads(data)
                except ValueError:
                    continue  # our own broadcast echo or noise
                hostname = str(info.get("hostname", ""))
                prefix, _, blid = hostname.partition("-")
                if prefix in ("Roomba", "iRobot") and blid:
                    info["blid"] = blid
                    return info
        except OSError as err:
            raise LocalPasswordError(f"No discovery answer from {host}: {err}") from err


def get_password_local(
    host: str, port: int = DEFAULT_PORT, timeout: float = LOCAL_TIMEOUT
) -> str:
    """Read the password from a robot in pairing mode. Blocking."""
    last_error: Exception | None = None
    for ciphers in CIPHER_PROFILES:
        try:
            return _read_password(host, port, ciphers, timeout)
        except ssl.SSLError as err:
            last_error = err
    raise LocalPasswordError(f"TLS handshake failed: {last_error}")


def parse_password_response(data: bytes) -> str:
    """Response is f0 <len> ef cc 3b 29 <status> <password> [\\0 padding]."""
    if len(data) <= len(PASSWORD_MAGIC):
        raise LocalPasswordError(
            "Robot is not in pairing mode: hold HOME until it beeps, then retry"
        )
    password = data[len(PASSWORD_MAGIC) :].rstrip(b"\x00").decode("utf-8", "replace")
    if not password:
        raise LocalPasswordError("Robot returned an empty password")
    return password


def _read_password(host: str, port: int, ciphers: str, timeout: float) -> str:
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with ssl_context(ciphers).wrap_socket(raw) as conn:
                conn.sendall(PASSWORD_MAGIC)
                data = b""
                while len(data) < 2 or len(data) < data[1] + 2:
                    chunk = conn.recv(1024)
                    if not chunk:
                        break
                    data += chunk
    except ssl.SSLError:
        raise
    except OSError as err:
        raise LocalPasswordError(f"Cannot reach {host}:{port}: {err}") from err
    return parse_password_response(data)


async def get_credentials_cloud(
    session: aiohttp.ClientSession, email: str, password: str, country_code: str
) -> list[RobotCredentials]:
    """Log in to the iRobot account and list its robots."""
    try:
        async with session.get(
            DISCOVERY_URL, params={"country_code": country_code}, timeout=CLOUD_TIMEOUT
        ) as resp:
            resp.raise_for_status()
            endpoints = await resp.json(content_type=None)

        gigya = endpoints.get("gigya") or {}
        api_key = gigya.get("api_key")
        if not api_key:
            raise CloudError("No Gigya API key in the discovery response")
        gigya_base = f"https://accounts.{gigya.get('datacenter_domain') or 'us1.gigya.com'}"
        http_base = _pick_http_base(endpoints)

        async with session.post(
            f"{gigya_base}/accounts.login",
            data={
                "apiKey": api_key,
                "targetenv": "mobile",
                "loginID": email,
                "password": password,
                "format": "json",
                "targetEnv": "mobile",
            },
            timeout=CLOUD_TIMEOUT,
        ) as resp:
            login = await resp.json(content_type=None)

        if login.get("errorCode") in (403042, 403047) or login.get("statusCode") in (401, 403):
            raise CloudAuthError(login.get("errorDetails") or "Invalid email or password")
        if login.get("errorCode") != 0 or not login.get("UID"):
            raise CloudError(f"Gigya login failed: {login.get('errorMessage')}")

        async with session.post(
            f"{http_base}/v2/login",
            json={
                "app_id": APP_ID,
                "assume_robot_ownership": 0,
                "gigya": {
                    "signature": login["UIDSignature"],
                    "timestamp": login["signatureTimestamp"],
                    "uid": login["UID"],
                },
            },
            timeout=CLOUD_TIMEOUT,
        ) as resp:
            account = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError, KeyError) as err:
        raise CloudError(f"{type(err).__name__}: {err}") from err

    robots = account.get("robots")
    if not isinstance(robots, dict):
        raise CloudError("No robots in the iRobot account response")
    return [
        RobotCredentials(
            blid=blid,
            password=robot["password"],
            name=robot.get("name"),
            sku=robot.get("sku"),
        )
        for blid, robot in robots.items()
        if isinstance(robot, dict) and robot.get("password")
    ]


def _pick_http_base(endpoints: dict[str, Any]) -> str:
    deployments = endpoints.get("deployments") or {}
    for key in sorted(deployments, reverse=True):
        base = (deployments[key] or {}).get("httpBase")
        if base:
            return base
    return endpoints.get("httpBase") or "https://unauth2.prod.iot.irobotapi.com"
