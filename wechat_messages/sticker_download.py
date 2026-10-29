"""Bounded, direct CDN GETs. Resolve once and connect to that public IP only."""
import http.client
import ipaddress
import queue
import socket
import ssl
import threading
import time
from urllib.parse import urljoin, urlsplit

from wechat_account_probe.models import ProbeError
from .images import MAX_IMAGE

NETWORK_SECONDS = 20
BYTES_PER_SECOND = 1024 * 1024
CHUNK = 64 * 1024
# Observed in authenticated 4.1.15.56 message XML, alongside emoji.qpic.cn.
EXACT_CDN_HOSTS = {"vweixinf.tc.qq.com"}


def cdn_url(value):
    try:
        if not isinstance(value, str) or len(value) > 8192 or any(ord(c) <= 32 or ord(c) == 127 for c in value):
            raise ValueError()
        url = urlsplit(value)
        host = url.hostname or ""
        port = 443 if url.scheme == "https" else 80
        if (url.scheme not in ("http", "https") or url.username is not None or url.password is not None
                or not (host == "qpic.cn" or host.endswith(".qpic.cn") or host in EXACT_CDN_HOSTS)
                or url.port not in (None, port) or url.fragment
                or not host.isascii() or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in host)):
            raise ValueError()
        target = (url.path or "/") + ("?" + url.query if url.query else "")
        target.encode("ascii")
        return url.scheme, host, port, target
    except (ValueError, UnicodeError):
        raise ProbeError("sticker_url_not_allowed") from None


class StickerDownload:
    """One cumulative budget for the original, redirects and thumbnail fallback."""
    def __init__(self, check):
        self.source_check = check
        self.started = time.monotonic()
        self.deadline = self.started + NETWORK_SECONDS
        self.received = 0

    def check(self):
        self.source_check()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ProbeError("sticker_download_timeout")
        return remaining

    def addresses(self, host, port):
        # DNS itself must not block the worker past the network deadline.
        answer = queue.Queue(maxsize=1)

        def resolve():
            try:
                answer.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
            except OSError:
                answer.put(None)

        threading.Thread(target=resolve, daemon=True, name="sticker-dns").start()
        while True:
            try:
                values = answer.get(timeout=min(.1, self.check()))
                break
            except queue.Empty:
                pass
        if not values:
            raise ProbeError("sticker_download_failed")
        for family, _, _, _, address in values:
            ip = ipaddress.ip_address(address[0])
            if family not in (socket.AF_INET, socket.AF_INET6) or not ip.is_global or getattr(ip, "ipv4_mapped", None):
                raise ProbeError("sticker_url_not_allowed")
        return values

    def connection(self, scheme, host, port):
        values = self.addresses(host, port)
        # Prefer IPv4; do not run multiple simultaneous connections or retry storms.
        family, _, _, _, address = sorted(values, key=lambda item: item[0] != socket.AF_INET)[0]
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.settimeout(min(5, self.check()))
            sock.connect(address)
            if scheme == "https":
                sock.settimeout(min(5, self.check()))
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            connection = http.client.HTTPConnection(host, port, timeout=min(5, self.check()))
            connection.sock = sock
            return connection
        except BaseException:
            sock.close()
            raise

    def fetch(self, url):
        try:
            for redirect in range(3):
                scheme, host, port, target = cdn_url(url)
                self.check()
                connection = self.connection(scheme, host, port)
                transport = connection.sock
                response = None

                def interrupt(sock=transport):
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

                watchdog = None
                try:
                    watchdog = threading.Timer(self.check(), interrupt)
                    watchdog.daemon = True
                    watchdog.start()
                    transport.settimeout(min(5, self.check()))
                    connection.request("GET", target, headers={"Accept": "image/*", "Accept-Encoding": "identity", "Connection": "close"})
                    transport.settimeout(min(5, self.check()))
                    response = connection.getresponse()
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.getheader("Location")
                        if not location or redirect == 2:
                            raise ProbeError("sticker_redirect_limit")
                        url = urljoin(url, location)
                        continue
                    if response.status != 200:
                        raise ProbeError("sticker_download_failed")
                    if response.getheader("Content-Encoding", "identity").lower() != "identity":
                        raise ProbeError("image_invalid")
                    declared = response.getheader("Content-Length")
                    if declared is not None and (not declared.isdecimal() or int(declared) > MAX_IMAGE):
                        raise ProbeError("image_size_limit" if declared.isdecimal() else "image_invalid")
                    data = bytearray()
                    while True:
                        transport.settimeout(min(5, self.check()))
                        block = response.read1(min(CHUNK, MAX_IMAGE + 1 - len(data)))
                        if not block:
                            break
                        data.extend(block)
                        if len(data) > MAX_IMAGE:
                            raise ProbeError("image_size_limit")
                        self.received += len(block)
                        due = self.started + self.received / BYTES_PER_SECOND
                        while True:
                            delay = due - time.monotonic()
                            if delay <= 0:
                                break
                            time.sleep(min(.05, delay, self.check()))
                    self.check()
                    if not data or (declared is not None and len(data) != int(declared)):
                        raise ProbeError("image_invalid")
                    return bytes(data)
                finally:
                    if watchdog is not None:
                        watchdog.cancel()
                    if response is not None:
                        response.close()
                    connection.close()
        except ProbeError:
            raise
        except (TimeoutError, socket.timeout):
            raise ProbeError("sticker_download_timeout") from None
        except (OSError, http.client.HTTPException, ValueError):
            self.check()
            raise ProbeError("sticker_download_failed") from None
