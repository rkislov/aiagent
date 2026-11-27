import json
import threading
import time
from contextlib import ExitStack

from fastapi.testclient import TestClient
from superagent.auth import Auth
from superagent.web.app import create_app
from test_auth import login


def eventually(read, predicate):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(.02)
    raise AssertionError('Expected state did not become visible')


def test_admin_audit_and_pending_approval_are_scoped_to_the_right_people(platform, monkeypatch):
    started = threading.Event()
    release_model = threading.Event()
    executed = []

    class Provider:
        def chat(self, messages, tools):
            if any(message['role'] == 'tool' for message in messages):
                return {'role': 'assistant', 'content': 'Личный результат выполнения'}
            started.set()
            assert release_model.wait(5)
            return {
                'role': 'assistant', 'content': None,
                'tool_calls': [{'id': 'read', 'type': 'function', 'function': {
                    'name': 'office_read', 'arguments': json.dumps({'file_id': 'a' * 32, 'offset': 0, 'limit': 20}),
                }}],
            }

    def fake_skill(self, name, payload):
        executed.append((name, payload))
        return {'result': 'Закрытые данные документа'}

    monkeypatch.setattr('superagent.core.agent_loop.make_provider', lambda *args: Provider())
    monkeypatch.setattr('superagent.integrations.hub.Hub.run', fake_skill)
    auth = Auth(platform.root)
    auth.create('admin', 'admin-long-password', bootstrap=True)
    auth.create('alice', 'alice-long-password')
    auth.create('bobby', 'bobby-long-password')
    app = create_app(platform)
    with ExitStack() as stack:
        admin, alice, bob = [stack.enter_context(TestClient(app, base_url='http://localhost')) for _ in range(3)]
        login(admin, 'admin', 'admin-long-password')
        login(alice, 'alice', 'alice-long-password')
        login(bob, 'bobby', 'bobby-long-password')

        connection_view = alice.get('/api/connections/settings').json()
        assert set(connection_view) == {'connections', 'channels'}
        assert alice.put('/api/connections/settings', json=connection_view).status_code == 200
        assert alice.put('/api/connections/settings', json={'providers': {}}).status_code == 400

        assert alice.get('/api/audit/overview?scope=all').status_code == 403
        assert admin.get('/api/audit/overview?scope=all').json()['accounts'] == ['admin', 'alice', 'bobby']
        response = alice.post('/api/chat', json={'conversation': True, 'model': 'demo:office',
                                                 'message': 'Прочитай личный документ', 'transport': 'poll'})
        assert response.status_code == 200
        run_id = response.json()['run_id']
        try:
            assert started.wait(4)
            view = admin.get('/api/audit/overview?scope=all').json()
            assert [(row['user'], row['stage']) for row in view['active']] == [('alice', 'model')]
            assert bob.get('/api/audit/overview').json()['active'] == []
        finally:
            release_model.set()
        approvals = eventually(lambda: alice.get('/api/confirmations').json(), bool)
        approval, = approvals
        assert approval['skill'] == 'office_read'
        assert admin.get('/api/audit/overview?scope=all').json()['active'][0]['stage'] == 'confirmation'
        assert admin.get('/api/confirmations').json() == []
        assert bob.get('/api/confirmations').json() == []
        action = '/api/confirmations/' + approval['id']
        assert admin.post(action, json={'approved': True}).status_code == 404
        assert bob.post(action, json={'approved': True}).status_code == 404
        assert not executed

        # A different page with the same session can discover and approve the request.
        refreshed = TestClient(app, base_url='http://localhost', cookies=alice.cookies)
        try:
            assert refreshed.get('/api/confirmations').json()[0]['id'] == approval['id']
            assert refreshed.post(action, json={'approved': True}).status_code == 200
            assert refreshed.get('/api/confirmations').json() == []
        finally:
            refreshed.close()
        events = []
        def poll():
            response = alice.get('/api/chat/runs/' + run_id).json()
            events.extend(response['events'])
            return response
        eventually(poll, lambda response: response['done'])
        assert len(executed) == 1
        assert any(event['type'] == 'confirmation_result' and event['approved'] for event in events)

        overview = admin.get('/api/audit/overview?scope=all&account=alice').json()
        assert overview['active'] == []
        assert {entry['user'] for entry in overview['events']} == {'alice'}
        assert all(not (entry.keys() & {'input', 'output', 'session_id'}) for entry in overview['events'])
        model_row = next(entry for entry in overview['events'] if entry['kind'] == 'model')
        path = f"/api/audit/request?account=alice&event_id={model_row['id']}"
        assert admin.get(path).json() == {'text': 'Прочитай личный документ'}
        assert bob.get(path).status_code == 403
        assert bob.get('/api/audit/overview').json()['events'] == []
        assert alice.get('/api/audit/overview').json()['events']
