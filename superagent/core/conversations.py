"""Copy a legacy agent conversation into independent shared chat storage."""
import json
from uuid import uuid4


def import_agent_history(service, name, session_id):
    agent=service.registry.build(name)
    old=service.runner.memory(agent)
    row=next((row for row in old.sessions(name) if row['id']==session_id),None)
    if row is None:raise ValueError('Диалог агента не найден')
    memory=service.runner.conversation_memory()
    new_id=uuid4().hex
    history=old.load(session_id)
    with memory.connect() as db:
        db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(new_id,'__conversation__',row['title'],row['created']))
        for message in history:
            saved={**message,'_speaker':name,'_model':agent.model} if message['role']!='user' else message
            db.execute('INSERT INTO messages(session_id,payload) VALUES(?,?)',(new_id,json.dumps(saved,ensure_ascii=False)))
    memory.web(new_id, old.web(session_id))
    memory.knowledge(new_id, old.knowledge(session_id))
    return new_id
