"""Bounded, read-only search across the current workspace's dialog databases."""
import json
import sqlite3
import time
from urllib.parse import quote


def search_dialogs(root,registry,query,limit):
    query=query.strip()
    if not query or len(query)>500 or type(limit) is not int or not 1<=limit<=20:
        raise ValueError('Нужен запрос до 500 символов и limit от 1 до 20')
    registry.scan()
    sources={root/'data/conversations.db'}
    for agent in registry.agents.values():
        path=(root/agent.memory.path).resolve()
        if path.is_relative_to((root/'data').resolve()) and path.suffix=='.db' and path.name not in ('audit.db','auth.db','schedules.db'):
            sources.add(path)
    results=[];truncated=False;deadline=time.monotonic()+2
    def text_content(payload):
        try:
            value=json.loads(payload).get('content')
            if isinstance(value,list):return '\n'.join(p.get('text','') for p in value if p.get('type')=='text')
            return value if isinstance(value,str) else ''
        except (ValueError,TypeError,AttributeError):return ''
    for path in sorted(sources):
        if time.monotonic()>=deadline:truncated=True;break
        if not path.exists():continue
        # Never inspect another tenant's files, even through an agent-supplied symlink.
        path=path.resolve()
        if not path.is_relative_to((root/'data').resolve()):continue
        try:
            with sqlite3.connect('file:'+quote(str(path),safe='/')+'?mode=ro',uri=True,timeout=.2) as db:
                db.create_function('dialog_text',1,text_content)
                db.create_function('unicode_fold',1,lambda value:value.casefold())
                db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
                rows=db.execute('''WITH hits AS (
                    SELECT s.id,s.agent,s.title,s.created,dialog_text(m.payload) AS text,
                    json_extract(m.payload,'$.role') AS role,m.id AS message_id,
                    row_number() OVER (PARTITION BY s.id ORDER BY m.id DESC) AS rank
                    FROM sessions s JOIN messages m ON s.id=m.session_id
                    WHERE instr(unicode_fold(dialog_text(m.payload)),?)>0
                    ) SELECT id,agent,title,created,text,role FROM hits WHERE rank=1 ORDER BY created DESC LIMIT ?''',(query.casefold(),limit+1)).fetchall()
            if len(rows)>limit:truncated=True
            for id_,agent,title,created,text,role in rows[:limit]:
                index=text.casefold().find(query.casefold());start=max(0,index-160)
                snippet=('…' if start else '')+text[start:start+650]+('…' if len(text)>start+650 else '')
                results.append({'session_id':id_,'scope':'conversation' if agent=='__conversation__' else 'agent','agent':'' if agent=='__conversation__' else agent,'title':title,'created':created,'role':role,'snippet':snippet})
        except sqlite3.Error:truncated=True
    results.sort(key=lambda row:row['created'],reverse=True)
    return {'dialogs':results[:limit],'truncated':truncated or len(results)>limit}
