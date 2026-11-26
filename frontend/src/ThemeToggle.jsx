import React,{useEffect,useState} from 'react';
import {Sun,Moon,Monitor} from 'lucide-react';
export default function ThemeToggle(){
 const [mode,setMode]=useState(()=>{try{return ['light','dark','auto'].includes(localStorage.getItem('aiagent-theme'))?localStorage.getItem('aiagent-theme'):'auto'}catch{return 'auto'}});
 useEffect(()=>{const media=window.matchMedia('(prefers-color-scheme: dark)');const apply=()=>{document.documentElement.dataset.theme=mode==='auto'?(media.matches?'dark':'light'):mode};apply();try{localStorage.setItem('aiagent-theme',mode)}catch{}media.addEventListener('change',apply);return()=>media.removeEventListener('change',apply)},[mode]);
 return <div className="theme-toggle inline-flex items-center gap-1 rounded-xl p-1" role="group" aria-label="Тема оформления">{[['light','Светлая',Sun],['dark','Тёмная',Moon],['auto','Авто',Monitor]].map(([value,label,Icon])=><button type="button" key={value} title={label} aria-label={label+' тема'} aria-pressed={mode===value} onClick={()=>setMode(value)} className="inline-flex items-center justify-center gap-1 rounded-lg px-2 py-2 transition-colors"><Icon size={15}/><span>{label}</span></button>)}</div>
}
