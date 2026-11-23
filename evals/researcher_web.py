"""Page opening for the researcher pilot (B-102): a real browser, safely.

    async with Renderer() as r:
        page = await r.open("https://...")      # Page(text, links, error, ...)

The browser (Playwright driving the Chrome installed on the PC) and the PDF download both go through `GuardProxy`, a small local
proxy that resolves every host itself and connects only to public addresses on web ports. That covers what a check of the first
link cannot: redirects, pages that fetch other addresses, and names that resolve differently the second time. No cookies, no
credentials, a fresh browser profile per page, no downloads kept, no service workers. `Renderer.open` also refuses prediction-market
sites (T7; no Kalshi data, Christian 2026-11-16) and files over 5 MB.

Playwright and pypdf are used only here and are imported when needed; they are not in poetry.lock (ADR-0014), so the bot, its CI and
its code fingerprint are untouched.
"""
from __future__ import annotations

import asyncio
import io
import ipaddress
import re
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from fastlane.scrub import HOST_RE as MARKET_HOST_RE
from forecast_engine import research_v2 as rv

WEB_PORTS = {80, 443, 8080, 8443}
OPEN_TIMEOUT_S = 20.0
MAX_FILE_BYTES = 5_000_000
MAX_TEXT_CHARS = 200_000
MAX_LINKS = 400
BLOCKED_HOSTS_FOR_SEARCH = ("metaculus.com", "kalshi.com", "polymarket.com", "manifold.markets", "predictit.org")


@dataclass
class Page:
    url: str
    final_url: str = ""
    status: int | None = None
    text: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)  # (link text, absolute url)
    error: str = ""
    seconds: float = 0.0
    kind: str = "html"


def public_addresses(host: str, port: int = 443) -> list[str]:
    """The host's addresses if every one of them is public, else [] (also for names that do not resolve)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return []
    addrs = list(dict.fromkeys(i[4][0].split("%")[0] for i in infos))
    return addrs if addrs and all(ipaddress.ip_address(a).is_global for a in addrs) else []


def refuse_reason(url: str, resolve=public_addresses) -> str | None:
    """Why `open` must not touch this link, or None. The proxy repeats the address check for every connection."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "refused: malformed link"
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return "refused: not an http(s) link"
    if parts.username or parts.password:
        return "refused: link carries credentials"
    if port is not None and port not in WEB_PORTS:
        return f"refused: port {port}"
    if MARKET_HOST_RE.search(parts.hostname):
        return f"refused: {parts.hostname} is a prediction-market or forecast-aggregator site"
    if not resolve(parts.hostname):
        return f"refused: {parts.hostname} is not a public address"
    return None


# ------------------------------------------------------------------------------------------------------------ guard proxy
class GuardProxy:
    """HTTP proxy on 127.0.0.1 that connects only to public addresses on web ports (the resolved address is the one used)."""

    def __init__(self, resolve=public_addresses) -> None:
        self._resolve = resolve
        self._server: asyncio.base_events.Server | None = None
        self.refused: list[str] = []
        self.port = 0

    async def __aenter__(self) -> "GuardProxy":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        if self._server:
            self._server.close()

    async def _connect(self, host: str, port: int):
        if port not in WEB_PORTS:
            raise PermissionError(f"port {port}")
        addrs = await asyncio.to_thread(self._resolve, host, port)
        if not addrs:
            raise PermissionError("not a public address")
        for a in addrs:
            try:
                return await asyncio.wait_for(asyncio.open_connection(a, port), 10)
            except (OSError, asyncio.TimeoutError):
                continue
        raise ConnectionError("unreachable")

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
            first, _, rest = head.partition(b"\r\n")
            method, target, version = first.decode("latin-1").split(" ", 2)
            if method == "CONNECT":
                host, _, port = target.rpartition(":")
                host, port_n, path = host.strip("[]"), int(port), None
            else:
                u = urlsplit(target)
                if u.scheme != "http" or not u.hostname:
                    raise PermissionError("not http")
                host, port_n = u.hostname, u.port or 80
                path = (u.path or "/") + (f"?{u.query}" if u.query else "")
            try:
                up_r, up_w = await self._connect(host, port_n)
            except PermissionError as e:
                self.refused.append(f"{host}:{port_n} ({e})")
                writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return
            if path is None:
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await writer.drain()
            else:  # one plain-http request per connection
                lines = [ln for ln in rest.split(b"\r\n") if ln and not ln.lower().startswith((b"proxy-", b"connection:"))]
                up_w.write(f"{method} {path} {version}\r\n".encode("latin-1") + b"\r\n".join(lines) + b"\r\nConnection: close\r\n\r\n")
                await up_w.drain()
            await asyncio.gather(self._pipe(reader, up_w), self._pipe(up_r, writer))
        except Exception:  # noqa: BLE001 - a broken connection is the browser's problem, reported there
            pass
        finally:
            writer.close()

    @staticmethod
    async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
        try:
            while data := await src.read(65536):
                dst.write(data)
                await dst.drain()
        except (OSError, ConnectionError):
            pass
        finally:
            try:
                dst.close()
            except OSError:
                pass


# ------------------------------------------------------------------------------------------------------------- the browser
_LINKS_JS = """max => Array.from(document.querySelectorAll('a[href]')).slice(0, max * 3)
  .map(a => [(a.innerText || a.getAttribute('aria-label') || a.title || '').trim().replace(/\\s+/g, ' ').slice(0, 160), a.href])
  .filter(x => x[0] && /^https?:/i.test(x[1])).slice(0, max)"""


