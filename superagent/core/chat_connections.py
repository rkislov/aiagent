"""Read-only tools exposed by the current user's enabled connections."""
CONNECTIONS = {
    'imap': ('Почта IMAP', ['imap_read']),
    'caldav': ('Календарь CalDAV', ['caldav_read']),
    'rss': ('RSS-ленты', ['rss_read']),
    'one_c': ('1С OData', ['one_c_read']),
    'jira': ('Jira', ['jira_search', 'jira_issue']),
    'confluence': ('Confluence', ['confluence_search', 'confluence_page']),
    'redmine': ('Redmine', ['redmine_issues', 'redmine_issue']),
    'bitrix24': ('Bitrix24', ['bitrix24_tasks', 'bitrix24_task', 'bitrix24_leads']),
}


def available(config):
    return [{'id': name, 'title': title, 'enabled': bool(getattr(config.connections, name).enabled), 'tools': tools[:]} for name, (title, tools) in CONNECTIONS.items()]


def tools_for(config, selected=None):
    if selected is not None and (len(selected) > len(CONNECTIONS) or len(set(selected)) != len(selected) or any(name not in CONNECTIONS for name in selected)):
        raise ValueError('Некорректный список подключений чата')
    names = list(CONNECTIONS) if selected is None else selected
    return [tool for name in names if getattr(config.connections, name).enabled for tool in CONNECTIONS[name][1]]
