from superagent.core.dialog_search import search_dialogs
from superagent.core.memory import Memory
from superagent.service import Platform


def test_dialog_search_handles_russian_and_no_image_base64(platform,tmp_path):
    memory=platform.runner.conversation_memory();memory.ensure('chat','__conversation__','Важный диалог')
    memory.append('chat',{'role':'user','content':[{'type':'text','text':'Обсуждали ОПЛАТУ сервера'},{'type':'image_url','image_url':{'url':'data:image/png;base64,private-image-marker'}}]})
    result=search_dialogs(platform.root,platform.registry,'оплату',5)
    assert result['dialogs'][0]['session_id']=='chat'
    assert result['dialogs'][0]['scope']=='conversation'
    assert 'ОПЛАТУ' in result['dialogs'][0]['snippet']
    assert search_dialogs(platform.root,platform.registry,'private-image-marker',5)['dialogs']==[]
    other=Platform(tmp_path/'other')
    try:assert search_dialogs(other.root,other.registry,'оплату',5)['dialogs']==[]
    finally:other.close()


def test_dialog_search_groups_results_by_dialog(platform):
    memory=platform.runner.conversation_memory();memory.ensure('chat','__conversation__','Topic')
    for i in range(5):memory.append('chat',{'role':'user','content':'query '+str(i)})
    result=search_dialogs(platform.root,platform.registry,'query',5)
    assert len(result['dialogs'])==1
    assert 'query 4' in result['dialogs'][0]['snippet']
