import React from 'react';
import {ChevronRight, MessageSquare, PanelLeftClose, PanelLeftOpen, Plus, Settings2, ShieldCheck, Zap} from 'lucide-react';

export default function WorkspaceSidebar({
  mode, mobile, groups, onToggleGroup, navigation, page, busy, onNavigate, onResize, onClose,
  agents, activeAgent, onSelectAgent, onAddAgent, sessions, session, onOpenSession, onNewChat,
}) {
  if (mode === 'closed') return null;
  const expanded = mode === 'expanded';
  const renderLinks = items => items.map(({id, icon: Icon, text}) =>
    <button key={id} className={page === id ? 'active' : ''} aria-current={page === id ? 'page' : undefined}
      aria-label={text} title={expanded ? undefined : text} disabled={busy} onClick={() => onNavigate(id)}>
      <Icon size={18} aria-hidden="true"/>{expanded && <span>{text}</span>}
    </button>);
  const work = navigation.filter(item => item.group === 'work');
  const management = navigation.filter(item => item.group === 'management');

  return <>
    {mobile && <button type="button" className="menu-backdrop" aria-label="Закрыть меню" onClick={onClose}/>}
    <aside id="workspace-sidebar" aria-label="Боковая панель" className={`workspace-sidebar floating-tile ${mode}`}>
      <div className="sidebar-brand-row">
        <div className="sidebar-brand"><span className="sidebar-emblem"><Zap size={17} aria-hidden="true"/></span>{expanded && <strong>aiagent<span>.</span></strong>}</div>
        {expanded && <button type="button" className="sidebar-resize" aria-label="Сузить панель" title="Сузить панель" onClick={onResize}><PanelLeftClose size={18}/></button>}
      </div>
      <div className="workspace-sidebar-scroll">
        {expanded && <span className="sidebar-caption">Работа</span>}
        <nav aria-label="Работа" className="workspace-nav">{renderLinks(work)}</nav>
        {expanded && <div className="sidebar-lists">
          <div className="sidebar-list-heading">
            <button aria-expanded={groups.dialogs} aria-controls="sidebar-dialog-list" onClick={() => onToggleGroup('dialogs')}><ChevronRight size={14}/>Недавние диалоги</button>
            <button aria-label="Начать новый диалог" title="Начать новый диалог" disabled={busy} onClick={onNewChat}><Plus size={15}/></button>
          </div>
          {groups.dialogs && <div id="sidebar-dialog-list" className="workspace-recents">
            {sessions.map(item => <button key={item.id} title={item.title} disabled={busy} aria-current={item.id === session ? 'true' : undefined}
              onClick={() => onOpenSession(item)}><MessageSquare size={14}/><span>{item.title}</span></button>)}
            {!sessions.length && <p>Здесь появятся ваши диалоги</p>}
          </div>}
          <div className="sidebar-list-heading">
            <button aria-expanded={groups.agents} aria-controls="sidebar-agent-list" onClick={() => onToggleGroup('agents')}><ChevronRight size={14}/>Мои агенты</button>
            <button aria-label="Добавить агента" title="Добавить агента" disabled={busy} onClick={onAddAgent}><Plus size={15}/></button>
          </div>
          {groups.agents && <div id="sidebar-agent-list" className="workspace-recents">
            {agents.map(item => <button key={item.name} title={item.label} aria-pressed={activeAgent === item.name} disabled={busy}
              onClick={() => onSelectAgent(item.name)}><item.icon size={14}/><span>{item.label}</span></button>)}
            {!agents.length && <p>Создайте первого агента</p>}
          </div>}
        </div>}
        {expanded ? <details className="sidebar-management" open={groups.management} onToggle={event => onToggleGroup('management', event.currentTarget.open)}>
          <summary><Settings2 size={17}/>Управление<ChevronRight size={14} className="management-arrow"/></summary>
          <nav aria-label="Управление" className="workspace-nav">{renderLinks(management)}</nav>
        </details> : <nav aria-label="Управление" className="workspace-nav rail-management">
          <button type="button" title="Управление" aria-label="Развернуть управление" onClick={() => {onToggleGroup('management', true); onResize();}}><Settings2 size={18}/></button>
        </nav>}
      </div>
      {expanded ? <div className="workspace-sidebar-footer"><ShieldCheck size={15}/>Личное пространство</div> :
        <button type="button" className="sidebar-resize rail-expand" aria-label="Развернуть панель" title="Развернуть панель" onClick={onResize}><PanelLeftOpen size={19}/></button>}
    </aside>
  </>;
}
