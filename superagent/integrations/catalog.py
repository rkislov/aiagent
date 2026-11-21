from superagent.gateway.models import Skill


BUILTINS = {
    s.name: s for s in [
        Skill(name="web_search", description="Поиск в интернете через настроенный Tavily, Brave или SearXNG. Возвращает заголовки, URL и фрагменты. query — поисковый запрос, limit от 1 до 10.", inputs={"query": "str", "limit": "int"}, outputs={"results": "list[dict]"}),
        Skill(name="imap_read", description="Читает письма через настроенный IMAP без изменения флага прочтения. folder — разрешённая папка (обычно INBOX); query — ALL, UNSEEN либо стандартные критерии IMAP; limit от 1 до 20.", inputs={"folder": "str", "query": "str", "limit": "int"}, outputs={"messages": "list[dict]"}),
        Skill(name="caldav_read", description="Читает события настроенного CalDAV-календаря. start и end — ISO 8601 с часовым поясом; интервал до 93 дней. calendar — имя/URL или пустая строка для всех разрешённых календарей. limit от 1 до 100.", inputs={"start": "str", "end": "str", "calendar": "str", "limit": "int"}, outputs={"events": "list[dict]"}),
        Skill(name="rss_read", description="Читает настроенные RSS/Atom-ленты. feed — имя из настроек, пустая строка для всех лент; limit — число записей на ленту от 1 до 30. Возвращает ссылки, заголовки, даты и текст.", inputs={"feed": "str", "limit": "int"}, outputs={"entries": "list[dict]"}),
    ]
}

EXAMPLES = {
    "web_search": {"query": "новости Python", "limit": 5},
    "imap_read": {"folder": "INBOX", "query": "UNSEEN", "limit": 5},
    "caldav_read": {"start": "2026-11-20T00:00:00+03:00", "end": "2026-11-27T00:00:00+03:00", "calendar": "", "limit": 20},
    "rss_read": {"feed": "", "limit": 10},
}

CONNECTION_FOR = {"web_search": "search", "imap_read": "imap", "caldav_read": "caldav", "rss_read": "rss"}

BUILTINS['ssh_run'] = Skill(name='ssh_run', description='Выполняет точную команду из списка разрешённых на настроенном SSH-сервере. Всегда требует подтверждения.', inputs={'command':'str'}, outputs={'exit_code':'int','stdout':'str','stderr':'str'})
for name, description in [('morning_digest','Утренний дайджест писем, календаря и RSS'),('meeting_prep','Подготовка к встречам: события и связанные письма'),('topic_monitor','Мониторинг темы в веб-поиске и RSS')]:
    inputs={'query':'str','limit':'int'} if name=='topic_monitor' else {'start':'str','end':'str','limit':'int'}
    BUILTINS[name]=Skill(name=name,description=description,inputs=inputs,outputs={'digest':'dict'})
CONNECTION_FOR['ssh_run']='ssh'
for name,desc,inputs,outputs in [
    ('commitment_scan','Выделяет предложения с обещаниями и явными датами; кандидаты требуют проверки человеком.',{'text':'str'},{'commitments':'list[dict]'}),
    ('invoice_check','Проверяет переданные счета: дубли, суммы и просроченные сроки. Не оплачивает счета.',{'invoices':'list[dict]'},{'issues':'list[dict]'}),
    ('reply_drafts','Готовит шаблонные черновики ответов по переданным письмам. Не отправляет сообщения.',{'messages':'list[dict]'},{'drafts':'list[dict]'})]:
    BUILTINS[name]=Skill(name=name,description=desc,inputs=inputs,outputs=outputs)

BUILTINS['one_c_read'] = Skill(name='one_c_read',description='Читает разрешённую коллекцию 1С OData. Только GET, без записи и проведения документов. entity — точное имя из настроек, filter — выражение OData либо пусто, select — поля через запятую либо пусто, limit от 1 до 100.',inputs={'entity':'str','filter':'str','select':'str','limit':'int'},outputs={'records':'list[dict]'})
CONNECTION_FOR['one_c_read']='one_c'

