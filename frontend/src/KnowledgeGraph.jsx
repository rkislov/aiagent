import React, {useEffect, useState} from 'react';
import {BookOpen, Download, FileText} from 'lucide-react';
import Progress from './Progress';

const pageSize = 36;
const width = 826;
const rowHeight = 56;

function layout(nodes) {
  const collections = nodes.filter(node => node.type === 'collection');
  const documents = nodes.filter(node => node.type === 'document');
  const height = Math.max(174, collections.length * rowHeight + 24, Math.ceil(documents.length / 3) * rowHeight + 24);
  const position = new Map();
  collections.forEach((node, i) => position.set(node.id, {
    x: 16, y: (height - collections.length * rowHeight) / 2 + i * rowHeight, width: 174,
  }));
  documents.forEach((node, i) => position.set(node.id, {
    x: 242 + i % 3 * 195, y: (height - Math.ceil(documents.length / 3) * rowHeight) / 2 + Math.floor(i / 3) * rowHeight, width: 169,
  }));
  return {position, height};
}

export default function KnowledgeGraph({api, collections, initialCollection, reportError, onOpenCollection}) {
  const [collection, setCollection] = useState(initialCollection || '');
  const [offset, setOffset] = useState(0);
  const [graph, setGraph] = useState(null);
  const [selected, setSelected] = useState('');
  const [similarity, setSimilarity] = useState(true);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setSelected('');
    const query = new URLSearchParams({collection, offset});
    api('/knowledge/graph?' + query, {signal: controller.signal, cache: 'no-store'})
      .then(value => {if (!controller.signal.aborted) setGraph(value);})
      .catch(error => {if (!controller.signal.aborted) {setGraph(null); reportError(error);}})
      .finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, [api, collection, offset]);

  const nodes = graph?.nodes || [];
  const edges = (graph?.edges || []).filter(edge => similarity || edge.type === 'contains');
  const {position, height} = layout(nodes);
  const focus = nodes.find(node => node.id === selected);
  const neighbors = new Set(edges.flatMap(edge => edge.source === selected ? [edge.target] : edge.target === selected ? [edge.source] : []));
  const links = edges.filter(edge => edge.type === 'similar' && (edge.source === selected || edge.target === selected));
  const names = new Map(nodes.map(node => [node.id, node.label]));

  return <section className="panel knowledge-graph">
    <div className="graph-controls">
      <label>База знаний<select value={collection} onChange={e => {setCollection(e.target.value); setOffset(0);}}>
        <option value="">Все мои базы</option>
        {collections.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}
      </select></label>
      <label className="checkbox-label"><input type="checkbox" checked={similarity} onChange={e => setSimilarity(e.target.checked)}/>Показать похожие документы</label>
    </div>
    <p className="muted">Сплошная линия связывает базу с её документом. Пунктир показывает смысловую близость документов по сохранённым векторам.</p>
    {loading ? <div className="graph-loading" role="status"><Progress label="Строится граф"/><p>Строится граф…</p></div> : graph && <>
      {!graph.total_documents && <p className="muted">Добавьте документ или заметку в базу, чтобы появились связи.</p>}
      <div className="graph-viewport" tabIndex={0} aria-label="Схема баз и документов">
        <div className="graph-map" style={{width, height}}>
          <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} aria-hidden="true">
            {edges.map(edge => {
              const from = position.get(edge.source), to = position.get(edge.target);
              const related = edge.type === 'similar';
              const highlighted = edge.source === selected || edge.target === selected;
              return <path key={edge.type + edge.source + edge.target}
                className={`graph-link ${related ? 'similar' : ''} ${highlighted ? 'highlighted' : ''} ${selected && !highlighted ? 'faded' : ''}`}
                d={`M ${from.x + (related ? from.width / 2 : from.width)} ${from.y + 20} L ${to.x + (related ? to.width / 2 : 0)} ${to.y + 20}`}/>;
            })}
          </svg>
          {nodes.map(node => {
            const {x, y, width} = position.get(node.id);
            const Icon = node.type === 'collection' ? BookOpen : FileText;
            return <button key={node.id} className={`graph-node ${node.type} ${selected && selected !== node.id && !neighbors.has(node.id) ? 'faded' : ''}`}
              aria-pressed={node.id === selected} title={node.label} style={{left: x, top: y, width}}
              onClick={() => setSelected(current => current === node.id ? '' : node.id)}>
              <Icon size={16}/><span>{node.label}</span>
            </button>;
          })}
        </div>
      </div>
      {focus && <div className="graph-details">
        <strong>{focus.label}</strong>
        {focus.type === 'collection' ? <button className="button secondary" onClick={() => onOpenCollection(focus.collection_id)}>Открыть базу</button> :
          <a className="button secondary" href={`/api/files/${focus.file_id}/download`}><Download size={15}/>Скачать документ</a>}
        {!!links.length && <ul>{links.map(edge => {
          const id = edge.source === selected ? edge.target : edge.source;
          return <li key={id}><button className="button secondary" onClick={() => setSelected(id)}>{names.get(id)} · близость {edge.score.toFixed(2)}</button></li>;
        })}</ul>}
      </div>}
      {graph.total_documents > pageSize && <div className="pagination">
        <button className="button secondary" disabled={!offset} onClick={() => setOffset(v => Math.max(0, v - pageSize))}>Назад</button>
        <span>Документы {offset + 1}–{offset + graph.shown_documents} из {graph.total_documents}</span>
        <button className="button secondary" disabled={offset + graph.shown_documents >= graph.total_documents} onClick={() => setOffset(v => v + pageSize)}>Далее</button>
      </div>}
    </>}
  </section>;
}
