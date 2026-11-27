import React from 'react';
import Progress, {Elapsed} from './Progress';
import {Globe,Search,BookOpen,Mail,Calendar,FileText,ImagePlus,Sparkles,ShieldCheck,Settings2} from 'lucide-react';

const labels={web_search:'Поиск в интернете',web_read:'Чтение страницы',rss_read:'Чтение RSS',imap_read:'Чтение почты',caldav_read:'Чтение календаря',office_create:'Создание документа',office_read:'Чтение документа',office_edit:'Изменение документа',office_compare:'Сравнение документов',office_list:'Личные документы',image_generate:'Генерация изображения',knowledge_search:'Поиск в базе знаний',knowledge_index:'Добавление знаний',knowledge_list:'Подключённые базы знаний',knowledge_forget:'Обновление базы знаний',dialog_search:'Поиск по диалогам',schedule_save:'Настройка расписания'};
export const skillLabel=skill=>labels[skill]||skill?.replaceAll('_',' ')||'Работа с моделью';
export function SkillIcon({skill,active=false}){
 const web=skill==='web_search'||skill==='web_read';
 const Icon=web?Globe:skill?.startsWith('knowledge_')?BookOpen:skill?.startsWith('office_')?FileText:skill==='image_generate'?ImagePlus:skill==='imap_read'?Mail:skill==='caldav_read'?Calendar:Settings2;
 return <span className={'skill-symbol '+(active?'is-active ':'')+(web?'is-web ':'')+(skill==='web_read'?'is-reading':'')} aria-hidden="true"><Icon size={21} className="skill-main-icon"/>{skill==='web_read'&&<Search size={13} className="skill-lens"/>}{active&&<span className="skill-orbit-dot"/>}</span>
}
export default function SkillActivity({skill, statusText, started}) {
 const confirmation = statusText?.startsWith('Требуется');
 const subtitle = ({web_search:'Ищу подходящие источники',web_read:'Открываю страницу и извлекаю текст',image_generate:'Создаю изображение по вашему описанию',office_create:'Собираю файл для скачивания',knowledge_search:'Ищу в подключённых базах знаний'})[skill] || 'Выполняю действие и передам результат в диалог';
 const title = skill ? skillLabel(skill) : confirmation ? 'Нужно ваше подтверждение' : 'Модель готовит ответ';
 return <div className="run-activity">
  {skill ? <SkillIcon skill={skill} active/> : confirmation ? <span className="skill-symbol"><ShieldCheck size={22}/></span> : <span className="skill-symbol model-breath"><Sparkles size={22}/></span>}
  <div className="run-activity-copy">
   <div role="status"><strong>{title}</strong><span>{skill ? subtitle : statusText}</span></div>
   {!confirmation && <Progress label={title}/>}
  </div>
  {started && <Elapsed started={started}/>}
 </div>;
}
