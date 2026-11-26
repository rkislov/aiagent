"""Explicitly started local channel workers; deny approvals outside the web UI."""
import time
import asyncio
import hashlib
import threading
import json
import re
import secrets
import httpx


def reply(service, config, sender, text, *, confirm=None, cancelled=None, session_id=None, web_enabled=None, author=None):
    session = session_id or hashlib.sha256((config.type + ':' + sender).encode()).hexdigest()
    result = ''
    confirm = confirm or (lambda _:False)
    channel_label = {'mattermost': 'Mattermost', 'telegram': 'Telegram', 'xmpp': 'XMPP'}[config.type]
    with service.audit.as_user(f'{channel_label} · {author}' if author is not None else None):
        options = {'confirm': confirm, 'cancelled': cancelled, 'web_enabled': web_enabled}
        if config.agent:
            events = service.runner.run(config.agent, text, session, **options)
        else:
            events = service.runner.run_conversation(text, config.model, session_id=session, **options)
        for event in events:
            if event['type'] == 'done':
                result = event['content']
            elif event['type'] == 'error':
                result = event['error']
    return result or 'Действие не выполнено. Для подтверждения используйте веб-интерфейс.'


def validate_channel(service,cfg, *, require_enabled=True, config=None):
    if require_enabled and not cfg.enabled:raise ValueError('Канал выключен')
    if not cfg.key():raise ValueError('Укажите токен бота / пароль')
    if require_enabled and not cfg.allowed and not (cfg.type=='mattermost' and cfg.allow_direct_messages and cfg.owner_user_id):raise ValueError('Укажите разрешённые chat ID / channel ID / JID')
    if cfg.allow_direct_messages and (cfg.type!='mattermost' or not cfg.owner_user_id or cfg.agent):
        raise ValueError('Для общих личных чатов укажите владельца и выберите обычный чат')
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
    reads={name for _,tools in CONNECTIONS.values() for name in tools}|{'web_read','web_search','dialog_search','office_read','office_create','office_edit','office_compare','image_generate','knowledge_index','knowledge_search','knowledge_forget'}
    def confirm(event):
        if event['skill'] not in reads:return False
        code=str(secrets.randbelow(900)+100)
        root=original.get('root_id') or original['id']
        message='Разрешить действие **'+event['skill']+'**?\n```json\n'+json.dumps(event.get('preview',event['input']),ensure_ascii=False,indent=2)+'\n```\nДля подтверждения ответьте только тремя цифрами: **'+code+'**. Для отказа: **ОТМЕНА '+code+'**. Код действует '+str(timeout)+' с; после 3 неверных попыток запрос отменяется.'
        r=client.post(base+'/posts',json={'channel_id':original['channel_id'],'root_id':root,'message':message});r.raise_for_status()
        since=r.json().get('create_at',int(time.time()*1000))
        deadline=time.monotonic()+timeout
        attempts=0
        while not stop.is_set() and time.monotonic()<deadline:
            r=client.get(base+'/channels/'+original['channel_id']+'/posts',params={'since':since});r.raise_for_status()
            for post in sorted(r.json().get('posts',{}).values(),key=lambda p:p.get('create_at',0)):
                if post['id'] in seen or post['user_id']!=original['user_id'] or post.get('channel_id',original['channel_id'])!=original['channel_id'] or post.get('create_at',0)<since:continue
                answer=post.get('message','').strip().casefold()
                match=re.fullmatch(r'(?:(разрешаю|отмена)\s+)?([0-9]{3})',answer)
                if not match:continue
                seen.add(post['id'])
                if match[2]==code:return match[1]!='отмена'
                attempts+=1
                if attempts>=3:return False
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
                    text = await asyncio.to_thread(reply, service, cfg, name+':'+sender, str(msg['body']), cancelled=stop, author=sender)
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
                    author = (message.get('sender_chat') or message.get('from') or {}).get('id')
                    text = reply(service, cfg, name + ':' + chat, message['text'], cancelled=stop, author=author)
                    for i in range(0, len(text), 3500):
                        client.post(base + '/sendMessage', json={'chat_id': chat, 'text': text[i:i+3500]}).raise_for_status()
        else:
            base = cfg.url.rstrip('/') + '/api/v4'
            client.headers['Authorization'] = 'Bearer ' + cfg.key()
            me = client.get(base + '/users/me'); me.raise_for_status()
            own_id = me.json()['id']
            register_commands(client, base, cfg)
            on_state('running')
            started_at=int(time.time()*1000)
            cursors={channel:started_at for channel in cfg.allowed}
            seen = set()
            while not stop.is_set():
                channels = list(cfg.allowed)
                if cfg.allow_direct_messages:
                    joined=client.get(base+'/users/me/channels');joined.raise_for_status()
                    channels = list(dict.fromkeys(channels+[row['id'] for row in joined.json() if row.get('type')=='D']))
                for channel in channels:
                    cursors.setdefault(channel,started_at)
                    response = client.get(base + '/channels/' + channel + '/posts', params={'since': cursors[channel]})
                    response.raise_for_status()
                    posts=sorted(response.json().get('posts', {}).values(),key=lambda p:p['create_at'])
                    for post in posts:
                        if post['id'] in seen or post['user_id'] == own_id or post.get('type') or not post.get('message'): continue
                        seen.add(post['id'])
                        post={**post,'channel_id':channel}
                        confirm=mattermost_confirmation(client,base,cfg,post,seen,stop,service.config.confirmation_timeout)
                        answer = mattermost_reply(service, name, cfg, post, confirm=confirm, cancelled=stop)
                        if stop.is_set():break
                        client.post(base + '/posts', json={'channel_id': channel, 'root_id': post.get('root_id') or post['id'], 'message': answer[:15000], 'props':command_buttons(cfg)}).raise_for_status()
                    if posts:cursors[channel]=max(p['create_at'] for p in posts)
                if len(seen) > 10000: seen.clear()
                stop.wait(3)


