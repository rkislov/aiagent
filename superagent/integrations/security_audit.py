"""Offline review of supplied website security evidence."""

import re
from collections import Counter


SECURITY_TOOLS = {'security_web_headers', 'security_cookie_flags', 'security_zap_report'}
HEADER_NAMES = {
    'strict-transport-security', 'content-security-policy',
    'content-security-policy-report-only', 'x-frame-options',
    'x-content-type-options', 'referrer-policy',
    'access-control-allow-origin', 'access-control-allow-credentials',
}
ZAP_RISK = {'3': 'high', '2': 'medium', '1': 'low', '0': 'informational'}
ZAP_CONFIDENCE = {'0': 'false_positive', '1': 'low', '2': 'medium', '3': 'high', '4': 'confirmed_in_report'}


def review_headers(headers, https, response_type):
    if response_type not in ('html', 'api'):
        raise ValueError('response_type: html или api')
    observed = {}
    for name, raw in headers.items():
        name = name.strip().lower()
        if name not in HEADER_NAMES:
            continue
        values = [raw] if isinstance(raw, str) else raw
        if not isinstance(values, list) or len(values) > 20 or any(
            not isinstance(value, str) or len(value) > 16_384 or '\r' in value or '\n' in value for value in values
        ):
            raise ValueError('Значения заголовков передавайте отдельными строками без переносов')
        observed.setdefault(name, []).extend(value.strip().lower() for value in values if value.strip())

    checks = []

    def check(control, configured, recommendation):
        checks.append({'control': control, 'status': 'observed' if configured else 'review',
                       'recommendation': '' if configured else recommendation})

    if https:
        hsts = observed.get('strict-transport-security', [])
        valid_hsts = len(hsts) == 1 and re.search(r'(?:^|;)\s*max-age\s*=\s*0*[1-9]\d*\s*(?:;|$)', hsts[0])
        check('HSTS', valid_hsts, 'Проверьте, что HTTPS-ответ содержит один Strict-Transport-Security с ненулевым max-age.')

    if response_type == 'html':
        policy = observed.get('content-security-policy', [])
        report_only = bool(observed.get('content-security-policy-report-only'))
        check('CSP', bool(policy), 'Есть только политика Report-Only. Проверьте возможность включить принудительную политику.' if report_only else 'Добавьте Content-Security-Policy, подходящую для приложения.')
        frame_policy = any(re.search(r'(?:^|;)\s*frame-ancestors\s+[^;\s]', value) for value in policy)
        frame_options = observed.get('x-frame-options', [])
        frame_option = len(frame_options) == 1 and frame_options[0] in ('deny', 'sameorigin')
        check('Права на встраивание страницы', frame_policy or frame_option,
              'Задайте frame-ancestors для разрешённых сайтов или X-Frame-Options для совместимости.')
        check('Referrer-Policy', bool(observed.get('referrer-policy')),
              'Явно задайте политику передачи Referer, подходящую для приложения.')

    check('X-Content-Type-Options', observed.get('x-content-type-options') == ['nosniff'],
          'Укажите корректный Content-Type и X-Content-Type-Options: nosniff.')
    if 'true' in observed.get('access-control-allow-credentials', []):
        origins = observed.get('access-control-allow-origin', [])
        recommendation = ('Для запросов с учётными данными задавайте конкретный разрешённый origin. Браузеры не принимают сочетание с *.'
                          if '*' in origins else 'Сверьте разрешённый origin со списком доверенных сайтов приложения.')
        check('CORS с учётными данными', False, recommendation)
    return {'checks': checks, 'scope': 'Предоставленный HTTP-ответ. Проверяется наличие настроек, качество политик требует отдельной оценки.'}


def review_cookie_flags(cookies):
    if not 1 <= len(cookies) <= 50:
        raise ValueError('Передайте настройки от 1 до 50 сессионных cookie')
    result = []
    for cookie in cookies:
        if set(cookie) != {'name', 'secure', 'http_only', 'same_site'}:
            raise ValueError('Укажите только имя cookie и флаги Secure, HttpOnly и SameSite')
        name = cookie['name']
        same_site = cookie['same_site']
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", name):
            raise ValueError('Некорректное имя cookie')
        if type(cookie['secure']) is not bool or type(cookie['http_only']) is not bool:
            raise ValueError('Флаги secure и http_only должны быть true или false')
        if not isinstance(same_site, str) or same_site.lower() not in ('', 'lax', 'strict', 'none'):
            raise ValueError('same_site: Lax, Strict, None или пустая строка')
        recommendations = []
        if not cookie['secure']:
            recommendations.append('Включите Secure для передачи сессионной cookie только по HTTPS.')
        if not cookie['http_only']:
            recommendations.append('Включите HttpOnly, если клиентскому JavaScript не нужен доступ к cookie.')
        if not same_site:
            recommendations.append('Явно задайте SameSite с учётом сценариев входа и переходов между сайтами.')
        if same_site.lower() == 'none' and not cookie['secure']:
            recommendations.append('Для SameSite=None браузеры требуют Secure.')
        result.append({'cookie': name, 'recommendations': recommendations})
    return {'cookies': result}


def summarize_zap(report, offset, limit):
    sites = report.get('site')
    if not isinstance(sites, list) or not 0 <= offset <= 5000 or not 1 <= limit <= 50:
        raise ValueError('Нужен Traditional JSON отчёт ZAP; offset: 0–5000, limit: 1–50')
    findings = []
    for site in sites:
        if not isinstance(site, dict) or not isinstance(site.get('alerts'), list):
            raise ValueError('Некорректный раздел site.alerts отчёта ZAP')
        for alert in site['alerts']:
            if len(findings) >= 5000:
                raise ValueError('В отчёте больше 5000 групп находок. Передайте отчёты по одному сайту.')
            if not isinstance(alert, dict):
                raise ValueError('Некорректная находка в отчёте ZAP')
            instances = alert.get('instances', [])
            count = str(alert.get('count', ''))
            if re.fullmatch(r'[0-9]{1,9}', count):
                count = int(count)
            else:
                count = len(instances) if isinstance(instances, list) else 0
            findings.append({
                'site': str(site.get('@host') or '')[:200],
                'id': str(alert.get('alertRef') or alert.get('pluginid') or '')[:80],
                'title': str(alert.get('alert') or alert.get('name') or '')[:200],
                'risk': ZAP_RISK.get(str(alert.get('riskcode')), 'unknown'),
                'confidence': ZAP_CONFIDENCE.get(str(alert.get('confidence')), 'unknown'),
                'instances': count,
            })
    order = ['high', 'medium', 'low', 'informational', 'unknown']
    findings.sort(key=lambda finding: order.index(finding['risk']))
    return {
        'findings': findings[offset:offset + limit],
        'counts': dict(Counter(finding['risk'] for finding in findings)),
        'total': len(findings),
        'next_offset': offset + limit if offset + limit < len(findings) else -1,
        'scope': 'Находки и оценки из отчёта ZAP. Требуется проверка по коду и конфигурации.',
    }


def run_security(name, payload):
    if name == 'security_web_headers':
        return review_headers(payload['headers'], payload['https'], payload['response_type'])
    if name == 'security_cookie_flags':
        return review_cookie_flags(payload['cookies'])
    return summarize_zap(payload['report'], payload['offset'], payload['limit'])