def _clean(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", text or "")
    return re.sub(r"\n\s*\n+", "\n", text).strip()[:MAX_TEXT_CHARS]


def pdf_text(body: bytes) -> str:
    from pypdf import PdfReader

    out, n = [], 0
    for page in PdfReader(io.BytesIO(body)).pages:
        t = page.extract_text() or ""
        out.append(t)
        n += len(t)
        if n > MAX_TEXT_CHARS:
            break
    return _clean("\n".join(out))


class Renderer:
    """One browser for the run, one fresh context per page."""

    def __init__(self, channel: str | None = "chrome", timeout_s: float = OPEN_TIMEOUT_S) -> None:
        self._channel, self._timeout = channel, timeout_s
        self._proxy = GuardProxy()
        self._pw = self._browser = None

    async def __aenter__(self) -> "Renderer":
        from playwright.async_api import async_playwright

        await self._proxy.__aenter__()
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            channel=self._channel, headless=True, proxy={"server": f"http://127.0.0.1:{self._proxy.port}", "bypass": "<-loopback>"},
            args=["--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--disable-quic"])
        return self

    async def __aexit__(self, *exc) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()
        await self._proxy.__aexit__()

    @property
    def refused(self) -> list[str]:
        return self._proxy.refused

    async def open(self, url: str) -> Page:
        start = time.monotonic()
        page = Page(url=url)
        page.error = await asyncio.to_thread(refuse_reason, url) or ""
        if not page.error:
            try:
                await asyncio.wait_for(self._render(page), self._timeout + 10)
            except Exception as e:  # noqa: BLE001 - every failure is a recorded outcome
                page.error = f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}"[:200]
        page.seconds = round(time.monotonic() - start, 1)
        return page

    async def _render(self, page: Page) -> None:
        deadline = time.monotonic() + self._timeout
        ctx = await self._browser.new_context(accept_downloads=False, service_workers="block", locale="en-US",
                                              viewport={"width": 1280, "height": 1600})
        try:
            tab = await ctx.new_page()
            try:
                resp = await tab.goto(page.url, timeout=self._timeout * 1000, wait_until="domcontentloaded")
            except Exception as e:
                if "Download is starting" in str(e) or "ERR_ABORTED" in str(e):
                    await self._file(page)
                    return
                raise
            ctype = ((resp.headers.get("content-type") if resp else "") or "").lower()
            if "application/pdf" in ctype:
                await self._file(page)
                return
            page.status = resp.status if resp else None
            left = max(0.5, deadline - time.monotonic())
            try:
                await tab.wait_for_load_state("networkidle", timeout=left * 1000)
            except Exception:  # noqa: BLE001 - pages that never go quiet are read as they are at the cap
                pass
            page.final_url = tab.url
            why = await asyncio.to_thread(refuse_reason, tab.url)
            if why:
                page.error = f"{why} (after redirect)"
                return
            parts = [await tab.evaluate("() => document.body ? document.body.innerText : ''")]
            for fr in tab.frames[1:9]:
                try:
                    t = await asyncio.wait_for(fr.evaluate("() => document.body ? document.body.innerText : ''"), 3)
                except Exception:  # noqa: BLE001 - a frame that is gone or blocked has no text
                    continue
                if len(t or "") > 200:
                    parts.append(t)
            page.text = _clean("\n".join(parts))
            page.links = [tuple(x) for x in await tab.evaluate(_LINKS_JS, MAX_LINKS)]
            if (page.status or 200) >= 400:
                page.error = f"HTTP {page.status}"
            elif not page.text:
                page.error = "empty page"
        finally:
            await ctx.close()

    async def _file(self, page: Page) -> None:
        kind, final, body = await asyncio.to_thread(download, page.url, self._proxy.port, self._timeout)
        page.kind, page.final_url, page.status = kind, final, 200
        page.text = pdf_text(body) if kind == "pdf" else _clean(body.decode("utf-8", errors="replace"))
        if not page.text:
            page.error = "no text in the file"


def download(url: str, proxy_port: int, timeout_s: float = OPEN_TIMEOUT_S, max_bytes: int = MAX_FILE_BYTES, max_redirects: int = 3,
             resolve=public_addresses, session=None) -> tuple[str, str, bytes]:
    """(kind, final url, bytes) of a PDF or text file, through the guard proxy, at most `max_bytes`. Raises on anything else."""
    import requests

    s = session or requests.Session()
    s.trust_env = False
    proxy = f"http://127.0.0.1:{proxy_port}"
    for _ in range(max_redirects + 1):
        why = refuse_reason(url, resolve)
        if why:
            raise PermissionError(why)
        resp = s.get(url, timeout=timeout_s, allow_redirects=False, stream=True, headers={"User-Agent": rv.USER_AGENT},
                     proxies={"http": proxy, "https": proxy})
        try:
            if resp.is_redirect and resp.headers.get("Location"):
                url = urljoin(url, resp.headers["Location"])
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if int(resp.headers.get("Content-Length") or 0) > max_bytes:
                raise RuntimeError("refused: file over 5 MB")
            body = b""
            for chunk in resp.iter_content(65536):
                body += chunk
                if len(body) > max_bytes:
                    raise RuntimeError("refused: file over 5 MB")
            if "pdf" in ctype or body[:5] == b"%PDF-":
                return "pdf", url, body
            if ctype.startswith(rv.TEXT_TYPES):
                return "text", url, body
            raise RuntimeError(f"refused: file type {ctype or 'unknown'}")
        finally:
            resp.close()
    raise RuntimeError("refused: too many redirects")
