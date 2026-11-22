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
