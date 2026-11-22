import Schedules from './Schedules';
import {readEvents} from './stream';
import Composer from './Composer';
import Login from './Login';
import Connections from './Connections';
import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Activity, ArrowDown, ArrowRight, ArrowUp, Bot, Check, CheckCircle2, ChevronDown, ChevronRight, Circle, Code2, FileText, Folder, History, LayoutGrid, Loader2, MessageSquare, MoreHorizontal, Plus, Search, Settings2, ShieldCheck, Sparkles, Square, Terminal, Workflow, X, Zap} from 'lucide-react';
import {EditorView} from '@codemirror/view';
import {EditorState} from '@codemirror/state';
import {basicSetup} from 'codemirror';
import {python} from '@codemirror/lang-python';
import {parse as parseYaml, stringify as stringifyYaml} from 'yaml';

const display = name => name === 'office_assistant' ? 'Офисный помощник' : name.replaceAll('_', ' ');
const pretty = value => JSON.stringify(value, null, 2);
function metadataObject(text) {const match = text.match(/^---\s*\n([\s\S]*?)\n---/); return parseYaml(match ? match[1] : text) || {};}
async function api(path, options = {}) {
  const r = await fetch('/api' + path, {...options, headers: {'Content-Type': 'application/json', ...options.headers}});
  const data = await r.json();
  if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : pretty(data.detail || data));
  return data;
}
const put = body => ({method: 'PUT', body: JSON.stringify(body)});
const post = body => ({method: 'POST', body: JSON.stringify(body)});

function CodeEditor({value, onChange, language = 'python'}) {
  const container = useRef(null), view = useRef(null), callback = useRef(onChange);
  callback.current = onChange;
  useEffect(() => {
    view.current = new EditorView({parent: container.current, state: EditorState.create({doc: value, extensions: [basicSetup, ...(language === 'python' ? [python()] : []),
      EditorView.theme({'&': {backgroundColor: '#141619', color: '#cbd1dc', height: '100%', fontSize: '13px'}, '.cm-content': {fontFamily: 'ui-monospace, SFMono-Regular, monospace', padding: '16px 0', caretColor: '#eab186'}, '.cm-gutters': {backgroundColor: '#141619', color: '#525860', border: 'none'}, '&.cm-focused .cm-cursor': {borderLeftColor: '#eab186'}, '.cm-activeLine, .cm-activeLineGutter': {backgroundColor: '#ffffff05'}, '&.cm-focused .cm-selectionBackground, .cm-selectionBackground': {backgroundColor: '#6e584855'}, '.cm-scroller': {overflow: 'auto'}}, {dark: true}),
      EditorView.updateListener.of(update => {if (update.docChanged) callback.current(update.state.doc.toString());})]})});
    return () => view.current.destroy();
  }, [language]);
  useEffect(() => {const editor = view.current; if (editor && editor.state.doc.toString() !== value) editor.dispatch({changes: {from: 0, to: editor.state.doc.length, insert: value}});}, [value]);
  return <div className="code-editor" ref={container}/>;
}

