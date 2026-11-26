import React,{useEffect,useRef,useState} from 'react';
const official=new Set(['https://api.openai.com/v1','https://api.deepseek.com','https://api.deepseek.com/v1']);
export default function ModelDiscovery({name,entry,onModels}) {
 const [status,setStatus]=useState({}),[retry,setRetry]=useState(0);
 const callback=useRef(onModels);callback.current=onModels;
 const base=(entry.base_url||'').replace(/\/+$/,'');
 const supported=entry.type==='openai'&&official.has(base);
 useEffect(()=>{
   if(!supported||(!entry.api_key&&!retry)){setStatus({});return;}
   const controller=new AbortController();let active=true;
   setStatus({text:'Ожидание ввода…'});
   const timer=setTimeout(async()=>{
     setStatus({loading:true,text:'Загрузка доступных моделей…'});
     try{
       const response=await fetch('/api/providers/models',{method:'POST',headers:{'Content-Type':'application/json'},signal:controller.signal,body:JSON.stringify({name,base_url:base,api_key:entry.api_key||''})});
       const data=await response.json();
       if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Не удалось загрузить модели');
       if(!active)return;
       setStatus({loading:true,text:'Сохранение списка моделей…'});
       const savedResponse=await fetch('/api/providers/models/save',{method:'POST',headers:{'Content-Type':'application/json'},signal:controller.signal,body:JSON.stringify({ticket:data.save_ticket})});
       const saved=await savedResponse.json();
       if(!savedResponse.ok)throw Error(typeof saved.detail==='string'?saved.detail:'Не удалось сохранить список моделей');
       if(active){callback.current(saved.models);setStatus({text:`Ключ и список сохранены. Моделей: ${saved.models.length}. Список доступен в агентах.`});}
     }catch(e){if(active&&e.name!=='AbortError')setStatus({error:true,text:e.message});}
   },800);
   return()=>{active=false;clearTimeout(timer);controller.abort();};
 },[name,base,entry.api_key,entry.type,supported,retry]);
 if(!supported)return null;
 return <div><button type="button" className="button secondary" disabled={status.loading||(!entry.api_key&&!entry.api_key_present)} onClick={()=>setRetry(v=>v+1)}>Обновить список моделей</button><p className="muted" role={status.error?'alert':'status'}>{status.text||'Список загрузится автоматически после ввода ключа. Можно обновить его и с сохранённым ключом.'}</p><small>API может вернуть также модели без поддержки чата и навыков. Выбранную модель проверьте тестовым запросом.</small></div>;
}
