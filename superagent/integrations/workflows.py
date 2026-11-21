"""Read-only workflow helpers. Task dispatch returns a proposal, never a write."""
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .task_writes import validate_fields

WORKFLOWS={'task_dispatch','stalled_tasks','release_notes'}


def project_name(value):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value):raise ValueError('Укажите точный ключ/ID проекта')
    return value


def reference(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00')) if value else datetime.now(ZoneInfo('Europe/Moscow'))
    if result.tzinfo is None:raise ValueError('as_of должен включать часовой пояс')
    return result


def timestamp(value):
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return parsed if parsed.tzinfo else None
    except ValueError:return None


class Workflows:
    def task_dispatch(self,p):
        service=p['service']
        if service not in ('jira','redmine','bitrix24'):raise ValueError('service: jira, redmine или bitrix24')
        if not p['message'].strip() or len(p['message'])>20000:raise ValueError('Нужно сообщение длиной до 20000 символов')
        summary=p['summary'].strip() or p['message'].strip().splitlines()[0][:200]
        fields={};missing=[]
        if service=='jira':
            fields={'summary':summary,'description':p['message']}
            if p['project']:fields['project_key']=project_name(p['project'])
            else:missing.append('project_key')
            if p['issue_type_id']:fields['issue_type_id']=p['issue_type_id']
            else:missing.append('issue_type_id')
            if p['assignee']:fields['assignee_id']=p['assignee']
            if p['due_date']:fields['due_date']=p['due_date']
            skill='jira_create_issue'
        elif service=='redmine':
            fields={'subject':summary,'description':p['message']}
            if p['project']:fields['project_id']=project_name(p['project'])
            else:missing.append('project_id')
            if p['assignee']:fields['assigned_to_id']=p['assignee']
            if p['due_date']:fields['due_date']=p['due_date']
            skill='redmine_create_issue'
        else:
            fields={'title':summary,'description':p['message']}
            if p['project']:fields['group_id']=int(project_name(p['project']))
            if p['assignee']:fields['responsible_id']=p['assignee']
            else:missing.append('responsible_id')
            if p['due_date']:fields['deadline']=p['due_date']
            skill='bitrix24_create_task'
        if not missing:validate_fields(service,fields,True)
        return {'proposal':{'skill':skill,'input':{'fields':fields},'ready':not missing,'missing_fields':missing,'date_candidates':re.findall(r'\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)\b',p['message']),'assignee_is_suggestion':True,'needs_confirmation':True,'saved':False}}

    def _workflow_tasks(self,service,project,limit,release=None):
        if service not in ('jira','redmine'):raise ValueError('Этот сценарий поддерживает Jira и Redmine')
        project_name(project)
        if not 1<=limit<=200:raise ValueError('limit: 1–200')
        rows=[];cursor='';offset=0;seen=set();more=False
        while len(rows)<limit:
            batch=min(50,limit-len(rows))
            if service=='jira':
                clause='statusCategory = Done' if release is not None else 'statusCategory != Done'
                jql=f'project = {json.dumps(project)} AND {clause}'
                if release is not None:jql+=' AND fixVersion = '+json.dumps(release)
                jql+=' ORDER BY updated ASC'
                result=self.run('jira_search',{'jql':jql,'limit':batch,'cursor':cursor})
                new=result['issues'];next_cursor=result['next_cursor'];more=bool(next_cursor)
                if next_cursor in seen and more:raise ValueError('Сервис повторил курсор пагинации')
                seen.add(next_cursor);cursor=next_cursor
            else:
                filters={'project_id':project,'status_id':'closed' if release is not None else 'open'}
                if release is not None:
                    if not release.isdigit():raise ValueError('Для Redmine release — числовой fixed_version_id')
                    filters['fixed_version_id']=release
                result=self.run('redmine_issues',{'filters':filters,'limit':batch,'offset':offset})
                new=result['issues'];offset+=len(new);more=offset<result['total_count']
            rows.extend(new)
            if not more or not new:break
        return rows,more

    def stalled_tasks(self,p):
        if not 1<=p['stale_days']<=365:raise ValueError('stale_days: 1–365')
        now=reference(p['as_of']);cutoff=now-timedelta(days=p['stale_days'])
        rows,truncated=self._workflow_tasks(p['service'],p['project'],p['limit'])
        flagged=[];unknown=[]
        for row in rows:
            if row.get('status_category')=='done' or row.get('closed_on'):continue
            reasons=[]
            updated=timestamp(row.get('updated',''))
            if updated and updated<=cutoff:reasons.append('no_updates')
            due=row.get('due_date','')
            if due:
                try:
                    if datetime.fromisoformat(str(due).replace('Z','+00:00')).date()<now.date():reasons.append('overdue')
                except ValueError:pass
            if not updated:unknown.append(row.get('key',row.get('id')))
            if reasons:flagged.append({**row,'reasons':reasons})
        return {'stalled':{'service':p['service'],'project':p['project'],'as_of':now.isoformat(),'stale_days':p['stale_days'],'examined':len(rows),'truncated':truncated,'tasks':flagged,'unknown_update_time':unknown,'saved':False}}

    def release_notes(self,p):
        if not p['release'].strip() or len(p['release'])>200:raise ValueError('Укажите версию релиза длиной до 200 символов')
        rows,truncated=self._workflow_tasks(p['service'],p['project'],p['limit'],p['release'])
        groups={}
        for row in rows:
            kind=row.get('issue_type') or 'Изменения'
            groups.setdefault(kind,[]).append(row)
        lines=[f"# Релиз {p['release']}",f"Проект: {p['project']} · источник: {p['service']}",'','Черновик для проверки. Выборка включает только закрытые задачи выбранной версии.','']
        for kind,items in sorted(groups.items()):
            lines.extend(['## '+kind,''])
            for row in items:
                title=str(row.get('summary',row.get('subject',''))).replace('\n',' ')
                identity=str(row.get('key',row.get('id','')))
                # Escape source text so it cannot inject Markdown links/sections.
                for char in ('\\','[',']','*','_','`','<','>'):title=title.replace(char,'\\'+char)
                lines.append(f"- [{identity}]({row['url']}) — {title}")
            lines.append('')
        if not rows:lines.append('Закрытых задач выбранной версии в выборке нет.')
        if truncated:lines.append('Выборка неполная: достигнут лимит. Увеличьте limit (максимум 200).')
        return {'notes':{'draft':True,'markdown':'\n'.join(lines),'tasks':rows,'examined':len(rows),'truncated':truncated,'published':False}}