function App() {
  const [workspace, setWorkspace] = useState({agents: [], skills: [], models: [], errors: {}});
  const [page, setPage] = useState('chat'), [agentName, setAgentName] = useState('office_assistant');
  const [messages, setMessages] = useState([]), [session, setSession] = useState(null), [sessions, setSessions] = useState([]);
  const [runStatus,setRunStatus]=useState(''), [input, setInput] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [toast, setToast] = useState(''), [agentDraft, setAgentDraft] = useState(null), [skillDraft, setSkillDraft] = useState(null);
  const [skillTab, setSkillTab] = useState('python'), [testInput, setTestInput] = useState('{"messages": [{"subject": "Счёт на оплату", "body": "Оплатить до пятницы"}]}'), [testOutput, setTestOutput] = useState(null), [saving, setSaving] = useState(false);
  const [audits, setAudits] = useState([]), [filters, setFilters] = useState({agent: '', skill: '', status: '', date: ''}), [offset, setOffset] = useState(0);
  const [expanded, setExpanded] = useState(null), [sidebarOpen, setSidebarOpen] = useState(false);
  const abort = useRef(null), bottom = useRef(null), form = useRef(null);
  const agent = workspace.agents.find(a => a.name === agentName);

  async function refresh() {const data = await api('/workspace'); setWorkspace(data); return data;}
  const reportError = e => setError(e.message || String(e));
  const notify = text => {setToast(text); setTimeout(() => setToast(''), 3500);};
  useEffect(() => {refresh().catch(reportError);}, []);
  useEffect(() => {if (agent) api('/sessions/' + agentName).then(setSessions).catch(reportError);}, [agentName, workspace.agents.length, busy]);
  useEffect(() => {bottom.current?.scrollIntoView({behavior: 'smooth'});}, [messages]);
  useEffect(() => {if (page === 'audit') loadAudit().catch(reportError);}, [page, filters, offset]);
  async function loadAudit() {setAudits(await api('/audit?' + new URLSearchParams({...filters, offset, limit: 30})));}
  function navigate(value) {if (busy) return; setPage(value); setSidebarOpen(false); setError('');}
  function newChat(name = agentName) {if (busy) return; setAgentName(name); setSession(null); setMessages([]); navigate('chat');}
  async function openSession(item) {
    if (busy) return;
    try {const history = await api(`/sessions/${agentName}/${item.id}`); setMessages(history.flatMap(m => m.role === 'user' ? [{role: 'user', text: Array.isArray(m.content) ? m.content.filter(p=>p.type==='text').map(p=>p.text).join('\n') : m.content, images: Array.isArray(m.content) ? m.content.filter(p=>p.type==='image_url').map(p=>p.image_url.url) : []}] : m.role === 'assistant' && m.content ? [{role: 'assistant', text: m.content}] : m.role === 'tool' ? [{role: 'tool', skill: m.name, status: JSON.parse(m.content).error ? 'error' : 'ok', output: JSON.parse(m.content)}] : [])); setSession(item.id); navigate('chat');} catch(e) {reportError(e);}
  }
  function editAgent(item = null) {if (busy) return; setAgentDraft(item ? structuredClone(item) : {name: '', model: 'demo:office', instructions: 'Ты — полезный помощник. Используй доступные навыки и не выдумывай результаты.', skills: [], confirmation_required: [], memory: {type: 'sqlite', path: './data/memory.db'}, max_steps: 10}); navigate('agents');}
  async function editSkill(name) {
    if (busy) return;
    if (workspace.builtin_skills?.includes(name)) {navigate('connections'); return;}
    try {setSkillDraft(name ? await api('/skills/' + name) : {name: '', metadata: '---\nname: new_skill\ndescription: Описание навыка\nversion: 0.1.0\ninputs:\n  text: str\noutputs:\n  result: str\npermissions:\n  filesystem: []\n  network: []\n  subprocess: false\n---\n', code: 'def run(input: dict) -> dict:\n    return {"result": input["text"].upper()}\n'}); setTestOutput(null); if (!name) setTestInput('{"text": "Привет, мир"}'); setSkillTab('python'); navigate('skills');} catch(e) {reportError(e);}
  }
  function changeAgent(key, value) {setAgentDraft(d => ({...d, [key]: value}));}
  function changeMetadata(patch) {
    try {const data = metadataObject(skillDraft.metadata); const body = skillDraft.metadata.match(/^---\s*\n[\s\S]*?\n---([\s\S]*)$/)?.[1] || '\n'; setSkillDraft({...skillDraft, metadata: '---\n' + stringifyYaml({...data, ...patch}) + '---' + body});} catch(e) {reportError(e);}
  }
  let metadata = {};
  if (skillDraft) {try {metadata = metadataObject(skillDraft.metadata);} catch {}}
  async function saveAgent(e) {
    e.preventDefault(); setSaving(true); setError('');
    try {await api('/agents/' + agentDraft.name, put(agentDraft)); await refresh(); setAgentName(agentDraft.name); notify('Агент проверен и сохранён');} catch(e) {reportError(e);} finally {setSaving(false);}
  }
  async function saveSkill() {
    setSaving(true); setError('');
    try {await api('/skills/' + skillDraft.name, put(skillDraft)); await refresh(); notify('Навык сохранён и зарегистрирован');} catch(e) {reportError(e);} finally {setSaving(false);}
  }
  async function testSkill() {
    setSaving(true); setError(''); setTestOutput(null);
    try {
      let payload = {...skillDraft, input: JSON.parse(testInput), confirmed: false};
      const execute = () => fetch('/api/skills/test', {...post(payload), headers: {'Content-Type': 'application/json'}});
      let response = await execute();
      if (response.status === 409) {const detail = await response.json(); if (!window.confirm('Разрешить тесту доступ?\n' + pretty(detail.detail.permissions))) {setTestOutput({status: 'denied'}); return;} payload.confirmed = true; response = await execute();}
      const result = await response.json(); if (!response.ok) throw new Error(pretty(result.detail)); setTestOutput(result);
    } catch(e) {reportError(e);} finally {setSaving(false);}
  }
  async function decide(id, approved) {
    try {await api('/confirmations/' + id, post({approved})); setMessages(current => current.map(m => m.id === id ? {...m, decision: approved ? 'approved' : 'denied'} : m));} catch(e) {reportError(e);}
  }
  async function send(text = input, images = [], webEnabled = false) {
    if (!text.trim() || busy || !agent) return;
    setInput(''); setBusy(true); setRunStatus('Отправляю запрос модели…'); setError(''); setPage('chat');
    const replyId = crypto.randomUUID();
    setMessages(current => [...current, {role: 'user', text, images}, {role: 'assistant', text: '', id: replyId, loading: true}]);
    abort.current = new AbortController(); let activeReply = replyId;
    const update = event => {
      const currentReply = activeReply;
      if(event.type==='session')setRunStatus('Модель готовит ответ…');
      if(event.type==='delta')setRunStatus('Получаю ответ…');
      if(event.type==='confirmation')setRunStatus('Требуется ваше подтверждение — откройте карточку действия в диалоге');
      if(event.type==='skill_start')setRunStatus('Выполняется: '+event.skill);
      if (event.type === 'session') setSession(event.session_id);
      if (event.type === 'done') setMessages(current => current.map(m => m.id === currentReply ? {...m, text: event.content || m.text, loading: false} : m));
      if (event.type === 'delta') setMessages(current => current.map(m => m.id === currentReply ? {...m, text: m.text + event.text, loading: false} : m));
      if (event.type === 'confirmation' || event.type === 'skill_start') setMessages(current => [...current.filter(m => m.id !== currentReply || m.text), {role: event.type === 'confirmation' ? 'confirmation' : 'tool', ...event, status: 'running'}]);
      if (event.type === 'skill_result') {
        const nextReply = crypto.randomUUID(); activeReply = nextReply;
        setMessages(current => {
          const index = current.findLastIndex(m => m.role === 'tool' && m.skill === event.skill && m.status === 'running');
          const next = current.filter(m => m.text !== '' || m.role !== 'assistant');
          if (index >= 0) {const target = current[index]; const j = next.indexOf(target); next[j] = {...target, ...event};} else next.push({role: 'tool', ...event});
          return [...next, {role: 'assistant', text: '', id: nextReply, loading: true}];
        });
      }
      if (event.type === 'error') throw new Error(event.error);
    };
    try {
      const response = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({agent: agentName, message: text, session_id: session, images, web_enabled: webEnabled}), signal: abort.current.signal});
      if (!response.ok) {const body = await response.json(); throw new Error(body.detail || 'Не удалось запустить агента');}
      await readEvents(response, update);
    } catch(e) {if (e.name !== 'AbortError') reportError(e); else notify('Запуск остановлен');}
    finally {abort.current?.abort(); abort.current = null; setBusy(false); setMessages(current => current.filter(m => !(m.role === 'assistant' && !m.text)).map(m => m.role === 'confirmation' && !m.decision ? {...m, decision: 'expired'} : m));}
  }
  const nav = [{id: 'connections', icon: Settings2, text: 'Подключения'}, {id: 'chat', icon: MessageSquare, text: 'Диалог'}, {id: 'agents', icon: Workflow, text: 'Агенты'}, {id: 'skills', icon: Code2, text: 'Навыки'}, {id: 'schedules', icon: History, text: 'Расписание'}, {id: 'audit', icon: Activity, text: 'Аудит'}];
  return <div className="app-shell">
    <aside className={'sidebar ' + (sidebarOpen ? 'open' : '')}>
      <a className="brand" href="/" aria-label="Superagent"><span className="brand-icon"><Zap size={20} fill="currentColor"/></span>superagent<span className="brand-dot">.</span></a>
      <div className="workspace-label"><span className="workspace-avatar">Л</span><div>Локальная мастерская<small>Личное пространство</small></div><span className="status-dot"/></div>
      <button className="new-chat" onClick={() => newChat()} disabled={busy}><Plus size={16}/> Новый диалог <kbd>↵</kbd></button>
      <nav>{nav.map(({id, icon: Icon, text}) => <button key={id} className={page === id ? 'active' : ''} disabled={busy} onClick={() => {if (id === 'agents') {setAgentDraft(null);} if (id === 'skills') {setSkillDraft(null);} navigate(id);}}><Icon size={17}/>{text}{id === 'skills' && <span className="nav-count">{workspace.skills.length}</span>}</button>)}</nav>
      <div className="sidebar-section"><span>МОИ АГЕНТЫ</span><button aria-label="Добавить агента" onClick={() => editAgent()} disabled={busy}><Plus size={14}/></button></div>
      <div className="agent-list">{workspace.agents.map(a => <button key={a.name} className={a.name === agentName ? 'selected' : ''} onClick={() => newChat(a.name)} disabled={busy}><span className="tiny-bot"><Bot size={16}/></span><span>{display(a.name)}</span><span className="status-dot"/></button>)}{!workspace.agents.length && <p className="sidebar-empty">Создайте первого агента</p>}</div>
      <div className="sidebar-section"><span>НЕДАВНИЕ ДИАЛОГИ</span><History size={13}/></div>
      <div className="session-list">{sessions.slice(0, 7).map(item => <button title={item.title} key={item.id} onClick={() => openSession(item)} disabled={busy}><MessageSquare size={13}/><span>{item.title}</span></button>)}{!sessions.length && <p className="sidebar-empty">Здесь появятся ваши диалоги</p>}</div>
      <div className="sidebar-bottom"><ShieldCheck size={16}/><div>Ваше пространство. Ваши правила.<small>Работает на этом компьютере</small></div><span className="version">v0.1</span></div>
    </aside>
    <main>
      <header className="topbar"><div className="breadcrumb"><button className="mobile-menu icon-button" onClick={() => setSidebarOpen(!sidebarOpen)} aria-label="Открыть меню"><LayoutGrid size={19}/></button><Folder size={16}/><span>Мастерская</span><ChevronRight size={13}/><strong>{nav.find(n => n.id === page)?.text}</strong></div><div className="topbar-right"><span className="local-badge"><span className="status-dot"/> Локально</span><span className="avatar">Я</span></div></header>
      {error && <div role="alert" className="error-banner"><span>{error}</span><button onClick={() => setError('')} aria-label="Закрыть ошибку"><X size={16}/></button></div>}
      {Object.keys(workspace.errors).length > 0 && <div className="error-banner">Ошибки регистрации: {Object.keys(workspace.errors).join(', ')}. Проверьте файлы в рабочем каталоге.</div>}
      {page === 'connections' && <Connections api={api} refresh={refresh} notify={notify} reportError={reportError}/>}
      {page === 'chat' && <>
        <div className="chat-toolbar"><div className="agent-heading"><span className="agent-avatar"><Bot size={20}/></span><div><button className="heading-button" onClick={() => editAgent(agent)} disabled={busy}>{display(agentName)}<ChevronDown size={14}/></button><small>{agent?.skills.length || 0} навыка <span>·</span> {agent?.model.startsWith('demo:') ? 'Демонстрационный режим' : agent?.model}</small></div></div><button className="text-button" disabled={busy} onClick={() => editAgent(agent)}><Settings2 size={15}/><span>Настроить агента</span></button></div>
        <div className="chat-scroll">
          {!messages.length ? <div className="welcome"><div className="eyebrow"><span/> ОТ ИДЕИ К ДЕЙСТВИЮ</div><div className="hero-mark"><Workflow size={32} strokeWidth={1.4}/><span className="sparkle"><Sparkles size={14}/></span></div><h1>Меньше рутины.<br/><span>Больше возможностей.</span></h1><p>Дайте агенту задачу. Он выберет навыки,<br/>выполнит работу и покажет результат.</p><div className="suggestion-grid">{[{icon: FileText, title: 'Разобрать входящие', text: 'Классифицировать письма и собрать отчёт', prompt: '{"messages":[{"subject":"Счёт на оплату","body":"Срочно оплатить до пятницы"},{"subject":"Стоимость внедрения","body":"Хотим купить решение для команды"},{"subject":"Встреча команды","body":"В четверг в 11:00"}]}'}, {icon: ShieldCheck, title: 'Сохранить результат', text: 'Попробовать действие с подтверждением', prompt: 'Сохрани отчёт: входящие письма обработаны, два запроса переданы в работу.'}].map((item, i) => <button className="suggestion" key={i} onClick={() => send(item.prompt)} disabled={!agent}><span className={'suggestion-icon color-' + i}><item.icon size={19}/></span><strong>{item.title}<ArrowRight size={15}/></strong><small>{item.text}</small></button>)}</div><div className="trust-line"><ShieldCheck size={14}/>Навыки изолированы<span>·</span>Действия под вашим контролем</div></div> : <div className="messages">{messages.map((m, i) => m.role === 'user' ? <div className="message user-message" key={m.id || i}><div className="message-label">Вы</div><div className="user-bubble">{!!m.images?.length&&<div className="message-photos">{m.images.map((url,j)=><img key={j} src={url} alt={`Фото ${j+1}`}/>)}</div>}{m.text}</div></div> : m.role === 'assistant' ? <div className="message assistant-message" key={m.id || i}><div className="message-label"><span className="tiny-bot"><Bot size={14}/></span>{display(agentName)}</div><div className="answer">{m.loading ? <span className="thinking"><span/><span/><span/></span> : m.text}</div></div> : m.role === 'confirmation' ? <div className="approval" key={m.id}><div className="approval-title"><ShieldCheck size={18}/>Требуется ваше подтверждение</div><p>Агент хочет выполнить <code>{m.skill}</code></p><pre>{pretty(m.preview || m.input)}</pre><details><summary>Запрашиваемые права</summary><pre>{pretty(m.permissions)}</pre></details>{m.decision ? <span className="decision">{m.decision === 'approved' ? '✓ Действие разрешено' : m.decision === 'denied' ? 'Действие отклонено' : 'Подтверждение завершено'}</span> : <div className="button-row"><button className="button primary" onClick={() => decide(m.id, true)}><Check size={14}/>Разрешить действие</button><button className="button secondary" onClick={() => decide(m.id, false)}>Отклонить</button></div>}</div> : <details className={'tool-event ' + m.status} key={m.id || i}><summary>{m.status === 'running' ? <Loader2 size={14} className="spin"/> : m.status === 'ok' ? <CheckCircle2 size={14}/> : <X size={14}/>}<code>{m.skill}</code><span>{m.status === 'running' ? 'Выполняется' : m.status === 'ok' ? 'Выполнено в песочнице' : m.status === 'denied' ? 'Отклонено' : 'Ошибка'}</span><ChevronDown size={13}/></summary><pre>{pretty(m.output || m.input)}</pre></details>)}<div ref={bottom}/></div>}
        </div>
        {busy&&<div className="run-status" role="status"><Loader2 size={14} className="spin"/>{runStatus}</div>}
        <Composer input={input} setInput={setInput} busy={busy} agent={agent} send={send} stop={()=>abort.current?.abort()} reportError={reportError}/>
      </>}
      {page === 'schedules' && <Schedules api={api} agents={workspace.agents} reportError={reportError} notify={notify} openResult={async(name,id)=>{try{const history=await api(`/sessions/${name}/${id}`);setAgentName(name);setSession(id);setMessages(history.filter(m=>['user','assistant'].includes(m.role)&&m.content).map(m=>({role:m.role,text:typeof m.content==='string'?m.content:m.content.filter(p=>p.type==='text').map(p=>p.text).join('\n')})));navigate('chat');}catch(e){reportError(e)}}}/>}
      {page === 'agents' && <div className="workspace-page"><button className="button secondary" onClick={async()=>{try{editAgent(await api('/agent-templates/work_manager'))}catch(e){reportError(e)}}}>Создать диспетчера задач</button><button className="button secondary" onClick={()=>{editAgent({name:'accountant_1c',model:workspace.models.find(m=>!m.startsWith('demo:'))||'demo:office',instructions:'Ты — помощник бухгалтера в 1С. Используй one_c_read только для разрешённых коллекций OData. Не придумывай имена коллекций или полей: попроси пользователя настроить их по метаданным его базы. Читай счета и документы, проверяй дубли и суммы с invoice_check после явного сопоставления полей 1С vendor, number, amount, currency, due_date, paid. Показывай ссылки или идентификаторы исходных документов и применённые фильтры. Даты, валюты, организации и статус проведения уточняй у пользователя. Ничего не записывай, не проводи и не оплачивай. Не выдавай предположения за факты. Итоговые бухгалтерские решения оставляй пользователю.',skills:['one_c_read','invoice_check'],skill_models:{},confirmation_required:['one_c_read'],memory:{type:'sqlite',path:'./data/memory.db'},max_steps:10})}}>Создать бухгалтера 1С (OData)</button><div className="page-heading"><div><div className="eyebrow">ВАША КОМАНДА</div><h1>{agentDraft ? 'Конструктор агента' : 'Агенты'}</h1><p>Инструкции, модель и навыки — соберите своего помощника.</p></div><button className="button primary" onClick={() => editAgent()}><Plus size={16}/>Новый агент</button></div>{agentDraft ? <form onSubmit={saveAgent} className="builder-grid"><section className="panel"><div className="panel-heading"><Bot size={18}/><h2>Основное</h2></div><label>Имя агента<input required pattern="[a-z][a-z0-9_]{0,63}" value={agentDraft.name} placeholder="my_assistant" onChange={e => changeAgent('name', e.target.value)}/><small>Латинские буквы, цифры и подчёркивания</small></label><label>Модель<input list="models" required value={agentDraft.model} onChange={e => changeAgent('model', e.target.value)}/><datalist id="models">{workspace.models.map(m => <option key={m} value={m}/>)}</datalist><small>Провайдер:model. Дополнительные провайдеры — в config.yaml</small></label><label>Инструкции<textarea rows={9} required value={agentDraft.instructions} onChange={e => changeAgent('instructions', e.target.value)}/></label><label>Лимит шагов<input type="number" min="1" max="100" value={agentDraft.max_steps} onChange={e => changeAgent('max_steps', Number(e.target.value))}/></label></section><section className="panel"><div className="panel-heading"><Workflow size={18}/><h2>Навыки и подтверждения</h2></div><p className="muted">Выберите возможности агента. Для записи файлов и сетевых действий подтверждение запрашивается автоматически.</p>{workspace.skills.map(s => <div className="skill-option" key={s.name}><label className="checkbox-label"><input type="checkbox" checked={agentDraft.skills.includes(s.name)} onChange={e => {if(!e.target.checked){const m={...agentDraft.skill_models}; delete m[s.name]; changeAgent('skill_models',m);} changeAgent('skills', e.target.checked ? [...agentDraft.skills, s.name] : agentDraft.skills.filter(n => n !== s.name)); if (!e.target.checked) changeAgent('confirmation_required', agentDraft.confirmation_required.filter(n => n !== s.name));}}/><div><strong>{s.name}</strong><small>{s.description}</small></div></label>{agentDraft.skills.includes(s.name) && <label className="confirm-check"><input type="checkbox" checked={agentDraft.confirmation_required.includes(s.name)} onChange={e => changeAgent('confirmation_required', e.target.checked ? [...agentDraft.confirmation_required, s.name] : agentDraft.confirmation_required.filter(n => n !== s.name))}/>Всегда запрашивать подтверждение</label>}</div>)}{agentDraft.skills.map(name => <label key={name}>Модель обработки результата · {name}<input list="models" placeholder="Без отдельной модели" value={agentDraft.skill_models?.[name] || ''} onChange={e => {const mapping={...agentDraft.skill_models}; if(e.target.value) mapping[name]=e.target.value; else delete mapping[name]; changeAgent('skill_models',mapping);}}/></label>)}<div className="info-box"><ShieldCheck size={18}/><span>Каждый навык выполняется в отдельном процессе с ограничениями ОС.</span></div><button className="button primary full" disabled={saving} type="submit">{saving ? <Loader2 className="spin" size={16}/> : <Check size={16}/>}Проверить и сохранить</button></section></form> : <div className="entity-grid">{workspace.agents.map(a => <button className="entity-card" key={a.name} onClick={() => editAgent(a)}><span className="agent-avatar"><Bot size={22}/></span><h2>{display(a.name)}</h2><p>{a.instructions}</p><div className="card-meta"><code>{a.model}</code><span>{a.skills.length} навыка</span></div><div className="card-footer">Настроить агента<ArrowRight size={15}/></div></button>)}</div>}</div>}
      {page === 'skills' && <div className="workspace-page"><div className="page-heading"><div><div className="eyebrow">СТРОИТЕЛЬНЫЕ БЛОКИ</div><h1>{skillDraft ? 'Редактор навыка' : 'Навыки'}</h1><p>Независимые Python-модули с явными правами доступа.</p></div><button className="button primary" onClick={() => editSkill()}><Plus size={16}/>Новый навык</button></div>{skillDraft ? <><div className="editor-topline"><label>Имя навыка<input value={skillDraft.name} placeholder="new_skill" onChange={e => setSkillDraft({...skillDraft, name: e.target.value})}/></label><button className="button primary" disabled={saving || !skillDraft.name} onClick={saveSkill}><Check size={15}/>Сохранить навык</button></div><div className="editor-grid"><section className="editor-panel"><div className="editor-tabs"><button className={skillTab === 'python' ? 'active' : ''} onClick={() => setSkillTab('python')}><Code2 size={14}/>skill.py</button><button className={skillTab === 'metadata' ? 'active' : ''} onClick={() => setSkillTab('metadata')}><FileText size={14}/>SKILL.md</button><span>Python 3.11+</span></div>{skillTab === 'metadata' && <div className="metadata-form"><div className="metadata-form-row"><label>Имя в SKILL.md<input value={metadata.name || ''} onChange={e => changeMetadata({name: e.target.value})}/></label><label>Версия<input value={metadata.version || ''} onChange={e => changeMetadata({version: e.target.value})}/></label></div><label>Описание<textarea rows={2} value={metadata.description || ''} onChange={e => changeMetadata({description: e.target.value})}/></label><label>Разрешённые пути · через запятую<input value={(Array.isArray(metadata.permissions?.filesystem) ? metadata.permissions.filesystem : []).join(', ')} onChange={e => changeMetadata({permissions: {...metadata.permissions, filesystem: e.target.value.split(',').map(v => v.trim()).filter(Boolean)}})}/></label><label>Сетевые адреса · host:port<input value={(Array.isArray(metadata.permissions?.network) ? metadata.permissions.network : []).join(', ')} onChange={e => changeMetadata({permissions: {...metadata.permissions, network: e.target.value.split(',').map(v => v.trim()).filter(Boolean)}})}/></label><div className="metadata-form-row"><label className="checkbox-label"><input type="checkbox" checked={!!metadata.irreversible} onChange={e => changeMetadata({irreversible: e.target.checked})}/>Необратимое действие</label><label className="checkbox-label"><input type="checkbox" checked={!!metadata.permissions?.subprocess} onChange={e => changeMetadata({permissions: {...metadata.permissions, subprocess: e.target.checked}})}/>Подпроцессы</label></div></div>}<CodeEditor key={skillTab} value={skillTab === 'python' ? skillDraft.code : skillDraft.metadata} language={skillTab} onChange={v => setSkillDraft(d => ({...d, [skillTab === 'python' ? 'code' : 'metadata']: v}))}/></section><section className="panel test-panel"><div className="panel-heading"><Terminal size={17}/><h2>Проверка в песочнице</h2></div><label>Входные данные · JSON<textarea rows={8} className="mono" value={testInput} onChange={e => setTestInput(e.target.value)}/></label><button className="button secondary full" disabled={saving} onClick={testSkill}>{saving ? <Loader2 size={16} className="spin"/> : <ArrowRight size={16}/>}Запустить проверку</button><h3>Результат</h3><pre className="test-output">{testOutput ? pretty(testOutput) : 'Запустите навык с тестовыми данными.\nЗдесь появится результат выполнения.'}</pre></section></div></> : <div className="entity-grid">{workspace.skills.map(s => <button key={s.name} className="entity-card" onClick={() => editSkill(s.name)}><span className="skill-card-icon"><Code2 size={22}/></span><h2>{s.name}</h2><p>{s.description}</p><div className="card-meta"><code>v{s.version}</code><span><ShieldCheck size={13}/>{s.irreversible ? 'Подтверждение' : 'Изолирован'}</span></div><div className="card-footer">Открыть редактор<ArrowRight size={15}/></div></button>)}</div>}</div>}
      {page === 'audit' && <div className="workspace-page"><div className="page-heading"><div><div className="eyebrow">ПРОЗРАЧНОСТЬ НА КАЖДОМ ШАГЕ</div><h1>Журнал действий</h1><p>Ответы моделей, вызовы навыков и ваши решения.</p></div><button className="button secondary" onClick={() => loadAudit().catch(reportError)}><History size={15}/>Обновить</button></div><div className="audit-filters">{[['agent', 'Все агенты', workspace.agents.map(a => a.name)], ['skill', 'Все навыки', workspace.skills.map(s => s.name)], ['status', 'Все статусы', ['ok', 'denied', 'error', 'limit']]].map(([key, label, options]) => <select key={key} aria-label={label} value={filters[key]} onChange={e => {setFilters({...filters, [key]: e.target.value}); setOffset(0);}}><option value="">{label}</option>{options.map(o => <option key={o}>{o}</option>)}</select>)}<input type="date" aria-label="Дата" value={filters.date} onChange={e => {setFilters({...filters, date: e.target.value}); setOffset(0);}}/><button className="icon-button" aria-label="Сбросить фильтры" onClick={() => {setFilters({agent: '', skill: '', status: '', date: ''}); setOffset(0);}}><X size={16}/></button></div><section className="audit-table"><div className="audit-row audit-head"><span>ВРЕМЯ</span><span>АГЕНТ</span><span>СОБЫТИЕ / НАВЫК</span><span>СТАТУС</span><span>ДЛИТЕЛЬНОСТЬ</span></div>{audits.map((a, i) => <React.Fragment key={i}><button className="audit-row" onClick={() => setExpanded(expanded === i ? null : i)}><span>{new Date(a.time).toLocaleString('ru-RU', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit'})}</span><span>{a.agent}</span><code>{a.skill || (a.kind === 'model' ? 'Ответ модели' : 'Запуск')}</code><span><span className={'status-pill ' + a.status}>{a.status}</span></span><span>{a.duration_ms} мс<ChevronDown size={13}/></span></button>{expanded === i && <pre className="audit-detail">{pretty(a)}</pre>}</React.Fragment>)}{!audits.length && <div className="empty-state"><Activity size={32}/><h3>Пока нет событий</h3><p>Запустите агента или проверьте навык — действия появятся здесь.</p></div>}</section><div className="pagination"><button className="button secondary" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 30))}>Назад</button><span>Страница {offset / 30 + 1}</span><button className="button secondary" disabled={audits.length < 30} onClick={() => setOffset(offset + 30)}>Далее</button></div></div>}
    </main>{toast && <div className="toast" role="status"><CheckCircle2 size={17}/>{toast}</div>}
  </div>;
}

createRoot(document.getElementById('root')).render(<Login><App/></Login>);
