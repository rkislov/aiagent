import React, {useEffect, useRef, useState} from 'react';
import {Bot, History, Menu, MessageSquare, Plug, Plus, SlidersHorizontal, Unplug} from 'lucide-react';
import ThemeToggle from './ThemeToggle';

export function NavigationToggle({open, onToggle}) {
  const label = open ? 'Скрыть боковую панель' : 'Открыть боковую панель';
  return <button type="button" className="navigation-toggle flex shrink-0 items-center gap-1.5 rounded-xl"
    aria-label={label} title={label} aria-expanded={open} aria-controls="workspace-sidebar" onClick={onToggle}>
    <Menu size={17} aria-hidden="true"/><span>ai</span>
  </button>;
}

export default function ChatToolbar({
  models, model, setModel, agents, connected, onAgent, busy, legacy, sources = [], selectedSources,
  onSources, onSettings, knowledgePicker, menuOpen, onToggleMenu, onNewChat, onOpenSession,
  sessions, session, title, accountMenu,
}) {
  const [panel, setPanel] = useState(null);
  const toolbar = useRef(null);
  const triggers = useRef({});
  const enabled = sources.filter(source => source.enabled);
  const selected = selectedSources ?? enabled.map(source => source.id);
  const count = enabled.filter(source => selected.includes(source.id)).length;
  const agent = agents.find(item => item.name === connected);

  useEffect(() => {
    if (!panel) return;
    function outside(event) {
      if (!toolbar.current?.contains(event.target)) setPanel(null);
    }
    function escape(event) {
      if (event.key === 'Escape') {
        setPanel(null);
        triggers.current[panel]?.focus();
      }
    }
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [panel]);

  function toggle(next) {
    setPanel(current => current === next ? null : next);
  }

  return <header className="chat-header floating-tile relative flex flex-wrap items-center gap-2.5 rounded-2xl" ref={toolbar}>
    <NavigationToggle open={menuOpen} onToggle={onToggleMenu}/>
    <div className="chat-heading min-w-0 flex-1">
      <h1 title={title}>{title || (legacy ? 'Диалог агента' : 'Новый диалог')}</h1>
      <p title={agent?.display_name || model}>{agent ? 'Агент · ' + agent.display_name : 'Обычный чат'}</p>
    </div>
    <div className="chat-header-actions flex items-center gap-1.5">
      <button type="button" className="chat-toolbar-button" title="История диалогов" aria-label="История диалогов"
        aria-expanded={panel === 'history'} aria-controls="chat-history" ref={node => {triggers.current.history = node;}}
        onClick={() => toggle('history')}><History size={17}/><span>История</span></button>
      <button type="button" className="chat-toolbar-button" title="Настройки диалога" aria-label="Настройки диалога"
        aria-expanded={panel === 'settings'} aria-controls="chat-settings" ref={node => {triggers.current.settings = node;}}
        onClick={() => toggle('settings')}><SlidersHorizontal size={17}/><span>Настройки</span></button>
      <button type="button" className="button primary chat-new-button" title="Начать с чистого контекста. Прежний диалог останется в истории."
        disabled={busy} onClick={() => {setPanel(null); onNewChat();}}><Plus size={17}/>Новый диалог</button>
    </div>
    {panel === 'history' && <section id="chat-history" className="chat-popover floating-tile" aria-label="Недавние диалоги">
      <h2>Недавние диалоги</h2>
      <div className="chat-history-list">
        {sessions.map(item => <button key={item.id} title={item.title} aria-current={item.id === session ? 'true' : undefined}
          disabled={busy} onClick={() => {setPanel(null); onOpenSession(item);}}><MessageSquare size={16}/><span>{item.title}</span></button>)}
        {!sessions.length && <p>Здесь появятся ваши диалоги</p>}
      </div>
    </section>}
    {panel === 'settings' && <section id="chat-settings" className="chat-popover floating-tile" aria-label="Настройки диалога">
      <h2>Настройки диалога</h2>
      <div className="chat-settings-grid grid gap-3">
        <label>Модель чата
          <select aria-label="Модель чата" value={model} disabled={busy} onChange={event => setModel(event.target.value)}>
            {models.map(item => <option key={item}>{item}</option>)}
          </select>
        </label>
        <label><span className="flex items-center gap-1.5"><Bot size={14}/>Агент</span>
          <select aria-label="Подключить агента" value={connected || ''} disabled={busy} onChange={event => onAgent(event.target.value)}>
            <option value="">Без агента — обычный чат</option>
            {agents.map(item => <option key={item.name} value={item.name}>{item.display_name || item.name}</option>)}
          </select>
        </label>
      </div>
      <div className="chat-context-options grid gap-2">
        {connected ? <button type="button" className="button secondary" disabled={busy} onClick={() => onAgent('')}><Unplug size={15}/>Отключить агента</button> :
          <details className="chat-sources">
            <summary><Plug size={15}/>Подключения · {count}</summary>
            <div className="chat-sources-menu">
              <strong>Источники этого чата</strong>
              <p>Разрешено только чтение после подтверждения.</p>
              {sources.map(source => <label className="checkbox-label" key={source.id}>
                <input type="checkbox" disabled={busy || !source.enabled} checked={source.enabled && selected.includes(source.id)}
                  onChange={event => onSources(event.target.checked ? [...selected, source.id] : selected.filter(id => id !== source.id))}/>
                {source.title}{!source.enabled && <small>не настроено</small>}
              </label>)}
              <button type="button" className="button secondary" disabled={busy} onClick={onSettings}>Настроить подключения</button>
              <button type="button" className="button secondary" disabled={busy} onClick={() => onSources([])}>Отключить все в чате</button>
            </div>
          </details>}
        {knowledgePicker}
      </div>
      {connected && <p className="chat-context-hint">Агент использует свою модель и навыки. При его отключении история диалога сохраняется.</p>}
      <div className="chat-settings-footer flex flex-wrap items-center justify-between gap-2"><ThemeToggle/>{accountMenu}</div>
    </section>}
  </header>;
}
