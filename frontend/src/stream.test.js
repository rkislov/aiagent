import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readEvents,readPollingEvents} from './stream.js';
function response(chunks){return new Response(new ReadableStream({start(c){for(const text of chunks)c.enqueue(new TextEncoder().encode(text));c.close()}}),{headers:{'Content-Type':'text/event-stream'}})}
test('frames tolerate CRLF, chunks and a final frame without separator',async()=>{
 const events=[];await readEvents(response([': keepalive\r\n\r\ndata: {"type":"del','ta","text":"Привет"}\r\n\r\ndata:{"type":"done","content":"Привет"}']),e=>events.push(e));
 assert.deepEqual(events.map(e=>e.type),['delta','done']);assert.equal(events[1].content,'Привет');
});
test('premature end is explicit',async()=>{await assert.rejects(readEvents(response(['data: {"type":"session"}\n\n']),()=>{}),/прерван/)});
test('HTML rejection is explicit',async()=>{await assert.rejects(readEvents(new Response('<html>blocked</html>'),()=>{}),/неожиданный/)});

test('polling delivers confirmation and completed response',async(t)=>{
 const events=[];t.mock.method(globalThis,'fetch',async()=>new Response(JSON.stringify({events:[{type:'confirmation',id:'approval'},{type:'done',content:'ok'}],done:true}),{headers:{'Content-Type':'application/json'}}));
 await readPollingEvents('run',e=>events.push(e),new AbortController().signal);assert.deepEqual(events.map(e=>e.type),['confirmation','done']);
});
test('polling respects cancellation',async()=>{const c=new AbortController();c.abort();await assert.rejects(readPollingEvents('run',()=>{},c.signal),e=>e.name==='AbortError')});
