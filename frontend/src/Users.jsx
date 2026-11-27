import React, {useEffect, useRef, useState} from 'react';
import {ArrowUpRight, Plus, Search, ShieldCheck, ShieldMinus, UserRound, X} from 'lucide-react';
import Progress from './Progress';

const labels = {
  block: 'Заблокировать', unblock: 'Разблокировать', promote: 'Назначить администратором',
  demote: 'Снять роль администратора', reset_password: 'Сбросить пароль',
  reset_2fa: 'Сбросить 2FA', revoke_sessions: 'Завершить сессии',
};
const post = body => ({method: 'POST', body: JSON.stringify(body)});
const blankProof = {password: '', code: '', new_password: ''};

export default function Users({api, currentUser, notify, reportError}) {
  const [users, setUsers] = useState(null);
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [selection, setSelection] = useState(null);
  const [action, setAction] = useState('');
  const [username, setUsername] = useState('');
  const [initial, setInitial] = useState('');
  const [proof, setProof] = useState(blankProof);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState('');
  const details = useRef(null);
  const load = () => api('/users').then(setUsers);

  useEffect(() => {load().catch(reportError);}, [api]);
  useEffect(() => {
    const dialog = details.current;
    if (selection !== null && !dialog.open) dialog.showModal();
    if (selection === null && dialog.open) dialog.close();
  }, [selection]);

  function select(value) {
    setSelection(value);
    setAction('');
    setProof(blankProof);
    setUsername('');
    setInitial('');
    setFormError('');
  }

  async function create(event) {
    event.preventDefault();
    setFormError('');
    setBusy(true);
    try {
      await api('/users', post({username, password: initial}));
      const created = username;
      select(created);
      setFilter('all');
      setSearch('');
      await load();
      notify('Пользователь создан. При первом входе потребуется настроить 2FA.');
    } catch (error) {
      setFormError(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function execute(event) {
    event.preventDefault();
    if (!action || !selection) return;
    setFormError('');
    setBusy(true);
    try {
      const endpoint = action === 'reset_2fa' ? '/users/reset-2fa' : '/users/manage';
      await api(endpoint, post({username: selection, action, ...proof}));
      setAction('');
      setProof(blankProof);
      if (selection === currentUser?.username && action === 'revoke_sessions') {
        window.location.reload();
        return;
      }
      await load();
      notify('Учётная запись обновлена');
    } catch (error) {
      setFormError(error.message);
    } finally {
      setBusy(false);
    }
  }

  const visible = (users || []).filter(user => user.username.includes(search.trim().toLowerCase()) &&
    (filter === 'all' || filter === 'admins' && user.admin || filter === 'blocked' && user.disabled));
  const chosen = users?.find(user => user.username === selection);
  const self = chosen?.id === currentUser?.id;
  const actions = chosen ? self ? ['revoke_sessions'] : [
    chosen.disabled ? 'unblock' : 'block', chosen.admin ? 'demote' : 'promote',
    'reset_password', ...(chosen.enrolled ? ['reset_2fa'] : []), 'revoke_sessions',
  ] : [];

  return <section className="account-directory mx-auto" aria-labelledby="account-directory-title">
    <header className="account-directory-header flex flex-wrap items-center justify-between gap-3">
      <h1 id="account-directory-title">Пользователи</h1>
      <button className="button primary" disabled={busy} onClick={() => select(false)}><Plus size={16}/>Добавить пользователя</button>
    </header>
    <div className="account-directory-filters grid gap-2">
      <div className="account-search-field relative min-w-0">
        <Search size={17} aria-hidden="true"/>
        <input type="search" aria-label="Поиск по логину" placeholder="Поиск по логину" value={search} onChange={event => setSearch(event.target.value)}/>
      </div>
      <select aria-label="Фильтр пользователей" value={filter} onChange={event => setFilter(event.target.value)}>
        <option value="all">Все пользователи</option>
        <option value="admins">Администраторы</option>
        <option value="blocked">Заблокированные</option>
      </select>
    </div>
    {!users && <Progress label="Загружаются учётные записи"/>}
    {users && !visible.length && <p className="account-directory-empty">Пользователи не найдены.</p>}
    <ul className="account-cards grid gap-3.5">
      {visible.map(user => <li className="account-card floating-tile grid gap-3.5 p-4" key={user.id}>
        <div className="account-card-heading flex min-w-0 items-center gap-3">
          <span className={'account-card-avatar ' + (user.admin ? 'administrator' : '')} aria-hidden="true">
            {user.admin ? <ShieldCheck size={20}/> : <UserRound size={20}/>}
          </span>
          <div className="min-w-0">
            <h2>{user.username}{user.id === currentUser?.id && <span>Вы</span>}</h2>
            <p>{user.admin ? 'Администратор' : 'Пользователь'}</p>
          </div>
        </div>
        <div className="account-card-status flex flex-wrap items-center gap-2">
          <span className={'account-state ' + (user.disabled ? 'blocked' : 'active')}>{user.disabled ? 'Заблокирован' : 'Активен'}</span>
          <span className="account-2fa" title={user.enrolled ? '2FA включена' : '2FA ожидает настройки'}>
            {user.enrolled ? <ShieldCheck size={14}/> : <ShieldMinus size={14}/>}
            {user.enrolled ? '2FA включена' : '2FA не настроена'}
          </span>
        </div>
        <button className="account-card-open flex items-center justify-between gap-2" type="button" disabled={busy}
          aria-label={'Управлять пользователем ' + user.username} aria-haspopup="dialog" onClick={() => select(user.username)}>
          Управлять <ArrowUpRight size={16} aria-hidden="true"/>
        </button>
      </li>)}
    </ul>
    <dialog className="account-editor floating-tile" ref={details} aria-labelledby="account-editor-title"
      onCancel={event => {event.preventDefault(); if (!busy) select(null);}}>
      <div className="account-editor-heading flex items-start justify-between gap-3">
        <div>
          <h2 id="account-editor-title">{chosen?.username || 'Новый пользователь'}</h2>
          {chosen && <p>{chosen.admin ? 'Администратор' : 'Пользователь'} · {chosen.enrolled ? '2FA включена' : '2FA не настроена'}</p>}
        </div>
        <button className="icon-button" type="button" aria-label="Закрыть" disabled={busy} onClick={() => select(null)}><X size={18}/></button>
      </div>
      {formError && <p className="account-editor-error" role="alert">{formError}</p>}
      {selection === false && <>
        <form onSubmit={create}>
          <label>Логин<input required pattern="[a-z][a-z0-9_]{2,40}" value={username} autoComplete="off" onChange={event => setUsername(event.target.value)}/></label>
          <label>Начальный пароль<input type="password" required minLength={12} maxLength={256} autoComplete="new-password" value={initial} onChange={event => setInitial(event.target.value)}/></label>
          <div className="button-row"><button className="button primary" disabled={busy}>Создать пользователя</button>
            <button type="button" className="button secondary" disabled={busy} onClick={() => select(null)}>Отмена</button></div>
        </form>
        {busy && <Progress label="Создаётся пользователь"/>}
      </>}
      {chosen && <>
        <form onSubmit={execute}>
          <label>Действие<select value={action} disabled={busy} onChange={event => {setAction(event.target.value); setProof(blankProof); setFormError('');}}>
            <option value="">Выберите действие</option>
            {actions.map(value => <option key={value} value={value}>{labels[value]}</option>)}
          </select></label>
          {action && <>
            <p className="user-proof-note"><ShieldCheck size={17}/>Подтвердите действие своим паролем и кодом 2FA или резервным кодом.</p>
            {action === 'reset_password' && <label>Новый пароль пользователя<input type="password" required minLength={12} maxLength={256}
              autoComplete="new-password" value={proof.new_password} onChange={event => setProof({...proof, new_password: event.target.value})}/></label>}
            <label>Ваш пароль<input type="password" required autoComplete="current-password" value={proof.password}
              onChange={event => setProof({...proof, password: event.target.value})}/></label>
            <label>Ваш код 2FA или резервный код<input required autoComplete="one-time-code" value={proof.code}
              onChange={event => setProof({...proof, code: event.target.value})}/></label>
            <div className="button-row"><button className="button primary" disabled={busy}>Подтвердить</button>
              <button type="button" className="button secondary" disabled={busy} onClick={() => select(selection)}>Отмена</button></div>
          </>}
        </form>
        {busy && <Progress label="Обновляется учётная запись"/>}
      </>}
    </dialog>
  </section>;
}
