import React, {useState} from 'react';
import {Copy, ThumbsDown, ThumbsUp} from 'lucide-react';

export default function AnswerActions({text, rating, onRate, notify, reportError}) {
  const [saving, setSaving] = useState(false);
  async function rate(next) {
    setSaving(true);
    try {
      await onRate(rating === next ? null : next);
    } catch (error) {
      reportError(error);
    } finally {
      setSaving(false);
    }
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      notify('Ответ скопирован');
    } catch {
      reportError(Error('Не удалось скопировать ответ. Проверьте доступ к буферу обмена.'));
    }
  }
  return <div className="answer-actions mt-2 flex items-center gap-1" role="group" aria-label="Действия с ответом">
    <button type="button" aria-label="Хорошо" title="Хорошо" aria-pressed={rating === 'good'} disabled={saving} onClick={() => rate('good')}><ThumbsUp size={16}/></button>
    <button type="button" aria-label="Плохо" title="Плохо" aria-pressed={rating === 'bad'} disabled={saving} onClick={() => rate('bad')}><ThumbsDown size={16}/></button>
    <button type="button" aria-label="Копировать ответ" title="Копировать ответ" onClick={copy}><Copy size={16}/></button>
  </div>;
}
