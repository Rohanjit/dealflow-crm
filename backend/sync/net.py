"""Talking to mail and calendar servers people type in themselves (IMAP, CalDAV).

Those addresses come from users, so before connecting Dealflow checks that the name resolves only to
public internet addresses and then connects to exactly the address it checked (so a name can't be
switched to an internal one in between). Private, loopback and link-local addresses are refused
unless the install allows them (`DEALFLOW_SYNC_ALLOW_PRIVATE=1`, for a firm's own mail server).
Certificates are always verified.

Tests swap `HTTP` and `IMAP` for stand-ins.
"""

from __future__ import annotations

import base64
import imaplib
import ipaddress
import os
import socket
import ssl
import urllib.parse
from http.client import HTTPSConnection

from mail.providers import MailError


def allow_private() -> bool:
    return os.environ.get("DEALFLOW_SYNC_ALLOW_PRIVATE", "0") == "1"


def resolve(host: str, port: int) -> str:
    """The address to connect to, after checking every address the name has."""
    host = (host or "").strip().rstrip(".").lower()
    if not host or len(host) > 253 or any(c in host for c in "/@?#\\ "):
        raise MailError("That server address doesn't look right.", "setup")
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise MailError(f"Couldn't find the server {host}. Check its address.", "setup")
    addrs = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not allow_private() and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                                    or ip.is_multicast or ip.is_unspecified):
            raise MailError(f"{host} points to a private network address, which Dealflow won't connect to.",
                            "setup")
        addrs.append(str(ip))
    if not addrs:
        raise MailError(f"Couldn't find the server {host}. Check its address.", "setup")
    return addrs[0]


def _context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


# ---------------------------------------------------------------- HTTPS (CalDAV)


class _PinnedHTTPS(HTTPSConnection):
    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout, context=_context())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _https(method: str, url: str, headers: dict, body: bytes | None):
    """One request, no redirects followed (the caller decides). Returns (status, headers, body, url)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise MailError("Calendar addresses must start with https://.", "setup")
    port = parts.port or 443
    ip = resolve(parts.hostname, port)
    conn = _PinnedHTTPS(parts.hostname, port, ip, timeout=60)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    try:
        conn.request(method, path, body=body, headers=headers)
        r = conn.getresponse()
        data = r.read(20 * 1024 * 1024)
        return r.status, {k.lower(): v for k, v in r.getheaders()}, data
    except (OSError, ssl.SSLError) as e:
        raise MailError(f"Couldn't reach {parts.hostname} ({e}). Try again shortly.")
    finally:
        conn.close()


def http(method: str, url: str, headers: dict, body: bytes | None = None):
    """Follows up to three redirects (iCloud sends people to their own server), each checked again."""
    for _ in range(4):
        status, h, data = HTTP(method, url, headers, body)
        if status in (301, 302, 307, 308) and h.get("location"):
            url = urllib.parse.urljoin(url, h["location"])
            continue
        return status, h, data, url
    raise MailError("The calendar server sent Dealflow round in circles.")


HTTP = _https


def basic_auth(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


# ---------------------------------------------------------------- IMAP


class _PinnedIMAP(imaplib.IMAP4_SSL):
    def __init__(self, host, port, ip, timeout=60):
        self._pinned_ip = ip
        super().__init__(host, port, ssl_context=_context(), timeout=timeout)

    def _create_socket(self, timeout):
        sock = socket.create_connection((self._pinned_ip, self.port), timeout)
        return self.ssl_context.wrap_socket(sock, server_hostname=self.host)


def _imap(host: str, port: int):
    ip = resolve(host, port)
    try:
        return _PinnedIMAP(host, port, ip)
    except (OSError, ssl.SSLError, imaplib.IMAP4.error) as e:
        raise MailError(f"Couldn't reach the mail server {host} ({e}).")


IMAP = _imap
