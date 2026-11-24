import React,{useState,useRef,useEffect} from 'react';
import {ImagePlus,Mic,Globe,X,ArrowUp,Square,Circle} from 'lucide-react';

async function photo(file){
 if(!['image/jpeg','image/png','image/webp'].includes(file.type)||file.size>15_000_000)throw Error('Выберите JPEG, PNG или WebP размером до 15 МБ');
 const bitmap=await createImageBitmap(file);
 try{
  const scale=Math.min(1,1536/Math.max(bitmap.width,bitmap.height));
  const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(bitmap.width*scale));canvas.height=Math.max(1,Math.round(bitmap.height*scale));
  const ctx=canvas.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.drawImage(bitmap,0,0,canvas.width,canvas.height);
  let data;for(const quality of [.85,.7,.5,.3]){data=canvas.toDataURL('image/jpeg',quality);if(data.length<=400023)return {name:file.name,url:data};}
  throw Error('Фото слишком детализировано. Выберите изображение меньшего размера.');
 }finally{bitmap.close();}
}

export default function Composer({input,setInput,busy,agent,send,stop,reportError,web,setWeb,webBusy=false}){
 const [images,setImages]=useState([]),[listening,setListening]=useState(false),[loading,setLoading]=useState(false);
 const picker=useRef(null),recognition=useRef(null);
 useEffect(()=>()=>recognition.current?.abort(),[]);
 useEffect(()=>{setImages([]);recognition.current?.abort();},[agent?.name]);
 useEffect(()=>{if(busy)recognition.current?.stop();},[busy]);
 async function choose(e){const files=Array.from(e.target.files||[]);e.target.value='';if(!files.length)return;
  if(images.length+files.length>4){reportError(Error('Можно прикрепить до четырёх фотографий'));return;}
  setLoading(true);try{const selected=await Promise.all(files.map(photo));setImages(current=>[...current,...selected].slice(0,4));}catch(e){reportError(e)}finally{setLoading(false)}
 }
 function microphone(){
  if(listening){recognition.current?.stop();return;}
  const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition;
  if(!Recognition){reportError(Error('Голосовой ввод недоступен в этом браузере. Откройте сайт в Chrome или Safari с поддержкой распознавания речи.'));return;}
  const r=new Recognition();recognition.current=r;r.lang='ru-RU';r.continuous=true;r.interimResults=false;
  r.onresult=e=>{let text='';for(let i=e.resultIndex;i<e.results.length;i++)if(e.results[i].isFinal)text+=e.results[i][0].transcript+' ';if(text)setInput(current=>(current?current+' ':'')+text.trim());};
  r.onend=()=>{setListening(false);recognition.current=null;};
  r.onerror=e=>{setListening(false);if(e.error!=='aborted')reportError(Error(e.error==='not-allowed'?'Разрешите браузеру доступ к микрофону.':'Не удалось распознать речь: '+e.error));};
  try{r.start();setListening(true)}catch(e){reportError(e)}
 }
 function submit(e){e?.preventDefault();if(busy||webBusy||loading||!agent||(!input.trim()&&!images.length))return;recognition.current?.stop();send(input.trim()||'Опиши прикреплённые фотографии.',images.map(i=>i.url),web);setImages([]);}
 return <div className="composer-wrap"><form className="composer" onSubmit={submit}>
  {!!images.length&&<div className="photo-previews">{images.map((image,i)=><div className="photo-preview" key={i}><img src={image.url} alt={image.name}/><button type="button" aria-label={'Убрать '+image.name} onClick={()=>setImages(current=>current.filter((_,j)=>j!==i))}><X size={14}/></button></div>)}</div>}
  <textarea aria-label="Сообщение агенту" value={input} onChange={e=>setInput(e.target.value)} placeholder="Какую задачу выполним?" rows={2} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();submit();}}}/>
  <div className="composer-bottom"><div className="composer-tools"><input hidden ref={picker} type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={choose}/>
   <button type="button" className="composer-tool" title="Прикрепить фото (до 4). После отправки фото получит выбранная модель." aria-label="Прикрепить фото" disabled={busy||loading||images.length===4} onClick={()=>picker.current?.click()}><ImagePlus size={19}/></button>
   <button type="button" className={'composer-tool '+(listening?'recording':'')} title="Голосовой ввод: браузер может использовать сервис распознавания речи" aria-label={listening?'Остановить микрофон':'Голосовой ввод'} aria-pressed={listening} disabled={busy} onClick={microphone}><Mic size={19}/></button>
   <button type="button" className={'composer-tool '+(web?'active':'')} title={web?'Выключить веб-поиск и чтение страниц':'Включить веб-поиск и чтение страниц'} aria-label="Веб-поиск и чтение страниц" aria-pressed={web} disabled={busy||webBusy} onClick={()=>setWeb(!web)}><Globe size={19}/>{web&&<span>Веб</span>}</button>
   <span className="model-label"><Circle size={10}/>{agent?.model||'Выберите агента'}</span></div>
   {busy?<button type="button" className="send-button" aria-label="Остановить" onClick={stop}><Square size={15} fill="currentColor"/></button>:<button className="send-button" aria-label="Отправить" disabled={webBusy||loading||(!input.trim()&&!images.length)||!agent}><ArrowUp size={19}/></button>}
  </div>{listening&&<div className="voice-status" role="status">Слушаю… Текст появится в поле. Нажмите микрофон, чтобы остановить.</div>}{loading&&<div role="status">Подготовка фото…</div>}
 </form><div className="composer-note">{web?'Веб включён · ссылки и текст страниц доступны агенту':'Проверяйте важные результаты. Агент может ошибаться.'}<span>Enter — отправить · Shift + Enter — новая строка</span></div></div>
}