HELP = ('**Команды помощника**\n/ai help — помощь\n/ai new — новый диалог\n'
        '/ai chats [текст] — список и поиск своих диалогов\n/ai use ID — продолжить свой диалог\n'
        '/ai web on|off|status — веб-поиск и чтение страниц\n'
        '/ai knowledge list|off|use ID[,ID] — личные базы знаний')


def register_commands(client, base, cfg):
    # TMS mailbox commands do not require an admin role or an external callback URL.
    teams=client.get(base+'/users/me/teams');teams.raise_for_status()
    for team in teams.json():
        response=client.post(base+'/commands',json={'team_id':team['id'],'trigger':cfg.command_trigger,
            'auto_complete':True,'auto_complete_desc':'Помощник: диалоги и веб',
            'auto_complete_hint':'help | new | chats [поиск] | use ID | web on/off/status',
            'display_name':'AI помощник','tms_bot_command':True})
        # Original Mattermost may not implement this transport; keep plain DM working.
        if response.status_code not in (403,404,405,501): response.raise_for_status()


def mattermost_reply(owner, name, cfg, post, *, confirm=None, cancelled=None):
    from .channel_context import user_service, identity_key, Dialogs
    service=user_service(owner,name,cfg,post['user_id'])
    identity=identity_key(name,cfg,post['user_id'])
    dialogs=Dialogs(service,identity)
    sender=name+':'+post['channel_id']+':'+post['user_id']
    initial=hashlib.sha256((cfg.type+':'+sender).encode()).hexdigest() if service is owner else None
    session=dialogs.current(initial)
    text=post['message'].strip()
    prefix='/'+cfg.command_trigger
    if text==prefix or text.startswith(prefix+' '):
        command,_,argument=text[len(prefix):].strip().partition(' ')
        argument=argument.strip()
        if command in ('','help'):return HELP.replace('/ai ',prefix+' ')
        if command=='new':return 'Создан новый диалог: `'+dialogs.new()+'`. Веб выключен.'
        if command=='chats':
            rows=dialogs.list(argument)
            return ('**Ваши диалоги**\n'+'\n'.join('`'+row['id']+'` — '+row['title'].replace('\n',' ') for row in rows)) if rows else 'Своих диалогов по запросу не найдено.'
        if command=='use':
            try: dialogs.use(argument)
            except ValueError:return 'Свой диалог с таким ID не найден.'
            return 'Выбран диалог `'+argument+'`. Веб '+('включён' if service.runner.conversation_memory().web(argument) else 'выключен')+'.'
        if command=='web':
            if argument not in ('on','off','status',''):return 'Используйте '+prefix+' web on|off|status'
            enabled=service.runner.conversation_memory().web(session, {'on':True,'off':False}.get(argument))
            return 'Веб '+('включён' if enabled else 'выключен')+' для текущего диалога.'
        if command=='knowledge':
            from .integrations.knowledge import Knowledge
            store=Knowledge(service.root)
            action,_,ids=argument.partition(' ')
            memory=service.runner.memory(service.registry.build(cfg.agent)) if cfg.agent else service.runner.conversation_memory()
            if action=='list':return '**Ваши базы знаний**\n'+'\n'.join('`'+row['id']+'` — '+row['name'] for row in store.collections())
            if action=='off':memory.knowledge(session,[]);return 'Базы знаний отключены для текущего диалога.'
            if action=='use':
                try:selected=store.validate_collections([value.strip() for value in ids.split(',') if value.strip()])
                except ValueError:return 'База не найдена в вашем пространстве. Используйте '+prefix+' knowledge list'
                memory.knowledge(session,selected)
                return 'Подключено баз знаний: '+str(len(selected))+'.'
            return 'Используйте '+prefix+' knowledge list|off|use ID[,ID]'
        return HELP.replace('/ai ',prefix+' ')
    return reply(service, cfg, sender, text, session_id=session, confirm=confirm, cancelled=cancelled, author=post['user_id'])



def command_buttons(cfg):
    actions=[]
    for key,label,command in [('help','Помощь','help'),('new','Новый диалог','new'),('chats','Мои диалоги','chats'),('web_on','Веб вкл.','web on'),('web_off','Веб выкл.','web off')]:
        actions.append({'id':'ai_'+key,'name':label,'type':'button','integration':{
            'url':cfg.url.rstrip('/')+'/api/v4/tms/bot-command',
            'context':{'command':'/'+cfg.command_trigger+' '+command}}})
    return {'attachments':[{'actions':actions}]}
