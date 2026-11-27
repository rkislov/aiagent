import React, {useEffect, useRef, useState} from 'react';
import {Plus, Search, Settings2, ShieldCheck, UserRound} from 'lucide-react';
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
  const details = useRef(null);
  const load = () => api('/users').then(setUsers);

  useEffect(() => {load().catch(reportError);}, [api]);
  useEffect(() => {
    if (selection !== null && details.current && window.matchMedia('(max-width: 1000px)').matches) {
      details.current.scrollIntoView({block: 'nearest'});
    }
  }, [selection]);

  function select(value) {
    setSelection(value);
    setAction('');
    setProof(blankProof);
    setUsername('');
    setInitial('');
  }

  async function create(event) {
    event.preventDefault();
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
      reportError(error);
    } finally {
      setBusy(false);
    }
  }

  async function execute(event) {
    event.preventDefault();
    if (!action || !selection) return;
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
      reportError(error);
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

  return <div className="users-layout">
    <section className="panel users-directory">
      <div className="users-toolbar">
        <h2>Учётные записи</h2>
        <button className="button primary" disabled={busy} onClick={() => select(false)}><Plus size={16}/>Добавить пользователя</button>
      </div>
      <div className="users-filters">
        <label className="users-search"><Search size={16}/><input type="search" aria-label="Поиск по логину" placeholder="Поиск по логину" value={search} onChange={event => setSearch(event.target.value)}/></label>
        <select aria-label="Фильтр пользователей" value={filter} onChange={event => setFilter(event.target.value)}>
          <option value="all">Все пользователи</option>
          <option value="admins">Администраторы</option>
          <option value="blocked">Заблокированные</option>
        </select>
      </div>
      <div className="user-list">
        {!users && <Progress label="Загружаются учётные записи"/>}
        {users && !visible.length && <p className="muted">Пользователи не найдены.</p>}
        {visible.map(user => <button className="managed-user" key={user.id} aria-pressed={selection === user.username}
          disabled={busy} onClick={() => select(user.username)}>
          <span className="user-avatar"><UserRound size={18}/></span>
          <span><strong>{user.username}{user.id === currentUser?.id ? ' · Вы' : ''}</strong>
            <small>{user.admin ? 'Администратор' : 'Пользователь'} · {user.enrolled ? '2FA включена' : '2FA ожидает настройки'}</small></span>
          <span className={'status-pill ' + (user.disabled ? 'denied' : 'ok')}>{user.disabled ? 'Заблокирован' : 'Активен'}</span>
          <Settings2 size={17} aria-hidden="true"/>
        </button>)}
      </div>
    </section>
    {selection === false && <section className="panel user-details" ref={details}>
      <h2>Новый пользователь</h2>
      <form onSubmit={create}>
        <label>Логин<input required pattern="[a-z][a-z0-9_]{2,40}" value={username} autoComplete="off" onChange={event => setUsername(event.target.value)}/></label>
        <label>Начальный пароль<input type="password" required minLength={12} maxLength={256} autoComplete="new-password" value={initial} onChange={event => setInitial(event.target.value)}/></label>
        <div className="button-row"><button className="button primary" disabled={busy}>Создать пользователя</button>
          <button type="button" className="button secondary" disabled={busy} onClick={() => select(null)}>Отмена</button></div>
      </form>
      {busy && <Progress label="Создаётся пользователь"/>}
    </section>}
    {chosen && <section className="panel user-details" ref={details}>
      <h2>{chosen.username}</h2>
      <p className="muted">{chosen.admin ? 'Администратор' : 'Пользователь'} · {chosen.enrolled ? '2FA включена' : '2FA ожидает настройки'}</p>
      <form onSubmit={execute}>
        <label>Действие<select value={action} disabled={busy} onChange={event => {setAction(event.target.value); setProof(blankProof);}}>
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
    </section>}
  </div>;
}
