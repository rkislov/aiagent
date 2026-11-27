import ChatToolbar, {NavigationToggle} from './ChatToolbar';
import WorkspaceSidebar from './WorkspaceSidebar';
import Audit from './Audit';
import PendingApprovals from './PendingApprovals';
import Schedules from './Schedules';
import {readEvents, readPollingEvents} from './stream';
import Composer from './Composer';
import ArtifactLinks from './ArtifactLinks';
import AnswerActions from './AnswerActions';
import ThemeToggle from './ThemeToggle';
import SharedAgents from './SharedAgents';
import UserManagement from './Users';
import AgentCreator from './AgentCreator';
import Knowledge from './Knowledge';
import KnowledgeSelector from './KnowledgeSelector';
import Markdown from './Markdown';
import SkillActivity,{SkillIcon,skillLabel} from './SkillActivity';
import Login from './Login';
import Connections from './Connections';
import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Activity, ArrowRight, Bot, Check, CheckCircle2, ChevronDown, Code2, FileText, History, Loader2, MessageSquare, Plus, Settings2, ShieldCheck, Sparkles, Terminal, Workflow, X, UsersRound, BookOpen} from 'lucide-react';
import {EditorView} from '@codemirror/view';
import {EditorState} from '@codemirror/state';
import {basicSetup} from 'codemirror';
import {python} from '@codemirror/lang-python';
import {parse as parseYaml, stringify as stringifyYaml} from 'yaml';

const sharedTitles={};
const display = name => sharedTitles[name] || ({plain_chat:'Модель',office_assistant:'Офисный помощник',lawyer_rf:'Юрист РФ',accountant_1c:'Бухгалтер 1С',work_manager:'Диспетчер задач',security_auditor:'Аудитор безопасности'})[name] || (name||'Модель').replaceAll('_',' ');
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

const mobileViewport = '(max-width:760px)';
const navigationKey = 'aiagent-navigation-mode';
function savedNavigationMode() {
  try {
    const mode = localStorage.getItem(navigationKey);
    if (mode === 'closed' || mode === 'expanded') return mode;
  } catch {}
  return 'rail';
}

