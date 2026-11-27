import React,{useRef} from 'react';
import {Plus} from 'lucide-react';

const templates=[
 ['lawyer_rf','Юрист РФ','Российское право и работа с договорами'],
 ['work_manager','Диспетчер задач','Задачи, сроки и подготовка релиза'],
 ['accountant_1c','Бухгалтер 1С','Чтение и проверка данных через OData'],
 ['security_auditor','Аудитор безопасности','Аудит сайтов по заголовкам, настройкам cookie и отчётам ZAP'],
];

export default function AgentCreator({api,model,onCreate,reportError}){
 const menu=useRef(null);
 async function choose(id){menu.current.open=false;try{onCreate(id?{...await api('/agent-templates/'+id),model}:null)}catch(e){reportError(e)}}
 return <details className="create-agent-menu" ref={menu}><summary className="button primary"><Plus size={16}/>Новый агент</summary><div className="create-agent-options"><button type="button" onClick={()=>choose(null)}><strong>С нуля</strong><small>Свои инструкции и навыки</small></button>{templates.map(([id,title,description])=><button key={id} type="button" onClick={()=>choose(id)}><strong>{title}</strong><small>{description}</small></button>)}</div></details>
}
