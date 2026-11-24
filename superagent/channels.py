"""Explicitly started local channel workers; deny approvals outside the web UI."""
import time
import asyncio
import hashlib
import threading
import json
import httpx


def reply(service, config, sender, text, *, confirm=None, cancelled=None):
    session = hashlib.sha256((config.type + ':' + sender).encode()).hexdigest()
    result = ''
    confirm = confirm or (lambda _:False)
    events=(service.runner.run(config.agent,text,session,confirm=confirm,cancelled=cancelled) if config.agent else service.runner.run_conversation(text,config.model,session_id=session,confirm=confirm,cancelled=cancelled))
    for event in events:
        if event['type'] == 'done': result = event['content']
        elif event['type'] == 'error': result = event['error']
    return result or 'Действие не выполнено. Для подтверждения используйте веб-интерфейс.'


def validate_channel(service,cfg, *, require_enabled=True, config=None):
    if require_enabled and not cfg.enabled:raise ValueError('Канал выключен')
    if not cfg.key():raise ValueError('Укажите токен бота / пароль')
    if require_enabled and not cfg.allowed:raise ValueError('Укажите разрешённые chat ID / channel ID / JID')
    if cfg.agent:service.registry.build(cfg.agent)
    else:
        from .core.providers.factory import make_provider
        make_provider(cfg.model,config or service.config)
    if cfg.type=='mattermost' and not cfg.url:raise ValueError('Укажите HTTPS-адрес Mattermost')
    if cfg.type=='xmpp' and '@' not in cfg.username:raise ValueError('Укажите JID XMPP-бота')


def test_channel(service,cfg):
    validate_channel(service,cfg,require_enabled=False)
    if cfg.type!='mattermost':raise ValueError('Проверка API пока доступна для Mattermost')
    with httpx.Client(timeout=15,trust_env=False,headers={'Authorization':'Bearer '+cfg.key()}) as c:
        base=cfg.url.rstrip('/')+'/api/v4'
        r=c.get(base+'/users/me');r.raise_for_status();me=r.json()
        r=c.get(base+'/users/me/channels');r.raise_for_status()
        return {'ok':True,'is_bot':bool(me.get('is_bot')),'channels':[{k:row.get(k,'') for k in ('id','name','display_name','type')} for row in r.json()]}


def mattermost_confirmation(client,base,cfg,original,seen,stop,timeout):
    from .core.chat_connections import CONNECTIONS
    reads={name for _,tools in CONNECTIONS.values() for name in tools}|{'web_read','web_search','dialog_search'}
    def confirm(event):
        if event['skill'] not in reads:return False
        code=event['id'][:12]
        root=original.get('root_id') or original['id']
        message='Разрешить чтение **'+event['skill']+'**?\n```json\n'+json.dumps(event['input'],ensure_ascii=False,indent=2)+'\n```\nДля подтверждения ответьте в этом диалоге: **РАЗРЕШАЮ '+code+'**. Для отказа: **ОТМЕНА '+code+'**.'
        r=client.post(base+'/posts',json={'channel_id':original['channel_id'],'root_id':root,'message':message});r.raise_for_status()
        since=r.json().get('create_at',int(time.time()*1000))
        deadline=time.monotonic()+timeout
        while not stop.is_set() and time.monotonic()<deadline:
            r=client.get(base+'/channels/'+original['channel_id']+'/posts',params={'since':since});r.raise_for_status()
            for post in r.json().get('posts',{}).values():
                if post['user_id']!=original['user_id'] or post.get('create_at',0)<since:continue
                answer=post.get('message','').strip().casefold()
                if answer in ('разрешаю '+code,'отмена '+code):
                    seen.add(post['id']);return answer.startswith('разрешаю ')
            stop.wait(1)
        return False
    return confirm


def run(service, name, *, stop=None, config=None, on_state=None):
    cfg = config or service.config.channels[name]
    stop = stop or threading.Event()
    on_state = on_state or (lambda _:None)
    validate_channel(service,cfg)
    if cfg.type == 'xmpp':
        import slixmpp
        class Bot(slixmpp.ClientXMPP):
            def __init__(self):
                super().__init__(cfg.username, cfg.key())
                self.add_event_handler('session_start', self.start)
                self.add_event_handler('message', self.message)
            async def start(self, event):
                self.send_presence()
                await self.get_roster()
                on_state('running')
            async def message(self, msg):
                sender = str(msg['from'].bare)
                if msg['type'] == 'chat' and sender in cfg.allowed and str(msg['body']):
                    text = await asyncio.to_thread(reply, service, cfg, name+':'+sender, str(msg['body']), cancelled=stop)
                    self.send_message(mto=sender, mbody=text, mtype='chat')
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        bot = Bot()
        bot.connect()
        async def watch():
            while not stop.is_set():await asyncio.sleep(.5)
            bot.disconnect(wait=0)
            loop.stop()
        loop.create_task(watch())
        loop.run_forever()
        return
    with httpx.Client(timeout=40, trust_env=False) as client:
        if cfg.type == 'telegram':
            base = 'https://api.telegram.org/bot' + cfg.key()
            r=client.get(base+'/getUpdates',params={'offset':-1,'timeout':0});r.raise_for_status()
            pending=r.json().get('result',[])
            offset=pending[-1]['update_id']+1 if pending else None
            on_state('running')
            while not stop.is_set():
                response = client.get(base + '/getUpdates', params={'offset': offset, 'timeout': 25, 'allowed_updates': '["message"]'})
                response.raise_for_status()
                body = response.json()
                if not body.get('ok'): raise ValueError('Telegram отклонил подключение')
                for update in body['result']:
                    offset = update['update_id'] + 1
                    message = update.get('message', {})
                    chat = str(message.get('chat', {}).get('id', ''))
                    if chat not in cfg.allowed or not message.get('text'): continue
                    text = reply(service, cfg, name + ':' + chat, message['text'],cancelled=stop)
                    for i in range(0, len(text), 3500):
                        client.post(base + '/sendMessage', json={'chat_id': chat, 'text': text[i:i+3500]}).raise_for_status()
        else:
            base = cfg.url.rstrip('/') + '/api/v4'
            client.headers['Authorization'] = 'Bearer ' + cfg.key()
            me = client.get(base + '/users/me'); me.raise_for_status()
            own_id = me.json()['id']
            on_state('running')
            cursors={channel:int(time.time()*1000) for channel in cfg.allowed}
            seen = set()
            while not stop.is_set():
                for channel in cfg.allowed:
                    response = client.get(base + '/channels/' + channel + '/posts', params={'since': cursors[channel]})
                    response.raise_for_status()
                    posts=sorted(response.json().get('posts', {}).values(),key=lambda p:p['create_at'])
                    for post in posts:
                        if post['id'] in seen or post['user_id'] == own_id or post.get('type') or not post.get('message'): continue
                        seen.add(post['id'])
                        post={**post,'channel_id':channel}
                        confirm=mattermost_confirmation(client,base,cfg,post,seen,stop,service.config.confirmation_timeout)
                        answer = reply(service, cfg, name + ':' + channel + ':' + post['user_id'], post['message'],confirm=confirm,cancelled=stop)
                        if stop.is_set():break
                        client.post(base + '/posts', json={'channel_id': channel, 'root_id': post.get('root_id') or post['id'], 'message': answer[:15000]}).raise_for_status()
                    if posts:cursors[channel]=max(p['create_at'] for p in posts)
                if len(seen) > 10000: seen.clear()
                stop.wait(3)