for name,connection,desc,inputs,outputs in [
    ('jira_search','jira','Поиск задач Jira по JQL. cursor пустой для первой страницы; затем next_cursor из результата. Только чтение.',{'jql':'str','limit':'int','cursor':'str'},{'issues':'list[dict]','next_cursor':'str'}),
    ('jira_issue','jira','Читает задачу Jira по ключу или ID. Только чтение.',{'issue':'str'},{'issue':'dict'}),
    ('confluence_search','confluence','Поиск контента Confluence по CQL, постранично с offset. Только чтение.',{'cql':'str','limit':'int','offset':'int'},{'pages':'list[dict]','next_offset':'int'}),
    ('confluence_page','confluence','Читает страницу Confluence по числовому page_id.',{'page_id':'str'},{'page':'dict'}),
    ('redmine_issues','redmine','Задачи Redmine. filters: project_id,status_id,assigned_to_id,tracker_id,subject. Пагинация offset. Только чтение.',{'filters':'dict','limit':'int','offset':'int'},{'issues':'list[dict]','total_count':'int'}),
    ('redmine_issue','redmine','Читает задачу Redmine по числовому ID, включая журнал изменений.',{'issue_id':'int'},{'issue':'dict'}),
    ('bitrix24_tasks','bitrix24','Читает задачи Bitrix24. group_id=0 — все доступные группы, start=0 — первая страница, затем next_start.',{'group_id':'int','limit':'int','start':'int'},{'tasks':'list[dict]','next_start':'int'}),
    ('bitrix24_task','bitrix24','Читает задачу Bitrix24 по ID.',{'task_id':'int'},{'task':'dict'}),
    ('bitrix24_leads','bitrix24','Читает лиды Bitrix24 CRM при включённом crm_enabled; start — пагинация.',{'limit':'int','start':'int'},{'leads':'list[dict]','next_start':'int'})]:
    BUILTINS[name]=Skill(name=name,description=desc,inputs=inputs,outputs=outputs)
    CONNECTION_FOR[name]=connection

for name,service,inputs in [
    ('jira_create_issue','jira',{'fields':'dict'}),('jira_update_issue','jira',{'issue_key':'str','fields':'dict'}),
    ('redmine_create_issue','redmine',{'fields':'dict'}),('redmine_update_issue','redmine',{'issue_id':'int','fields':'dict'}),
    ('bitrix24_create_task','bitrix24',{'fields':'dict'}),('bitrix24_update_task','bitrix24',{'task_id':'int','fields':'dict'})]:
    BUILTINS[name]=Skill(name=name,description='Создаёт задачу' if '_create_' in name else 'Изменяет указанные поля задачи',inputs=inputs,outputs={'saved':'bool','id':'str','url':'str'},irreversible=True)
    CONNECTION_FOR[name]=service
BUILTINS['jira_create_issue'].description += '. fields: project_key, issue_type_id, summary обязательны; description, assignee_id, priority_id, due_date YYYY-MM-DD, labels необязательны. Только после подтверждения.'
BUILTINS['jira_update_issue'].description += '. fields: summary, description, assignee_id, priority_id, due_date YYYY-MM-DD, labels. Статус требует отдельного перехода и здесь не поддерживается. Только после подтверждения.'
for name in ('redmine_create_issue','redmine_update_issue'):
    BUILTINS[name].description += '. fields: subject, description, tracker_id, status_id, priority_id, assigned_to_id, due_date, start_date, done_ratio, notes. При создании обязательны project_id и subject. Только после подтверждения.'
for name in ('bitrix24_create_task','bitrix24_update_task'):
    BUILTINS[name].description += '. fields: title, description, responsible_id, group_id, deadline ISO с часовым поясом, priority 0..2. При создании обязательны title и responsible_id. Только после подтверждения.'

BUILTINS['task_dispatch']=Skill(name='task_dispatch',description='Готовит предложение задачи из письма/сообщения без записи. service: jira/redmine/bitrix24; неизвестные project, assignee, due_date, issue_type_id, summary оставьте пустыми. Не угадывайте ID. proposal.ready=false требует уточнений. Для сохранения нужен отдельный подтверждённый create-навык.',inputs={'message':'str','service':'str','project':'str','summary':'str','assignee':'str','due_date':'str','issue_type_id':'str'},outputs={'proposal':'dict'})
BUILTINS['stalled_tasks']=Skill(name='stalled_tasks',description='Находит открытые просроченные задачи или без обновлений из одного проекта Jira/Redmine. stale_days 1–365, limit 1–200, as_of — ISO с часовым поясом или пусто (текущее время). Только чтение, неполная выборка помечается.',inputs={'service':'str','project':'str','stale_days':'int','as_of':'str','limit':'int'},outputs={'stalled':'dict'})
BUILTINS['release_notes']=Skill(name='release_notes',description='Готовит черновик release notes по закрытым задачам версии Jira/Redmine. release: точное имя/ID fixVersion Jira или числовой fixed_version_id Redmine. limit 1–200. Не публикует в Confluence.',inputs={'service':'str','project':'str','release':'str','limit':'int'},outputs={'notes':'dict'})

BUILTINS['confluence_publish_release']=Skill(name='confluence_publish_release',description='Публикует release notes Markdown после обязательного подтверждения. Для новой страницы page_id пусто, space_id — числовой ID пространства Cloud или space_key — ключ Server/Data Center; parent_id необязателен. Для обновления page_id задан, space_id/space_key/parent_id пусты: title и markdown заменяют весь заголовок и тело. Не угадывайте ID. Пустые необязательные поля задавайте строкой "". Обновление защищено версией.',inputs={'page_id':'str','space_id':'str','space_key':'str','parent_id':'str','title':'str','markdown':'str'},outputs={'saved':'bool','id':'str','url':'str'},irreversible=True)
CONNECTION_FOR['confluence_publish_release']='confluence'
