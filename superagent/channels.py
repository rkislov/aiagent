"""Explicitly started local channel workers; deny approvals outside the web UI."""
import time
import asyncio
import hashlib
import httpx


def reply(service, config, sender, text):
    session = hashlib.sha256((config.type + ':' + sender).encode()).hexdigest()
    result = ''
    for event in service.runner.run(config.agent, text, session, confirm=lambda _: False):
        if event['type'] == 'done': result = event['content']
        elif event['type'] == 'error': result = event['error']
    return result or 'Действие не выполнено. Для подтверждения используйте веб-интерфейс.'


def run(service, name):
    cfg = service.config.channels[name]
    if not cfg.enabled or not cfg.allowed or not cfg.key():
        raise ValueError('Включите канал, задайте секрет и список разрешённых собеседников')
    service.registry.build(cfg.agent)
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
            async def message(self, msg):
                sender = str(msg['from'].bare)
                if msg['type'] == 'chat' and sender in cfg.allowed and str(msg['body']):
                    text = await asyncio.to_thread(reply, service, cfg, sender, str(msg['body']))
                    self.send_message(mto=sender, mbody=text, mtype='chat')
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        bot = Bot()
        bot.connect()
        loop.run_forever()
        return
    with httpx.Client(timeout=40, trust_env=False) as client:
        if cfg.type == 'telegram':
            base = 'https://api.telegram.org/bot' + cfg.key()
            offset = None
            while True:
                response = client.get(base + '/getUpdates', params={'offset': offset, 'timeout': 25, 'allowed_updates': '["message"]'})
                response.raise_for_status()
                body = response.json()
                if not body.get('ok'): raise ValueError('Telegram отклонил подключение')
                for update in body['result']:
                    offset = update['update_id'] + 1
                    message = update.get('message', {})
                    chat = str(message.get('chat', {}).get('id', ''))
                    if chat not in cfg.allowed or not message.get('text'): continue
                    text = reply(service, cfg, name + ':' + chat, message['text'])
                    for i in range(0, len(text), 3500):
                        client.post(base + '/sendMessage', json={'chat_id': chat, 'text': text[i:i+3500]}).raise_for_status()
        else:
            base = cfg.url.rstrip('/') + '/api/v4'
            client.headers['Authorization'] = 'Bearer ' + cfg.key()
            me = client.get(base + '/users/me'); me.raise_for_status()
            own_id = me.json()['id']
            since = int(time.time()*1000)
            seen = set()
            while True:
                for channel in cfg.allowed:
                    response = client.get(base + '/channels/' + channel + '/posts', params={'since': since})
                    response.raise_for_status()
                    for post in sorted(response.json().get('posts', {}).values(), key=lambda p:p['create_at']):
                        if post['id'] in seen or post['user_id'] == own_id or post.get('type') or not post.get('message'): continue
                        seen.add(post['id'])
                        answer = reply(service, cfg, name + ':' + channel + ':' + post['user_id'], post['message'])
                        client.post(base + '/posts', json={'channel_id': channel, 'root_id': post.get('root_id') or post['id'], 'message': answer[:15000]}).raise_for_status()
                if len(seen) > 10000: seen.clear(); since = int(time.time()*1000)
                time.sleep(3)
