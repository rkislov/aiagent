"""Read-only administrative view of event metadata across workspaces."""

from contextlib import closing
from heapq import merge
from itertools import islice
import json
import re
import sqlite3


def _metadata(path, account, guest, agent, skill, status, date, count):
    if not path.is_file() or path.resolve() != path:
        return []
    filters, parameters = [], []
    for column, value in (('agent', agent), ('skill', skill), ('status', status)):
        if value:
            filters.append(f'{column} = ?')
            parameters.append(value)
    if date:
        filters.append('time LIKE ?')
        parameters.append(date + '%')
    where = ' WHERE ' + ' AND '.join(filters) if filters else ''
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute('''
            SELECT id, time, agent, skill, status, kind,
                   CASE WHEN json_valid(payload) THEN
                       CASE WHEN json_type(payload, '$.user') = 'text'
                            THEN substr(json_extract(payload, '$.user'), 1, 320) END
                   END AS user,
                   CASE WHEN json_valid(payload) THEN
                       CASE WHEN json_type(payload, '$.duration_ms') IN ('integer', 'real')
                            THEN json_extract(payload, '$.duration_ms') END
                   END AS duration_ms
            FROM events
            ''' + where + ' ORDER BY time DESC, id DESC LIMIT ?', [*parameters, count]).fetchall()
    return [{**row, 'account': account, 'guest': guest} for row in rows]


def overview(auth, actor, *, account=None, agent=None, skill=None, status=None, date=None, limit=30, offset=0):
    users = auth.list_users(actor)
    active, batches = [], []
    with auth.lock:
        services = auth.services.copy()
    for user in users:
        username, uid = user['username'], user['id']
        if account and username != account:
            continue
        root = auth.root / 'data/users' / uid
        tenant = services.get(uid)
        if tenant:
            workspaces = [tenant, *getattr(tenant, '_channel_users', {}).copy().values()]
            active.extend({**entry, 'account': username} for workspace in workspaces for entry in workspace.audit.active())
        batches.append(_metadata(root / 'data/audit.db', username, '', agent, skill, status, date, offset + limit))
        for guest in (root / 'data/channel-users').glob('*'):
            if re.fullmatch(r'[a-f0-9]{64}', guest.name):
                batches.append(_metadata(guest / 'data/audit.db', username, guest.name, agent, skill, status, date, offset + limit))
    events = merge(*batches, key=lambda row: row['time'], reverse=True)
    return {'events': list(islice(events, offset, offset + limit)),
            'active': sorted(active, key=lambda row: row['time'], reverse=True),
            'accounts': [user['username'] for user in users]}


def request_text(auth, actor, account, event_id, guest):
    user = next((user for user in auth.list_users(actor) if user['username'] == account), None)
    if not user:
        return None
    root = auth.root / 'data/users' / user['id']
    if guest:
        root = root / 'data/channel-users' / guest
    path = root / 'data/audit.db'
    if not path.is_file() or path.resolve() != path:
        return None
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute("SELECT payload FROM events WHERE id=? AND kind='model' AND skill IS NULL", (event_id,)).fetchone()
    if not row:
        return None
    try:
        messages = json.loads(row[0]).get('input')
    except (ValueError, AttributeError):
        return None
    if not isinstance(messages, list):
        return None
    # Earlier messages, system instructions, tool output and images stay private.
    message = next((item for item in reversed(messages) if isinstance(item, dict) and item.get('role') == 'user'), {})
    content = message.get('content')
    if isinstance(content, list):
        content = '\n'.join(part['text'] for part in content
                            if isinstance(part, dict) and part.get('type') == 'text' and isinstance(part.get('text'), str))
    if not isinstance(content, str):
        return None
    return content.split('\n\nПрикреплённые файлы (для чтения используй office_read):\n', 1)[0]
