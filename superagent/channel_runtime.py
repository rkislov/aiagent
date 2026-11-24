"""Lifecycle management of explicitly enabled, user-scoped channel workers."""
import hashlib
import threading
import time
from .channels import run, validate_channel


class ChannelSupervisor:
    def __init__(self,owner,auth):
        self.owner,self.auth=owner,auth
        self.stop=threading.Event();self.lock=threading.RLock();self.workers={}
        self.thread=threading.Thread(target=self.loop,daemon=True,name='channel-supervisor')

    def start(self):self.thread.start()

    def services(self):
        if not self.auth:return [self.owner]
        with self.auth.connect() as db:users=db.execute('SELECT id FROM users WHERE enrolled=1').fetchall()
        return [self.auth.service({'id':uid}) for (uid,) in users if (self.owner.root/'data/users'/uid/'config.yaml').exists()]

    def report(self,key,state,error=''):
        with self.lock:
            if key in self.workers:self.workers[key].update(state=state,error=error)

    def reconcile(self,services):
        desired={}
        for service in services:
            for name,cfg in list(service.config.channels.items()):
                if cfg.enabled:
                    desired[(str(service.root),name)]=(service,cfg.model_copy(deep=True))
        with self.lock:
            for key,entry in list(self.workers.items()):
                if key not in desired:
                    entry['stop'].set()
                    if not entry['thread'].is_alive():self.workers.pop(key,None)
            for key,(service,cfg) in desired.items():
                signature=hashlib.sha256((cfg.model_dump_json()+'\0'+cfg.key()).encode()).hexdigest()
                previous=self.workers.get(key)
                if previous:
                    if previous['signature']!=signature:
                        previous['stop'].set()
                        if previous['thread'].is_alive():continue
                    elif previous['thread'].is_alive() or time.monotonic()<previous['retry_at']:continue
                stopped=threading.Event()
                def work(key=key,service=service,cfg=cfg,stopped=stopped):
                    try:
                        validate_channel(service,cfg)
                        run(service,key[1],stop=stopped,config=cfg,on_state=lambda state:self.report(key,state))
                        self.report(key,'stopped')
                    except Exception as exc:
                        import httpx
                        error=('HTTP '+str(exc.response.status_code)+' — проверьте токен, адрес и доступ к каналу') if isinstance(exc,httpx.HTTPStatusError) else ('Нет соединения с сервисом' if isinstance(exc,httpx.HTTPError) else service.audit.redact(str(exc)))
                        self.report(key,'error',error)
                    finally:
                        with self.lock:
                            if key in self.workers:self.workers[key]['retry_at']=time.monotonic()+30
                worker=threading.Thread(target=work,daemon=True,name='channel-'+key[1])
                self.workers[key]={'signature':signature,'thread':worker,'stop':stopped,'state':'connecting','error':'','retry_at':0}
                worker.start()

    def status(self,service):
        with self.lock:
            return [{'name':name,'type':cfg.type,'enabled':cfg.enabled,'status':self.workers.get((str(service.root),name),{}).get('state','stopped'),'error':self.workers.get((str(service.root),name),{}).get('error','')} for name,cfg in service.config.channels.items()]

    def loop(self):
        while not self.stop.is_set():
            try:self.reconcile(self.services())
            except Exception:pass
            self.stop.wait(2)

    def close(self):
        self.stop.set()
        with self.lock:
            for entry in self.workers.values():entry['stop'].set()
        self.thread.join(timeout=2)
