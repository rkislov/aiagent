import pytest
from fastapi.testclient import TestClient

from superagent.web.app import create_app


def test_security_auditor_template_saves_with_passive_tools(platform):
    with TestClient(create_app(platform, authentication=False), base_url='http://localhost') as client:
        template = client.get('/api/agent-templates/security_auditor').json()
        assert set(template['skills']) == {
            'security_web_headers', 'security_cookie_flags', 'security_zap_report', 'office_read', 'office_compare',
        }
        template['model'] = 'demo:office'
        assert client.put('/api/agents/security_auditor', json=template).status_code == 200
        assert platform.registry.build('security_auditor').skills == template['skills']


def test_headers_distinguish_report_only_and_duplicate_hsts(platform):
    hub = platform.runner.builtins
    headers = {
        'Content-Security-Policy-Report-Only': "default-src 'self'",
        'Strict-Transport-Security': ['max-age=31536000', 'max-age=0'],
        'X-Content-Type-Options': 'NoSniff',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Credentials': 'true',
        'Set-Cookie': 'session=example-sensitive-value',
    }
    result = hub.run('security_web_headers', {'headers': headers, 'https': True, 'response_type': 'html'})
    by_control = {row['control']: row for row in result['checks']}
    assert by_control['HSTS']['status'] == 'review'
    assert by_control['CSP']['status'] == 'review'
    assert 'Report-Only' in by_control['CSP']['recommendation']
    assert by_control['X-Content-Type-Options']['status'] == 'observed'
    assert by_control['CORS с учётными данными']['status'] == 'review'
    assert 'example-sensitive-value' not in str(result)


def test_cookie_flags_accept_only_metadata(platform):
    hub = platform.runner.builtins
    good = {'name': 'session', 'secure': True, 'http_only': True, 'same_site': 'Lax'}
    bad = {'name': 'auth', 'secure': False, 'http_only': False, 'same_site': 'None'}
    result = hub.run('security_cookie_flags', {'cookies': [good, bad]})
    assert result['cookies'][0]['recommendations'] == []
    assert any('SameSite=None' in line for line in result['cookies'][1]['recommendations'])
    with pytest.raises(ValueError, match='флаги'):
        hub.run('security_cookie_flags', {'cookies': [{**good, 'value': 'private'}]})


def test_zap_report_orders_and_paginates_without_raw_traffic(platform):
    hub = platform.runner.builtins
    report = {'site': [{'@host': 'example.test', 'alerts': [
        {'pluginid': '100', 'name': 'First', 'riskcode': '1', 'instances': []},
        {'pluginid': '200', 'name': 'Second', 'riskcode': '3', 'confidence': '2',
         'instances': [{'uri': 'https://example.test/?private=1', 'evidence': 'private evidence'}]},
    ]}]}
    first = hub.run('security_zap_report', {'report': report, 'offset': 0, 'limit': 1})
    assert first['findings'][0] == {
        'site': 'example.test', 'id': '200', 'title': 'Second', 'risk': 'high', 'confidence': 'medium', 'instances': 1,
    }
    assert first['counts'] == {'high': 1, 'low': 1}
    second = hub.run('security_zap_report', {'report': report, 'offset': first['next_offset'], 'limit': 1})
    assert second['findings'][0]['id'] == '100'
    assert second['next_offset'] == -1
