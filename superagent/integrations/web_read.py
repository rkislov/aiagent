"""Public HTTP(S) reader with pinned DNS and bounded responses."""
import http.client
import ipaddress
import socket
import ssl
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin


def public_address(host, port):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError("Веб-чтение доступно только для публичных адресов")
    return addresses[0][4][0]


class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.hidden = 0; self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'): self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'): self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden and data.strip(): self.parts.append(data.strip())


def read_page(url):
    for _ in range(4):
        parsed = urlsplit(url)
        if (len(url) > 4000 or parsed.scheme not in ('https', 'http') or not parsed.hostname
                or parsed.username or parsed.password or parsed.port not in (None, 80, 443)):
            raise ValueError("Нужен публичный HTTP(S) URL без учётных данных, порт 80 или 443")
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        address = public_address(parsed.hostname, port)
        conn = http.client.HTTPConnection(parsed.hostname, port, timeout=15)
        try:
            conn.sock = socket.create_connection((address, port), timeout=15)
            if parsed.scheme == 'https':
                conn.sock = ssl.create_default_context().wrap_socket(conn.sock, server_hostname=parsed.hostname)
            target = parsed.path or '/'
            if parsed.query: target += '?' + parsed.query
            conn.request('GET', target, headers={'Host': parsed.netloc, 'User-Agent': 'AIagent-WebReader/1.0', 'Accept-Encoding': 'identity'})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location: raise ValueError("Переадресация без адреса")
                url = urljoin(url, location); continue
            if response.status != 200: raise ValueError(f"Страница ответила HTTP {response.status}")
            mime = response.getheader('Content-Type', '').split(';')[0].lower()
            if mime not in ('text/html', 'text/plain', 'application/xhtml+xml'):
                raise ValueError("Веб-чтение поддерживает HTML и текстовые страницы")
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000: raise ValueError("Страница превышает 2 МБ")
            charset = response.headers.get_content_charset() or 'utf-8'
            text = raw.decode(charset, errors='replace')
            if mime != 'text/plain':
                parser = Text(); parser.feed(text); text = '\n'.join(parser.parts)
            return {'url': url, 'text': text[:30000], 'truncated': len(text) > 30000, 'untrusted': True}
        finally:
            conn.close()
    raise ValueError("Слишком много переадресаций")
