import React, {useEffect, useState} from 'react';
import {Activity, ChevronDown, History, ShieldCheck, X} from 'lucide-react';
import Progress, {Elapsed} from './Progress';
import {SkillIcon, skillLabel} from './SkillActivity';

const emptyFilters = {account: '', agent: '', skill: '', status: '', date: ''};
const pageSize = 30;
const stages = {
  starting: 'Подготавливается', model: 'Ожидает ответ модели', preparing: 'Готовит действие',
  confirmation: 'Ожидает подтверждения', skill: 'Выполняется',
};
const statuses = {
  ok: 'Готово', error: 'Ошибка', denied: 'Отклонено', cancelled: 'Отменено',
  limit: 'Лимит', interrupted: 'Прервано', needs_confirmation: 'Требуется подтверждение',
};
const eventLabel = row => row.skill ? skillLabel(row.skill) : ({
  model: 'Ответ модели', schedule: 'Запуск по расписанию', run: 'Запуск',
})[row.kind] || row.kind;
const actor = row => row.user || (row.account ? 'Пространство ' + row.account : 'Не указан');
const dateTime = value => new Date(value).toLocaleString('ru-RU', {
  day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit',
});

function RequestDetails({api, row}) {
  const [text, setText] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    const query = new URLSearchParams({account: row.account, event_id: row.id, guest: row.guest});
    api('/audit/request?' + query, {signal: controller.signal, cache: 'no-store'})
      .then(result => {if (!controller.signal.aborted) setText(result.text);})
      .catch(error => {if (!controller.signal.aborted) setError(error.message);});
    return () => controller.abort();
  }, [api, row.account, row.id, row.guest]);
  return <div className="audit-request">
    <strong>Запрос пользователя</strong>
    {error ? <p role="status">{error}</p> : text === null ? <Progress label="Загружается запрос"/> : <pre>{text || 'Запрос без текста'}</pre>}
  </div>;
}

