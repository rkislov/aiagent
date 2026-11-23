export async function readEvents(response, update) {
 if(!response.headers.get('content-type')?.includes('text/event-stream'))throw Error('Сервер вернул неожиданный ответ. Обновите страницу и повторите запрос.');
 const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',finished=false;
 function frames(final=false){
  if(final&&buffer.trim())buffer+='\n\n';
  let match;while((match=/\r?\n\r?\n/.exec(buffer))){
   const frame=buffer.slice(0,match.index);buffer=buffer.slice(match.index+match[0].length);
   const data=frame.split(/\r?\n/).filter(line=>line.startsWith('data:')).map(line=>line.slice(5).replace(/^ /,'')).join('\n');
   if(data){const event=JSON.parse(data);if(event.type==='done'||event.type==='error')finished=true;update(event);}
  }
 }
 try{while(true){const {done,value}=await reader.read();if(done){buffer+=decoder.decode();frames(true);break;}buffer+=decoder.decode(value,{stream:true});frames();}}
 finally{reader.releaseLock();}
 if(!finished)throw Error('Поток ответа прерван. Откройте диалог из истории или повторите запрос.');
}

export async function readPollingEvents(runId,update,signal){
 let terminal=false;
 while(!signal.aborted){
  const response=await fetch('/api/chat/runs/'+runId,{signal,cache:'no-store'});
  if(!response.ok)throw Error('Не удалось получить ответ агента. Откройте диалог из истории.');
  const data=await response.json();
  for(const event of data.events){if(['done','error'].includes(event.type))terminal=true;update(event);}
  if(data.done){if(!terminal)throw Error('Запуск завершился без ответа. Проверьте историю диалога.');return;}
  await new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>{signal.removeEventListener('abort',abort);resolve()},250);
   function abort(){clearTimeout(timer);signal.removeEventListener('abort',abort);reject(new DOMException('Stopped','AbortError'))}
   signal.addEventListener('abort',abort,{once:true});
   if(signal.aborted)abort();
  });
 }
 throw new DOMException('Stopped','AbortError');
}
