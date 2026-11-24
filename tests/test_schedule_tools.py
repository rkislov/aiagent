import threading
import time
import pytest
from superagent.schedules import Schedule, Store, execute_due
from superagent.integrations.schedule_tools import list_schedules
from superagent.service import Platform


def task(**kwargs):
    return {'name':'Daily digest','agent':'','model':'demo:office','message':'Prepare a digest','mode':'daily','local_time':'09:00','timezone':'Europe/Moscow',**kwargs}


def plan(platform, payload=None):
    return platform.runner.builtins.prepare_write('schedule_save',{'schedule_id':'','task':payload or task()},model='demo:office')


def test_schedule_requires_single_use_confirmation_and_immutable_payload(platform):
    payload=task(allow_reads=True)
    pending=plan(platform,payload)
    assert pending.preview['automatic_source_reading'] is True
    assert pending.preview['next_run_local'].endswith('+03:00')
    payload['message']='changed'
    pending.preview['task']['message']='also changed'
    with pytest.raises(PermissionError):pending.execute()
    assert Store(platform.root).list()==[]
    result=pending.execute(confirmed=True)
    assert result['saved']
    assert Store(platform.root).list()[0]['message']=='Prepare a digest'
    with pytest.raises(PermissionError):pending.execute(confirmed=True)
    assert len(Store(platform.root).list())==1


def test_edit_and_controls_reject_stale_confirmation(platform):
    id_=plan(platform).execute(confirmed=True)['schedule_id']
    pause=platform.runner.builtins.prepare_write('schedule_control',{'schedule_id':id_,'action':'pause'})
    update=platform.runner.builtins.prepare_write('schedule_save',{'schedule_id':id_,'task':task(name='Updated')})
    pause.execute(confirmed=True)
    with pytest.raises(ValueError,match='изменилось'):update.execute(confirmed=True)
    assert Store(platform.root).list()[0]['name']=='Daily digest'
    resume=platform.runner.builtins.prepare_write('schedule_control',{'schedule_id':id_,'action':'resume'})
    resume.execute(confirmed=True)
    delete=platform.runner.builtins.prepare_write('schedule_control',{'schedule_id':id_,'action':'delete'})
    delete.execute(confirmed=True)
    assert Store(platform.root).list()==[]


def test_invalid_targets_and_workspace_paths_cannot_be_scheduled(platform,tmp_path):
    with pytest.raises(ValueError):plan(platform,task(agent='not_existing'))
    with pytest.raises(ValueError):plan(platform,task(root='/another/user'))
    with pytest.raises(ValueError):plan(platform,task(connections=['ssh']))
    id_=plan(platform).execute(confirmed=True)['schedule_id']
    other=Platform(tmp_path/'other')
    try:
        assert list_schedules(other.runner.builtins)['schedules']==[]
        with pytest.raises(ValueError,match='не найдено'):other.runner.builtins.prepare_write('schedule_control',{'schedule_id':id_,'action':'pause'})
    finally:other.close()


def test_expired_planned_time_is_not_silently_rescheduled(platform,monkeypatch):
    pending=plan(platform,task(mode='interval',interval_minutes=1))
    now=time.time()
    monkeypatch.setattr('superagent.schedules.time.time',lambda:now+120)
    with pytest.raises(ValueError,match='прошло'):pending.execute(confirmed=True)
    assert Store(platform.root).list()==[]


def test_scheduled_plain_chat_never_approves_schedule_writes(platform,monkeypatch):
    id_=plan(platform,task(mode='interval',allow_reads=True)).execute(confirmed=True)['schedule_id']
    store=Store(platform.root)
    with store.connect() as db:db.execute('UPDATE jobs SET next_run=0 WHERE id=?',(id_,))
    def run(*args,confirm,**kwargs):
        assert not confirm({'skill':'schedule_save'})
        assert not confirm({'skill':'schedule_control'})
        yield {'type':'done','content':'result'}
    monkeypatch.setattr(platform.runner,'run_conversation',run)
    execute_due(platform,threading.Event())
    assert store.list()[0]['status']=='ok'


def test_schedule_listing_has_no_previous_job_results(platform):
    result=plan(platform).execute(confirmed=True)
    Store(platform.root).finish(result['schedule_id'],'ok','private-mail-result')
    listed=list_schedules(platform.runner.builtins)
    assert 'private-mail-result' not in str(listed)
    assert listed['models']