export default function Audit({api, isAdmin, workspace, reportError}) {
  const [scope, setScope] = useState(null);
  const [filters, setFilters] = useState(emptyFilters);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [data, setData] = useState(null);
  const [expanded, setExpanded] = useState(null);
  const all = isAdmin && scope !== 'mine';

  useEffect(() => {
    const controller = new AbortController();
    let timer;
    async function load() {
      try {
        const query = new URLSearchParams({...filters, scope: all ? 'all' : 'mine', offset, limit: pageSize});
        const overview = await api('/audit/overview?' + query, {signal: controller.signal, cache: 'no-store'});
        if (!controller.signal.aborted) setData(overview);
      } catch (error) {
        if (!controller.signal.aborted) reportError(error);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(load, 4000);
      }
    }
    load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [all, filters, offset, revision, api]);

  function filter(key, value) {
    setExpanded(null);
    setOffset(0);
    setFilters(current => ({...current, [key]: value}));
  }

  function changeScope(next) {
    if (next === scope) return;
    setData(null);
    setScope(next);
    setOffset(0);
    setExpanded(null);
    setFilters(emptyFilters);
  }

  return <div className="workspace-page audit-page">
    <div className="page-heading">
      <div><h1>Журнал действий</h1><p>{all ? 'Запросы и действия всех пользователей. Нажмите на строку ответа модели, чтобы прочитать исходный запрос.' : 'Ответы моделей, вызовы навыков и ваши решения.'}</p></div>
      <button className="button secondary" aria-label="Обновить журнал" onClick={() => setRevision(n => n + 1)}><History size={15}/>Обновить</button>
    </div>
    {isAdmin && <nav className="settings-tabs audit-scope" aria-label="Область журнала">
      <button aria-pressed={all} className={all ? 'active' : ''} onClick={() => changeScope('all')}>Все пользователи</button>
      <button aria-pressed={!all} className={all ? '' : 'active'} onClick={() => changeScope('mine')}>Мой журнал</button>
    </nav>}
    {!!data?.active.length && <section className="audit-active panel" aria-label="Текущие действия">
      <h2>Сейчас выполняется</h2>
      {data.active.map(row => <div className="audit-active-row" key={row.id}>
        {row.stage === 'confirmation' ? <ShieldCheck size={22}/> : <SkillIcon skill={row.skill} active/>}
        <div>
          <strong>{stages[row.stage]}{row.skill ? ' · ' + skillLabel(row.skill) : ''}</strong>
          <span>{actor(row)} · {row.agent}</span>
          {row.stage !== 'confirmation' && <Progress label={stages[row.stage]}/>}
        </div>
        <Elapsed started={row.time}/>
      </div>)}
    </section>}
    <div className="audit-filters">
      {all && <select aria-label="Пространство пользователя" value={filters.account} onChange={e => filter('account', e.target.value)}>
        <option value="">Все пространства</option>
        {data?.accounts.map(name => <option key={name}>{name}</option>)}
      </select>}
      <input list="audit-agents" aria-label="Агент" placeholder="Агент" maxLength={120} value={filters.agent} onChange={e => filter('agent', e.target.value)}/>
      <datalist id="audit-agents">{workspace.agents.map(a => <option key={a.name} value={a.name}/>)}</datalist>
      <input list="audit-skills" aria-label="Навык" placeholder="Навык" maxLength={120} value={filters.skill} onChange={e => filter('skill', e.target.value)}/>
      <datalist id="audit-skills">{workspace.skills.map(s => <option key={s.name} value={s.name}/>)}</datalist>
      <select aria-label="Статус" value={filters.status} onChange={e => filter('status', e.target.value)}>
        <option value="">Все статусы</option>
        {Object.entries(statuses).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
      </select>
      <input type="date" aria-label="Дата" value={filters.date} onChange={e => filter('date', e.target.value)}/>
      <button className="icon-button" aria-label="Сбросить фильтры" onClick={() => {setFilters(emptyFilters); setOffset(0); setExpanded(null);}}><X size={16}/></button>
    </div>
    <section className="audit-table" aria-label="Завершённые действия" aria-busy={!data}>
      <div className="audit-row audit-head"><span>ВРЕМЯ</span><span>ПОЛЬЗОВАТЕЛЬ</span><span className="audit-agent">АГЕНТ</span><span>СОБЫТИЕ / НАВЫК</span><span>СТАТУС</span><span className="audit-duration">ДЛИТЕЛЬНОСТЬ</span></div>
      {data?.events.map((row, index) => {
        const key = all ? row.account + '-' + row.guest + '-' + row.id : row.time + '-' + index;
        const expandable = !all || (row.kind === 'model' && row.skill == null);
        const content = <><span>{dateTime(row.time)}</span><span>{actor(row)}</span><span className="audit-agent">{row.agent}</span>
          <span>{eventLabel(row)}</span><span><span className={'status-pill ' + row.status}>{statuses[row.status] || row.status}</span></span>
          <span className="audit-duration">{row.duration_ms == null ? '—' : row.duration_ms + ' мс'}{expandable && <ChevronDown size={13}/>}</span></>;
        return !expandable ? <div className="audit-row" key={key}>{content}</div> :
          <React.Fragment key={key}><button className="audit-row" aria-expanded={expanded === key} onClick={() => setExpanded(expanded === key ? null : key)}>{content}</button>
            {expanded === key && (all ? <RequestDetails api={api} row={row}/> : <pre className="audit-detail">{JSON.stringify(row, null, 2)}</pre>)}
          </React.Fragment>;
      })}
      {!data && <div className="empty-state" role="status"><Progress label="Загружается журнал"/><p>Загружается журнал…</p></div>}
      {data && !data.events.length && <div className="empty-state"><Activity size={30}/><h3>Нет событий по выбранным фильтрам</h3></div>}
    </section>
    <div className="pagination">
      <button className="button secondary" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - pageSize))}>Назад</button>
      <span>Страница {offset / pageSize + 1}</span>
      <button className="button secondary" disabled={(data?.events.length ?? 0) < pageSize || offset + pageSize > 10000} onClick={() => setOffset(offset + pageSize)}>Далее</button>
    </div>
  </div>;
}
