"""Map personal collections and document similarity using stored embeddings."""

from contextlib import closing
from itertools import combinations
from math import sqrt
import json


def _document_vector(db, document):
    rows = db.execute('''SELECT signature, vector FROM chunks
                         WHERE collection=? AND file=? ORDER BY part LIMIT 4''',
                      (document['collection_id'], document['file_id'])).fetchall()
    if not rows:
        return None
    signature = rows[0][0]
    vectors = [json.loads(raw) for key, raw in rows if key == signature]
    dimension = len(vectors[0])
    centroid = [sum(values) for values in zip(*(vector for vector in vectors if len(vector) == dimension))]
    norm = sqrt(sum(value * value for value in centroid))
    return (signature, dimension, [value / norm for value in centroid]) if norm else None


def graph(store, collection=None, *, offset=0, limit=36):
    listing = store.list([collection] if collection else None)
    by_file = {}
    for document in listing['documents']:
        by_file.setdefault(document['file_id'], []).append(document)
    page = list(by_file.items())[offset:offset + limit]
    nodes = [{'id': 'collection:' + c['id'], 'type': 'collection', 'label': c['name'],
              'collection_id': c['id'], 'documents': c['documents']} for c in listing['collections']]
    edges, vectors = [], {}
    with closing(store.connect()) as db:
        for file_id, memberships in page:
            node_id = 'document:' + file_id
            nodes.append({'id': node_id, 'type': 'document', 'label': memberships[0]['name'],
                          'file_id': file_id, 'collections': [row['collection_id'] for row in memberships]})
            edges.extend({'source': 'collection:' + row['collection_id'], 'target': node_id, 'type': 'contains'}
                         for row in memberships)
            vector = _document_vector(db, memberships[0])
            if vector:
                vectors[node_id] = vector

    related = []
    for (source, (src_model, src_dim, left)), (target, (dst_model, dst_dim, right)) in combinations(vectors.items(), 2):
        if (src_model, src_dim) == (dst_model, dst_dim):
            score = sum(a * b for a, b in zip(left, right))
            if score >= 0.75:
                related.append((score, source, target))
    degree = dict.fromkeys(vectors, 0)
    for score, source, target in sorted(related, reverse=True):
        if degree[source] < 3 and degree[target] < 3:
            edges.append({'source': source, 'target': target, 'type': 'similar', 'score': round(min(score, 1), 3)})
            degree[source] += 1
            degree[target] += 1
    return {'nodes': nodes, 'edges': edges, 'total_documents': len(by_file), 'shown_documents': len(page)}
