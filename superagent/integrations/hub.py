import imaplib
import json
import ssl
import re
from datetime import datetime
from email import policy
from email.parser import BytesParser

import httpx
import feedparser
import caldav
from .catalog import BUILTINS, CONNECTION_FOR
from .work_tools import WorkTools, WORK_TOOLS
from .workflows import Workflows, WORKFLOWS
from superagent.gateway.models import validate_payload


def plain(value):
    return re.sub('<[^>]+>', '', str(value))[:12000]


class Hub(WorkTools,Workflows):
    def __init__(self, config, *, root=None, registry=None,knowledge_scope=None):
        self.config, self.root, self.registry = config, root, registry
        self.knowledge_scope=None if knowledge_scope is None else tuple(knowledge_scope)

    def prepare_write(self,name,payload, *, model=None):
        from .task_writes import WritePlan, WRITE_TOOLS
        if name not in WRITE_TOOLS:raise ValueError('Неизвестная операция записи')
        validate_payload(payload,BUILTINS[name].inputs)
        if name in ('schedule_save','schedule_control'):
            from .schedule_tools import SchedulePlan
            return SchedulePlan(self,name,payload,model=model)
        snapshot=Hub(self.config.model_copy(deep=True))
        if name=='confluence_publish_release':
            from .confluence_publish import ConfluencePublishPlan
            return ConfluencePublishPlan(snapshot,json.loads(json.dumps(payload)))
        return WritePlan(snapshot,name,json.loads(json.dumps(payload)))

    def run(self, name, payload):
        from .task_writes import WRITE_TOOLS
        if name in WRITE_TOOLS:raise PermissionError('Запись выполняется только через подтверждённый план')
        validate_payload(payload, BUILTINS[name].inputs)
        from .security_audit import SECURITY_TOOLS, run_security
        if name in SECURITY_TOOLS:
            return run_security(name, payload)
        if name=='image_generate':
            from .model_media import generate_image
            if self.root is None:raise ValueError('Нужно личное пространство')
            return generate_image(self.root,self.config,payload)
        from .knowledge import KNOWLEDGE_TOOLS,run_knowledge
        if name in KNOWLEDGE_TOOLS:
            if self.root is None:raise ValueError('Нужно личное пространство')
            return run_knowledge(self.root,self.config,name,payload,self.knowledge_scope)
        from .office import OFFICE_TOOLS,run_office
        if name in OFFICE_TOOLS:
            if self.root is None:raise ValueError('Нужно личное пространство документов')
            return run_office(self.root,name,payload)
        if name == 'dialog_search':
            if self.root is None or self.registry is None:raise ValueError('Поиск диалогов доступен только в пространстве пользователя')
            from superagent.core.dialog_search import search_dialogs
            return search_dialogs(self.root,self.registry,payload['query'],payload['limit'])
        if name == 'schedule_list':
            from .schedule_tools import list_schedules
            return list_schedules(self)
        if name == 'web_read':
            from .web_read import read_page
            return read_page(payload['url'])
        if name in WORKFLOWS:return getattr(self,name)(payload)
        if name in ('commitment_scan','invoice_check','reply_drafts'):
            return getattr(self,name)(payload)
        if name in ('morning_digest','meeting_prep','topic_monitor'):
            return self.composite(name, payload)
        cfg = getattr(self.config.connections, CONNECTION_FOR[name])
        if not cfg.enabled:
            raise ValueError('Подключение выключено. Настройте его в разделе «Подключения».')
        limit = payload.get('limit',1)
        if not 1 <= limit <= (50 if name in WORK_TOOLS else 100 if name in ('caldav_read','one_c_read') else 30 if name == 'rss_read' else 20 if name == 'imap_read' else 10):
            raise ValueError('Недопустимый limit')
        try:
            return getattr(self, name)(cfg, payload)
        except ValueError:
            raise
        except Exception:
            raise ValueError('Ошибка подключения: проверьте адрес, учётные данные и доступность сервера') from None

    def web_search(self, cfg, p):
        if not p['query'].strip() or len(p['query']) > 2000:
            raise ValueError('Нужен запрос длиной до 2000 символов')
        with httpx.Client(timeout=20, trust_env=False) as c:
            if cfg.engine == 'tavily':
                r = c.post(cfg.base_url + '/search', headers={'Authorization': 'Bearer ' + cfg.key()}, json={'query': p['query'], 'max_results': p['limit']})
            elif cfg.engine == 'brave':
                r = c.get(cfg.base_url + '/res/v1/web/search', headers={'X-Subscription-Token': cfg.key()}, params={'q': p['query'], 'count': p['limit']})
            else:
                r = c.get(cfg.base_url + '/search', params={'q': p['query'], 'format': 'json'})
            r.raise_for_status()
            data = r.json()
        rows = data.get('web', {}).get('results', []) if cfg.engine == 'brave' else data.get('results', [])
        return {'results': [{'title': plain(x.get('title', '')), 'url': x.get('url', ''), 'content': plain(x.get('content', x.get('description', '')))} for x in rows[:p['limit']]]}

    def rss_read(self, cfg, p):
        feeds = [f for f in cfg.feeds if not p['feed'] or f.name == p['feed']]
        if not feeds:
            raise ValueError('Лента не настроена')
        entries = []
        with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
            for feed in feeds:
                with client.stream('GET', feed.url) as response:
                    response.raise_for_status()
                    chunks = bytearray()
                    for chunk in response.iter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > 2_000_000:
                            raise ValueError('Лента превышает 2 MB')
                parsed = feedparser.parse(bytes(chunks))
                if parsed.bozo and not parsed.entries:
                    raise ValueError('Некорректная RSS/Atom-лента')
                entries.extend({'feed': feed.name, 'title': plain(e.get('title', '')), 'url': e.get('link', ''), 'published': e.get('published', e.get('updated', '')), 'text': plain(e.get('summary', ''))} for e in parsed.entries[:p['limit']])
        return {'entries': entries}

    def imap_read(self, cfg, p):
        if p['folder'] not in cfg.folders or any(c in p['query'] for c in '\r\n\x00') or len(p['query']) > 512:
            raise ValueError('Папка не разрешена или поисковый запрос некорректен')
        context = ssl.create_default_context()
        if cfg.security == 'tls':
            connection = imaplib.IMAP4_SSL(cfg.host, cfg.port, ssl_context=context, timeout=20)
        else:
            connection = imaplib.IMAP4(cfg.host, cfg.port, timeout=20)
            connection.starttls(ssl_context=context)
        with connection as mailbox:
            mailbox.login(cfg.username, cfg.key())
            status, _ = mailbox.select('"' + p['folder'].replace('"', '\\"') + '"', readonly=True)
            if status != 'OK':
                raise ValueError('Не удалось открыть папку')
            status, data = mailbox.uid('search', None, p['query'] or 'ALL')
            if status != 'OK':
                raise ValueError('IMAP отклонил критерии поиска')
            messages = []
            for uid in reversed(data[0].split()[-p['limit']:]):
                status, parts = mailbox.uid('fetch', uid, '(BODY.PEEK[]<0.131072>)')
                if status != 'OK':
                    continue
                raw = b''.join(part[1] for part in parts if isinstance(part, tuple))
                message = BytesParser(policy=policy.default).parsebytes(raw)
                body = message.get_body(preferencelist=('plain', 'html')) if message.is_multipart() else message
                try:
                    text = plain(body.get_content()) if body else ''
                except Exception:
                    text = ''
                messages.append({'uid': uid.decode(), 'subject': str(message.get('subject', '')), 'from': str(message.get('from', '')), 'date': str(message.get('date', '')), 'body': text})
        return {'messages': messages}

    def caldav_read(self, cfg, p):
        start, end = datetime.fromisoformat(p['start']), datetime.fromisoformat(p['end'])
        if not start.tzinfo or not end.tzinfo or not 0 < (end-start).total_seconds() <= 93*86400:
            raise ValueError('Нужен интервал до 93 дней с часовым поясом')
        events = []
        with caldav.DAVClient(url=cfg.url, username=cfg.username, password=cfg.key(), timeout=20, ssl_verify_cert=True) as client:
            calendars = client.principal().calendars()
            for calendar in calendars:
                name, url = str(calendar.name), str(calendar.url)
                if cfg.calendars and name not in cfg.calendars and url not in cfg.calendars:
                    continue
                if p['calendar'] and p['calendar'] not in (name, url):
                    continue
                for item in calendar.search(start=start, end=end, event=True, expand=True):
                    for component in item.icalendar_instance.walk('VEVENT'):
                        events.append({'calendar': name, 'summary': str(component.get('SUMMARY', '')), 'start': str(component.decoded('DTSTART')), 'end': str(component.decoded('DTEND')) if component.get('DTEND') else '', 'description': plain(component.get('DESCRIPTION', '')), 'uid': str(component.get('UID', ''))})
                        if len(events) >= p['limit']:
                            return {'events': events}
        return {'events': events}

    def composite(self, name, p):
        if not 1 <= p['limit'] <= 20: raise ValueError('limit должен быть от 1 до 20')
        out = {}
        cfg = self.config.connections
        if name != 'topic_monitor' and cfg.imap.enabled:
            out.update(self.run('imap_read',{'folder':cfg.imap.folders[0],'query':'UNSEEN' if name=='morning_digest' else 'ALL','limit':p['limit']}))
        if name != 'topic_monitor' and cfg.caldav.enabled:
            out.update(self.run('caldav_read',{'start':p['start'],'end':p['end'],'calendar':'','limit':p['limit']}))
        if name != 'meeting_prep' and cfg.rss.enabled:
            out.update(self.run('rss_read',{'feed':'','limit':p['limit']}))
        if name == 'topic_monitor':
            if cfg.search.enabled: out.update(self.run('web_search',{'query':p['query'],'limit':min(10,p['limit'])}))
            if 'entries' in out:
                query=p['query'].casefold()
                out['entries']=[e for e in out['entries'] if query in (e['title']+' '+e['text']).casefold()]
        if not out: raise ValueError('Сначала включите нужные источники в подключениях')
        return {'digest':out}

    def ssh_run(self, cfg, p):
        import paramiko
        import hashlib
        import base64
        import time
        if p['command'] not in cfg.commands or not cfg.host_key_sha256:
            raise ValueError('Команда не разрешена либо не задан проверенный SHA256 ключ сервера')
        expected=cfg.host_key_sha256
        class PinnedKey(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                actual='SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
                if not __import__('secrets').compare_digest(actual,expected):
                    raise ValueError('SSH: ключ сервера не совпадает с настроенным')
        with paramiko.SSHClient() as client:
            client.set_missing_host_key_policy(PinnedKey())
            client.connect(cfg.host,port=cfg.port,username=cfg.username,password=cfg.key(),look_for_keys=False,allow_agent=False,timeout=15,auth_timeout=15,banner_timeout=15)
            channel=client.get_transport().open_session(timeout=10)
            channel.settimeout(20)
            channel.exec_command(p['command'])
            out,err=bytearray(),bytearray()
            deadline=time.monotonic()+30
            try:
                while True:
                    if time.monotonic()>deadline: raise ValueError('SSH: таймаут команды; удалённый процесс мог продолжить работу')
                    if channel.recv_ready(): out.extend(channel.recv(16384))
                    if channel.recv_stderr_ready(): err.extend(channel.recv_stderr(16384))
                    if len(out)+len(err)>131072: raise ValueError('SSH: вывод превышает 128 KB')
                    if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready(): break
                    time.sleep(.02)
                return {'exit_code':channel.recv_exit_status(),'stdout':out.decode(errors='replace'),'stderr':err.decode(errors='replace')}
            finally: channel.close()

    def commitment_scan(self,p):
        if len(p['text'])>100000: raise ValueError('Текст превышает 100000 символов')
        candidates=[]
        for sentence in re.split(r'(?<=[.!?])\s+|\n+',p['text']):
            if re.search(r'обещ|пришл|сдела|подготов|отправ|до \d|will |deadline|срок',sentence,re.I):
                dates=re.findall(r'\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)\b',sentence)
                candidates.append({'text':sentence,'explicit_dates':dates,'needs_review':True})
        return {'commitments':candidates[:100]}

    def invoice_check(self,p):
        from decimal import Decimal,InvalidOperation
        from datetime import date
        if len(p['invoices'])>1000: raise ValueError('Не более 1000 счетов')
        seen=set();issues=[]
        for index,invoice in enumerate(p['invoices']):
            identity=(str(invoice.get('vendor','')).casefold(),str(invoice.get('number','')),str(invoice.get('currency','')))
            if identity[0] and identity[1]:
                if identity in seen: issues.append({'index':index,'issue':'possible_duplicate','identity':list(identity)})
                seen.add(identity)
            try:
                amount=Decimal(str(invoice.get('amount','')))
                if not amount.is_finite() or amount<=0: raise InvalidOperation()
            except (InvalidOperation,ValueError): issues.append({'index':index,'issue':'invalid_amount'})
            if invoice.get('due_date'):
                try:
                    if date.fromisoformat(str(invoice['due_date'])) < date.today() and not invoice.get('paid',False):
                        issues.append({'index':index,'issue':'overdue'})
                except ValueError: issues.append({'index':index,'issue':'invalid_due_date'})
        return {'issues':issues}

    def reply_drafts(self,p):
        if len(p['messages'])>100: raise ValueError('Не более 100 писем')
        return {'drafts':[{'to':str(m.get('from','')),'subject':'Re: '+str(m.get('subject','')),'body':'Спасибо за письмо! Ваш запрос получен. Мы изучим детали и вернёмся с ответом.','template':True,'sent':False} for m in p['messages']]}

    def one_c_read(self,cfg,p):
        if p['entity'] not in cfg.entities or not re.fullmatch(r'[^/?.\s]+',p['entity']):
            raise ValueError('Коллекция 1С не входит в разрешённый список')
        if len(p['filter'])>2000 or len(p['select'])>1000:
            raise ValueError('Слишком длинный запрос')
        if p['select'] and not re.fullmatch(r'[\w, ]+',p['select']):
            raise ValueError('Укажите простые имена полей через запятую')
        from urllib.parse import quote
        params={'$format':'json','$top':p['limit']}
        if p['filter']:params['$filter']=p['filter']
        if p['select']:params['$select']=p['select']
        with httpx.Client(timeout=20,trust_env=False,follow_redirects=False,auth=(cfg.username,cfg.key())) as client:
            with client.stream('GET',cfg.url.rstrip('/')+'/'+quote(p['entity'],safe=''),params=params) as response:
                response.raise_for_status()
                raw=bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw)>2_000_000:raise ValueError('Ответ 1С превышает 2 MB; уменьшите limit или select')
            import json
            data=json.loads(raw)
        records=data.get('value',data.get('d',{}).get('results',[]) if isinstance(data.get('d'),dict) else [])
        if not isinstance(records,list) or any(not isinstance(r,dict) for r in records): raise ValueError('Некорректный ответ OData')
        return {'records':records[:p['limit']]}