function App({accountMenu}) {
  const [workspace, setWorkspace] = useState({agents: [], skills: [], models: [], errors: {}});
  const [page, setPage] = useState('chat'), [agentName, setAgentName] = useState('office_assistant');
  const [chatMode,setChatMode]=useState(true),[connected,setConnected]=useState(''),[chatModel,setChatModel]=useState(''),[chatConnections,setChatConnections]=useState(null);
  const [webEnabled,setWebEnabled]=useState(false),[webBusy,setWebBusy]=useState(false);
  const [knowledgeCollections,setKnowledgeCollections]=useState([]),[selectedKnowledge,setSelectedKnowledge]=useState([]),[knowledgeBusy,setKnowledgeBusy]=useState(false);
  const loadKnowledge=()=>api('/knowledge/collections').then(rows=>{setKnowledgeCollections(rows);setSelectedKnowledge(current=>current.filter(id=>rows.some(row=>row.id===id)));return rows});
  useEffect(()=>{if(page==='chat')loadKnowledge().catch(reportError)},[page]);
  const [messages, setMessages] = useState([]), [session, setSession] = useState(null), [sessions, setSessions] = useState([]);
  const [activeSkill,setActiveSkill]=useState(null);
  const [runStatus,setRunStatus]=useState(''), [input, setInput] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [toast, setToast] = useState(''), [agentDraft, setAgentDraft] = useState(null), [skillDraft, setSkillDraft] = useState(null);
  const [skillTab, setSkillTab] = useState('python'), [testInput, setTestInput] = useState('{"messages": [{"subject": "Счёт на оплату", "body": "Оплатить до пятницы"}]}'), [testOutput, setTestOutput] = useState(null), [saving, setSaving] = useState(false);
  const [approvalRevision, setApprovalRevision] = useState(0), [runStarted, setRunStarted] = useState(null);
  const [desktopMenu, setDesktopMenu] = useState(savedNavigationMode);
  const [mobileMenu, setMobileMenu] = useState('closed');
  const [mobile, setMobile] = useState(() => matchMedia(mobileViewport).matches);
  const menuMode = mobile ? mobileMenu : desktopMenu;
  const setMenuMode = mobile ? setMobileMenu : setDesktopMenu;
  const menuOpen = menuMode !== 'closed';
  const [composerRevision, setComposerRevision] = useState(0);
  const [sidebarGroups, setSidebarGroups] = useState(() => {
    try {
      const value = JSON.parse(localStorage.getItem('aiagent-sidebar-groups') || '{}');
      return {agents: value.agents !== false, dialogs: value.dialogs !== false, management: value.management === true};
    } catch {
      return {agents: true, dialogs: true, management: false};
    }
  });
  useEffect(() => {
    const query = matchMedia(mobileViewport);
    const update = () => setMobile(query.matches);
    query.addEventListener('change', update);
    update();
    return () => query.removeEventListener('change', update);
  }, []);
  useEffect(() => {try {localStorage.setItem(navigationKey, desktopMenu);} catch {}}, [desktopMenu]);
  useEffect(() => {try {localStorage.setItem('aiagent-sidebar-groups', JSON.stringify(sidebarGroups));} catch {}}, [sidebarGroups]);
  useEffect(() => {
    if (!mobile || !menuOpen) return;
    const close = event => {if (event.key === 'Escape') setMobileMenu('closed');};
    document.addEventListener('keydown', close);
    return () => document.removeEventListener('keydown', close);
  }, [mobile, menuOpen]);
  const toggleSidebarGroup = (key, value) => setSidebarGroups(groups => {
    const next = value ?? !groups[key];
    return groups[key] === next ? groups : {...groups, [key]: next};
  });
  const toggleMenu = () => setMenuMode(current => current === 'closed' ? 'rail' : 'closed');
  const resizeMenu = () => setMenuMode(current => current === 'expanded' ? 'rail' : 'expanded');
  const dialogLoad=useRef(0);
  const abort = useRef(null), bottom = useRef(null), form = useRef(null);
  const usableModels=workspace.models.filter(m=>workspace.providers?.some(p=>p.name===m.split(':')[0]&&p.configured));
  const plainModel=chatModel||usableModels.find(m=>!m.startsWith('demo:'))||usableModels[0]||'demo:office';
  const resolvedAgent = chatMode ? (workspace.agents.find(a=>a.name===connected)||{name:'plain_chat',model:plainModel,skills:[]}) : workspace.agents.find(a => a.name === agentName);

  const agent=resolvedAgent?.name.startsWith('shared_')?{...resolvedAgent,model:plainModel}:resolvedAgent;
  async function refresh() {const data = await api('/workspace'); for(const row of data.shared_agents||[])sharedTitles[row.id]=row.original_name.replaceAll('_',' '); setWorkspace(data); return data;}
  const reportError = e => setError(e.message || String(e));
  const notify = text => {setToast(text); setTimeout(() => setToast(''), 3500);};
  useEffect(() => {refresh().catch(reportError);}, []);
  useEffect(() => {api('/conversations').then(setSessions).catch(reportError);}, [workspace.agents.length,busy]);
  useEffect(() => {bottom.current?.scrollIntoView({behavior: 'smooth'});}, [messages]);
  function navigate(value) {
    if (busy) return;
    setPage(value);
    setMobileMenu('closed');
    setError('');
  }
  function newChat() {
    if (busy) return;
    dialogLoad.current++;
    setComposerRevision(current => current + 1);
    setChatMode(true);
    setConnected('');
    setChatConnections(null);
    setWebEnabled(false);
    setSelectedKnowledge([]);
    setSession(null);
    setMessages([]);
    setInput('');
    navigate('chat');
  }
  async function chooseAgent(name) {
    if (busy) return;
    const revision = ++dialogLoad.current;
    try {
      if (!chatMode && session) {
        const result = await api('/conversations/import', post({agent: agentName, session_id: session}));
        if (revision !== dialogLoad.current) return;
        setSession(result.session_id);
      }
      setChatMode(true);
      setConnected(name);
      navigate('chat');
    } catch (error) {
      if (revision === dialogLoad.current) reportError(error);
    }
  }
  async function openSession(item) {
    if (busy) return;
    const revision=++dialogLoad.current;
    try {const shared=item.scope==='conversation';const history=await api(shared?`/conversations/${item.id}`:`/sessions/${item.agent}/${item.id}`);const settings=shared?await api(`/conversations/${item.id}/settings`):null;const restoredWeb=shared?settings.web_enabled:([...history].reverse().find(m=>m.role==='user')?._web_enabled??false);if(revision!==dialogLoad.current)return;setComposerRevision(current => current + 1);setInput('');setChatMode(shared);setConnected('');setWebEnabled(restoredWeb);setSelectedKnowledge(shared?(settings.knowledge_collections||[]):([...history].reverse().find(m=>m.role==='user')?._knowledge_collections||[]));setChatConnections([...history].reverse().find(m=>m.role==='user')?._connections??null);if(!shared)setAgentName(item.agent); setMessages(history.flatMap(m => m.role === 'user' ? [{role: 'user', text: Array.isArray(m.content) ? m.content.filter(p=>p.type==='text').map(p=>p.text).join('\n') : m.content, images: Array.isArray(m.content) ? m.content.filter(p=>p.type==='image_url').map(p=>p.image_url.url) : []}] : m.role === 'assistant' && m.content ? [{role:'assistant',text:m.content,speaker:m._speaker||(!shared?item.agent:'plain_chat'), messageId:m._message_id, rating:m._feedback}] : m.role === 'tool' ? [{role: 'tool', skill: m.name, status: JSON.parse(m.content).error ? 'error' : 'ok', output: JSON.parse(m.content)}] : [])); setSession(item.id); navigate('chat');} catch(e) {reportError(e);}
  }
  async function changeWeb(enabled) {
    setWebEnabled(enabled);
    if (!session || !chatMode) return;
    const revision = dialogLoad.current;
    setWebBusy(true);
    try {await api(`/conversations/${session}/settings`,put({web_enabled:enabled}));}
    catch(e){if(revision===dialogLoad.current){setWebEnabled(!enabled);reportError(e)}}finally{setWebBusy(false)}
  }
  async function changeKnowledge(ids){
    const previous=selectedKnowledge,revision=dialogLoad.current;
    setSelectedKnowledge(ids);setKnowledgeBusy(true);
    try{if(session&&chatMode)await api(`/conversations/${session}/settings`,put({knowledge_collections:ids}));return true}
    catch(e){if(revision===dialogLoad.current){setSelectedKnowledge(previous);reportError(e)}return false}
    finally{setKnowledgeBusy(false)}
  }
  async function connectKnowledge(ids){await loadKnowledge();if(await changeKnowledge(ids))navigate('chat')}
  function editAgent(item = null) {if (busy) return; setAgentDraft(item ? structuredClone(item) : {name: '', model: plainModel, instructions: 'Ты — полезный помощник. Используй доступные навыки и не выдумывай результаты.', skills: [], confirmation_required: [], memory: {type: 'sqlite', path: './data/memory.db'}, max_steps: 10}); navigate('agents');}
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
  async function rateAnswer(messageId, rating) {
    const revision = dialogLoad.current;
    const root = chatMode ? '/conversations/' : '/sessions/' + encodeURIComponent(agentName) + '/';
    const result = await api(root + encodeURIComponent(session) + '/messages/' + messageId + '/feedback', put({rating}));
    if (revision === dialogLoad.current) {
      setMessages(current => current.map(message => message.messageId === messageId ? {...message, rating: result.rating} : message));
    }
  }
  function markDecision(id, approved, expired = false) {
    setMessages(current => current.map(m => m.id === id ? {...m, decision: expired ? 'expired' : approved ? 'approved' : 'denied'} : m));
    setApprovalRevision(value => value + 1);
  }
  async function send(text = input, images = [], currentWeb = webEnabled, files = []) {
    if (!text.trim() || busy || !agent) return;
    setInput(''); setBusy(true); setRunStarted(Date.now()); setRunStatus('Отправляю запрос модели…'); setError(''); setPage('chat');
    const replyId = crypto.randomUUID();
    setMessages(current => [...current, {role: 'user', text: text+(files.length?'\n\nФайлы: '+files.map(f=>f.name).join(', '):''), images}, {role:'assistant',text:'',id:replyId,loading:true,speaker:agent.name}]);
    abort.current = new AbortController(); let activeReply = replyId;
    const update = event => {
      const currentReply = activeReply;
      if(event.type==='session')setRunStatus('Модель готовит ответ…');
      if(event.type==='delta')setRunStatus('Получаю ответ…');
      if(event.type==='confirmation') {
        setRunStatus('Требуется ваше подтверждение в панели над диалогом');
        setApprovalRevision(value => value + 1);
      }
      if(event.type==='confirmation_result') {
        markDecision(event.id, event.approved, event.expired);
        setRunStatus(event.approved ? 'Разрешение получено…' : 'Модель обрабатывает результат…');
      }
      if(event.type==='skill_start'){setActiveSkill(event.skill);setRunStatus('Выполняется: '+event.skill)}
      if(event.type==='confirmation')setActiveSkill(null);
      if (event.type === 'session') {setSession(event.session_id);if(event.knowledge_collections)setSelectedKnowledge(event.knowledge_collections);if(typeof event.web_enabled==='boolean')setWebEnabled(event.web_enabled);}
      if (event.type === 'done') setMessages(current => current.map(m => m.id === currentReply ? {...m, text: event.content || m.text, loading: false, messageId: event.message_id} : m));
      if (event.type === 'delta') setMessages(current => current.map(m => m.id === currentReply ? {...m, text: m.text + event.text, loading: false} : m));
      if (event.type === 'confirmation' || event.type === 'skill_start') setMessages(current => [...current.filter(m => m.id !== currentReply || m.text), {role: event.type === 'confirmation' ? 'confirmation' : 'tool', ...event, status: 'running'}]);
      if (event.type === 'skill_result') {
        setActiveSkill(null);setRunStatus('Модель обрабатывает результат…');
        const nextReply = crypto.randomUUID(); activeReply = nextReply;
        setMessages(current => {
          const index = current.findLastIndex(m => m.role === 'tool' && m.skill === event.skill && m.status === 'running');
          const next = current.filter(m => m.text !== '' || m.role !== 'assistant');
          if (index >= 0) {const target = current[index]; const j = next.indexOf(target); next[j] = {...target, ...event};} else next.push({role: 'tool', ...event});
          return [...next, {role:'assistant',text:'',id:nextReply,loading:true,speaker:agent.name}];
        });
      }
      if (event.type === 'error') throw new Error(event.error);
    };
    try {
      const response = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({agent:chatMode?(connected||null):agentName, conversation:chatMode, connections:chatConnections, model:plainModel, message:text, session_id: session, images, files:files.map(f=>f.id), web_enabled: currentWeb, knowledge_collections:selectedKnowledge, transport:'poll'}), signal: abort.current.signal});
      if (!response.ok) {const body = await response.json(); throw new Error(body.detail || 'Не удалось запустить агента');}
      if(response.headers.get('content-type')?.includes('application/json')){
        const {run_id}=await response.json();
        const cancel=()=>fetch('/api/chat/runs/'+run_id+'/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).catch(()=>{});
        abort.current.signal.addEventListener('abort',cancel,{once:true});
        try{await readPollingEvents(run_id,update,abort.current.signal);}finally{abort.current.signal.removeEventListener('abort',cancel);}
      }else await readEvents(response, update);
    } catch(e) {if (e.name !== 'AbortError') reportError(e); else notify('Запуск остановлен');}
    finally {abort.current?.abort(); abort.current = null; setBusy(false);setActiveSkill(null); setApprovalRevision(value => value + 1); setMessages(current => current.filter(m => !(m.role === 'assistant' && !m.text)).map(m => m.role === 'confirmation' && !m.decision ? {...m, decision: 'expired'} : m));}
  }
  const nav = [
    {id: 'chat', icon: MessageSquare, text: 'Диалог', group: 'work'},
    {id: 'agents', icon: Workflow, text: 'Агенты', group: 'work'},
    {id: 'knowledge', icon: BookOpen, text: 'Знания', group: 'work'},
    {id: 'schedules', icon: History, text: 'Расписание', group: 'work'},
    {id: 'connections', icon: Settings2, text: 'Подключения', group: 'management'},
    {id: 'skills', icon: Code2, text: 'Навыки', group: 'management'},
    ...(workspace.current_user?.admin ? [{id: 'users', icon: UsersRound, text: 'Пользователи', group: 'management'}] : []),
    {id: 'audit', icon: Activity, text: 'Аудит', group: 'management'},
  ];
  function selectPage(id) {
    if (id === 'agents') setAgentDraft(null);
    if (id === 'skills') setSkillDraft(null);
    navigate(id);
  }
  const dialogTitle = sessions.find(item => item.id === session)?.title ||
    messages.find(message => message.role === 'user')?.text.split('\n')[0];
  const chatAgents = workspace.agents.map(item => ({...item, display_name: display(item.name)}));

  return <div className="app-shell workspace-shell">
    <WorkspaceSidebar mode={menuMode} mobile={mobile} groups={sidebarGroups} onToggleGroup={toggleSidebarGroup}
      navigation={nav} page={page} busy={busy} onNavigate={selectPage} onResize={resizeMenu} onClose={() => setMenuMode('closed')}
      agents={chatAgents.map(item => ({name: item.name, label: item.display_name, icon: Bot}))} activeAgent={chatMode ? connected : agentName}
      onSelectAgent={chooseAgent} onAddAgent={() => editAgent()} sessions={sessions} session={session} onOpenSession={openSession} onNewChat={newChat}/>
    <main className={page === 'chat' ? 'chat-workspace' : ''}>
      {page !== 'chat' && <header className="topbar workspace-topbar floating-tile">
        <div className="breadcrumb"><NavigationToggle open={menuOpen} onToggle={toggleMenu}/><strong>{nav.find(item => item.id === page)?.text}</strong></div>
        <div className="topbar-right"><ThemeToggle/>{accountMenu}</div>
      </header>}
      {error && <div role="alert" className="error-banner"><span>{error}</span><button onClick={() => setError('')} aria-label="Закрыть ошибку"><X size={16}/></button></div>}
      {Object.keys(workspace.errors).length > 0 && <div className="error-banner">Ошибки регистрации: {Object.keys(workspace.errors).join(', ')}. Проверьте файлы в рабочем каталоге.</div>}
      <PendingApprovals api={api} revision={approvalRevision} onDecision={markDecision} reportError={reportError}/>
      {page === 'knowledge' && <Knowledge api={api} notify={notify} reportError={reportError} onConnect={connectKnowledge}/>}
      {page === 'users' && workspace.current_user?.admin && <div className="workspace-page accounts-page"><UserManagement api={api} currentUser={workspace.current_user} notify={notify} reportError={reportError}/></div>}
      {page === 'connections' && <Connections api={api} currentUser={workspace.current_user} refresh={refresh} notify={notify} reportError={reportError}/>}
      {page === 'chat' && <>
        <ChatToolbar
          models={usableModels.includes(plainModel) ? usableModels : [plainModel, ...usableModels]} model={plainModel} setModel={setChatModel}
          agents={chatAgents} connected={chatMode ? connected : agentName} onAgent={chooseAgent} busy={busy} legacy={!chatMode}
          sources={workspace.chat_connections || []} selectedSources={chatConnections} onSources={setChatConnections} onSettings={() => navigate('connections')}
          knowledgePicker={<KnowledgeSelector collections={knowledgeCollections} selected={selectedKnowledge} onChange={changeKnowledge}
            onManage={() => navigate('knowledge')} busy={busy || knowledgeBusy}/>}
          menuOpen={menuOpen} onToggleMenu={toggleMenu} onNewChat={newChat} onOpenSession={openSession}
          sessions={sessions} session={session} title={dialogTitle} accountMenu={accountMenu}/>
        <div className="chat-scroll">
          {!messages.length ? <div className="welcome chat-welcome"><div className="hero-mark"><Workflow size={32} strokeWidth={1.4}/><span className="sparkle"><Sparkles size={14}/></span></div><h2>С чего начнём?</h2><p>Напишите сообщение или подключите агента в настройках диалога.</p><div className="suggestion-grid">{[{icon: FileText, title: 'Разобрать входящие', text: 'Классифицировать письма и собрать отчёт', prompt: '{"messages":[{"subject":"Счёт на оплату","body":"Срочно оплатить до пятницы"},{"subject":"Стоимость внедрения","body":"Хотим купить решение для команды"},{"subject":"Встреча команды","body":"В четверг в 11:00"}]}'}, {icon: ShieldCheck, title: 'Сохранить результат', text: 'Попробовать действие с подтверждением', prompt: 'Сохрани отчёт: входящие письма обработаны, два запроса переданы в работу.'}].map((item, i) => <button className="suggestion floating-tile rounded-2xl" key={i} onClick={() => send(item.prompt)} disabled={!agent}><span className={'suggestion-icon color-' + i}><item.icon size={19}/></span><strong>{item.title}<ArrowRight size={15}/></strong><small>{item.text}</small></button>)}</div><div className="trust-line"><ShieldCheck size={14}/>Навыки изолированы<span>·</span>Действия под вашим контролем</div></div> : <div className="messages">{messages.map((m, i) => m.role === 'user' ? <div className="message user-message" key={m.id || i}><div className="message-label">Вы</div><div className="user-bubble">{!!m.images?.length&&<div className="message-photos">{m.images.map((url,j)=><img key={j} src={url} alt={`Фото ${j+1}`}/>)}</div>}{m.text}</div></div> : m.role === 'assistant' ? <div className="message assistant-message" key={m.id || i}><div className="message-label"><span className="tiny-bot"><Bot size={14}/></span>{display(m.speaker||agent?.name)}</div><div className="answer">{m.loading ? <span className="thinking"><span/><span/><span/></span> : <Markdown text={m.text}/>}
{m.messageId && session && m.text && <AnswerActions text={m.text} rating={m.rating} onRate={rating => rateAnswer(m.messageId, rating)} notify={notify} reportError={reportError}/>}</div></div> : m.role === 'confirmation' ? <div className="approval confirmation-history" key={m.id}><div className="approval-title"><ShieldCheck size={18}/>{skillLabel(m.skill)}</div><span className="decision">{m.decision === 'approved' ? 'Действие разрешено' : m.decision === 'denied' ? 'Действие отклонено' : m.decision === 'expired' ? 'Подтверждение завершено' : 'Ожидает решения в панели подтверждений'}</span></div> : <div key={m.id || i}><details className={'tool-event ' + m.status}><summary><SkillIcon skill={m.skill} active={m.status==='running'}/><strong>{skillLabel(m.skill)}</strong><span>{m.status === 'running' ? 'Выполняется' : m.status === 'ok' ? 'Готово' : m.status === 'denied' ? 'Отклонено' : 'Ошибка'}</span><ChevronDown size={13}/></summary><pre>{pretty(m.output || m.input)}</pre></details><ArtifactLinks output={m.output}/></div>)}<div ref={bottom}/></div>}
        </div>
        {busy&&<SkillActivity skill={activeSkill} statusText={runStatus} started={runStarted}/>}
        <Composer key={composerRevision} web={webEnabled} setWeb={changeWeb} webBusy={webBusy||knowledgeBusy} input={input} setInput={setInput} busy={busy} agent={agent} send={send} stop={()=>abort.current?.abort()} reportError={reportError}/>
      </>}
      {page === 'schedules' && <Schedules api={api} agents={workspace.agents} models={usableModels} defaultModel={plainModel} reportError={reportError} notify={notify} openResult={async(name,id,shared=false,model)=>{try{const history=await api(shared?`/conversations/${id}`:`/sessions/${name}/${id}`);setChatMode(shared);setConnected('');setWebEnabled(shared?(await api(`/conversations/${id}/settings`)).web_enabled:([...history].reverse().find(m=>m.role==='user')?._web_enabled??false));if(model)setChatModel(model);if(name)setAgentName(name);setSession(id);setMessages(history.filter(m=>['user','assistant'].includes(m.role)&&m.content).map(m=>({role:m.role,text:typeof m.content==='string'?m.content:m.content.filter(p=>p.type==='text').map(p=>p.text).join('\n'),messageId:m._message_id,rating:m._feedback})));navigate('chat');}catch(e){reportError(e)}}}/>}

      {page === 'agents' && <div className="workspace-page"><div className="page-heading"><div><div className="eyebrow">ВАША КОМАНДА</div><h1>{agentDraft ? 'Конструктор агента' : 'Агенты'}</h1><p>Инструкции, модель и навыки — соберите своего помощника.</p></div>{agentDraft?<button className="button secondary" onClick={()=>{setAgentDraft(null);setError('')}}>К списку агентов</button>:<AgentCreator api={api} model={plainModel} onCreate={editAgent} reportError={reportError}/>}</div>{agentDraft ? <form onSubmit={saveAgent} className="builder-grid"><section className="panel"><div className="panel-heading"><Bot size={18}/><h2>Основное</h2></div><label>Имя агента<input required pattern="[a-z][a-z0-9_]{0,63}" value={agentDraft.name} placeholder="my_assistant" onChange={e => changeAgent('name', e.target.value)}/><small>Латинские буквы, цифры и подчёркивания</small></label><label>Модель<input list="models" required value={agentDraft.model} onChange={e => changeAgent('model', e.target.value)}/><datalist id="models">{workspace.models.map(m => <option key={m} value={m}/>)}</datalist><small>Провайдер:model. Дополнительные провайдеры — в config.yaml</small></label><label>Инструкции<textarea rows={9} required value={agentDraft.instructions} onChange={e => changeAgent('instructions', e.target.value)}/></label><label>Лимит шагов<input type="number" min="1" max="100" value={agentDraft.max_steps} onChange={e => changeAgent('max_steps', Number(e.target.value))}/></label></section><section className="panel"><div className="panel-heading"><Workflow size={18}/><h2>Навыки и подтверждения</h2></div><p className="muted">Выберите возможности агента. Для записи файлов и сетевых действий подтверждение запрашивается автоматически.</p>{workspace.skills.map(s => <div className="skill-option" key={s.name}><label className="checkbox-label"><input type="checkbox" checked={agentDraft.skills.includes(s.name)} onChange={e => {if(!e.target.checked){const m={...agentDraft.skill_models}; delete m[s.name]; changeAgent('skill_models',m);} changeAgent('skills', e.target.checked ? [...agentDraft.skills, s.name] : agentDraft.skills.filter(n => n !== s.name)); if (!e.target.checked) changeAgent('confirmation_required', agentDraft.confirmation_required.filter(n => n !== s.name));}}/><div><strong>{s.name}</strong><small>{s.description}</small></div></label>{agentDraft.skills.includes(s.name) && <label className="confirm-check"><input type="checkbox" checked={agentDraft.confirmation_required.includes(s.name)} onChange={e => changeAgent('confirmation_required', e.target.checked ? [...agentDraft.confirmation_required, s.name] : agentDraft.confirmation_required.filter(n => n !== s.name))}/>Всегда запрашивать подтверждение</label>}</div>)}{agentDraft.skills.map(name => <label key={name}>Модель обработки результата · {name}<input list="models" placeholder="Без отдельной модели" value={agentDraft.skill_models?.[name] || ''} onChange={e => {const mapping={...agentDraft.skill_models}; if(e.target.value) mapping[name]=e.target.value; else delete mapping[name]; changeAgent('skill_models',mapping);}}/></label>)}<div className="info-box"><ShieldCheck size={18}/><span>Пользовательский Python-код выполняется с ограничениями ОС. Доступ к данным и запись требуют подтверждения.</span></div><button className="button primary full" disabled={saving} type="submit">{saving ? <Loader2 className="spin" size={16}/> : <Check size={16}/>}Проверить и сохранить</button></section></form> : <><h2>Личные агенты</h2><div className="entity-grid">{workspace.agents.filter(a=>!a.name.startsWith('shared_')).map(a => <button className="entity-card" key={a.name} onClick={() => editAgent(a)}><span className="agent-avatar"><Bot size={22}/></span><h2>{display(a.name)}</h2><p>{a.instructions}</p><div className="card-meta"><code>{a.model}</code><span>{a.skills.length} навыка</span></div><div className="card-footer">Настроить агента<ArrowRight size={15}/></div></button>)}</div><SharedAgents api={api} refresh={refresh} notify={notify} reportError={reportError} agents={workspace.agents.filter(a=>!a.name.startsWith('shared_'))} onUse={chooseAgent}/></>}</div>}
      {page === 'skills' && <div className="workspace-page"><div className="page-heading"><div><div className="eyebrow">СТРОИТЕЛЬНЫЕ БЛОКИ</div><h1>{skillDraft ? 'Редактор навыка' : 'Навыки'}</h1><p>Независимые Python-модули с явными правами доступа.</p></div><button className="button primary" onClick={() => editSkill()}><Plus size={16}/>Новый навык</button></div>{skillDraft ? <><div className="editor-topline"><label>Имя навыка<input value={skillDraft.name} placeholder="new_skill" onChange={e => setSkillDraft({...skillDraft, name: e.target.value})}/></label><button className="button primary" disabled={saving || !skillDraft.name} onClick={saveSkill}><Check size={15}/>Сохранить навык</button></div><div className="editor-grid"><section className="editor-panel"><div className="editor-tabs"><button className={skillTab === 'python' ? 'active' : ''} onClick={() => setSkillTab('python')}><Code2 size={14}/>skill.py</button><button className={skillTab === 'metadata' ? 'active' : ''} onClick={() => setSkillTab('metadata')}><FileText size={14}/>SKILL.md</button><span>Python 3.11+</span></div>{skillTab === 'metadata' && <div className="metadata-form"><div className="metadata-form-row"><label>Имя в SKILL.md<input value={metadata.name || ''} onChange={e => changeMetadata({name: e.target.value})}/></label><label>Версия<input value={metadata.version || ''} onChange={e => changeMetadata({version: e.target.value})}/></label></div><label>Описание<textarea rows={2} value={metadata.description || ''} onChange={e => changeMetadata({description: e.target.value})}/></label><label>Разрешённые пути · через запятую<input value={(Array.isArray(metadata.permissions?.filesystem) ? metadata.permissions.filesystem : []).join(', ')} onChange={e => changeMetadata({permissions: {...metadata.permissions, filesystem: e.target.value.split(',').map(v => v.trim()).filter(Boolean)}})}/></label><label>Сетевые адреса · host:port<input value={(Array.isArray(metadata.permissions?.network) ? metadata.permissions.network : []).join(', ')} onChange={e => changeMetadata({permissions: {...metadata.permissions, network: e.target.value.split(',').map(v => v.trim()).filter(Boolean)}})}/></label><div className="metadata-form-row"><label className="checkbox-label"><input type="checkbox" checked={!!metadata.irreversible} onChange={e => changeMetadata({irreversible: e.target.checked})}/>Необратимое действие</label><label className="checkbox-label"><input type="checkbox" checked={!!metadata.permissions?.subprocess} onChange={e => changeMetadata({permissions: {...metadata.permissions, subprocess: e.target.checked}})}/>Подпроцессы</label></div></div>}<CodeEditor key={skillTab} value={skillTab === 'python' ? skillDraft.code : skillDraft.metadata} language={skillTab} onChange={v => setSkillDraft(d => ({...d, [skillTab === 'python' ? 'code' : 'metadata']: v}))}/></section><section className="panel test-panel"><div className="panel-heading"><Terminal size={17}/><h2>Проверка в песочнице</h2></div><label>Входные данные · JSON<textarea rows={8} className="mono" value={testInput} onChange={e => setTestInput(e.target.value)}/></label><button className="button secondary full" disabled={saving} onClick={testSkill}>{saving ? <Loader2 size={16} className="spin"/> : <ArrowRight size={16}/>}Запустить проверку</button><h3>Результат</h3><pre className="test-output">{testOutput ? pretty(testOutput) : 'Запустите навык с тестовыми данными.\nЗдесь появится результат выполнения.'}</pre></section></div></> : <div className="entity-grid">{workspace.skills.map(s => <button key={s.name} className="entity-card" onClick={() => editSkill(s.name)}><span className="skill-card-icon"><Code2 size={22}/></span><h2>{s.name}</h2><p>{s.description}</p><div className="card-meta"><code>v{s.version}</code><span><ShieldCheck size={13}/>{s.irreversible ? 'Подтверждение' : 'Изолирован'}</span></div><div className="card-footer">Открыть редактор<ArrowRight size={15}/></div></button>)}</div>}</div>}
      {page === 'audit' && <Audit api={api} isAdmin={!!workspace.current_user?.admin} workspace={workspace} reportError={reportError}/>}
    </main>{toast && <div className="toast" role="status"><CheckCircle2 size={17}/>{toast}</div>}
  </div>;
}

createRoot(document.getElementById('root')).render(<Login>{accountMenu => <App accountMenu={accountMenu}/>}</Login>);
