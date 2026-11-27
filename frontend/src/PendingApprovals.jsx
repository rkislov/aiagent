import React, {useEffect, useState} from 'react';
import {Check, ShieldCheck} from 'lucide-react';
import {skillLabel} from './SkillActivity';

export default function PendingApprovals({api, revision, onDecision, reportError}) {
  const [rows, setRows] = useState([]);
  const [submitting, setSubmitting] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    let timer;
    async function load() {
      try {
        const approvals = await api('/confirmations', {signal: controller.signal, cache: 'no-store'});
        if (!controller.signal.aborted) setRows(approvals);
      } catch {
        // An interrupted background request can be retried on the next refresh.
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(load, 2000);
      }
    }
    load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [api, revision]);

  async function decide(id, approved) {
    setSubmitting(id);
    try {
      await api('/confirmations/' + id, {method: 'POST', body: JSON.stringify({approved})});
      setRows(current => current.filter(row => row.id !== id));
      onDecision(id, approved);
    } catch (error) {
      reportError(error);
    } finally {
      setSubmitting('');
    }
  }

  if (!rows.length) return null;
  return <section className="pending-approvals" aria-label="Ожидающие подтверждения">
    <div className="approval-title" role="status"><ShieldCheck size={18}/>Требуется ваше подтверждение{rows.length > 1 ? ' · ' + rows.length : ''}</div>
    {rows.map(row => <article key={row.id} className="pending-approval">
      <strong>{skillLabel(row.skill)}</strong>
      <details open={rows.length === 1}>
        <summary>Детали действия и запрашиваемые права</summary>
        <pre>{JSON.stringify(row.preview || row.input, null, 2)}</pre>
        <pre>{JSON.stringify(row.permissions, null, 2)}</pre>
      </details>
      <div className="button-row">
        <button className="button primary" disabled={!!submitting} onClick={() => decide(row.id, true)}><Check size={15}/>Разрешить действие</button>
        <button className="button secondary" disabled={!!submitting} onClick={() => decide(row.id, false)}>Отклонить</button>
      </div>
    </article>)}
  </section>;
}
