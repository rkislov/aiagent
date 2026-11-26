import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import {Copy} from 'lucide-react';

function plain(children){return React.Children.toArray(children).map(child=>typeof child==='string'||typeof child==='number'?String(child):React.isValidElement(child)?plain(child.props.children):'').join('')}
function CodeBlock({children}){
 const [copied,setCopied]=React.useState(false);
 return <div className="markdown-code"><button type="button" title="Копировать код" onClick={async()=>{try{await navigator.clipboard.writeText(plain(children));setCopied(true);setTimeout(()=>setCopied(false),1800)}catch{setCopied(false)}}}><Copy size={14}/>{copied?'Скопировано':'Копировать'}</button><pre>{children}</pre></div>
}
export default function Markdown({text}){
 return <div className="markdown-body"><ReactMarkdown skipHtml remarkPlugins={[remarkGfm]} rehypePlugins={[[rehypeHighlight,{detect:false}]]} components={{
  pre:CodeBlock,
  a:({href,children})=><a href={href} target={/^https?:/.test(href||'')?'_blank':undefined} rel="noopener noreferrer">{children}</a>,
  img:({src,alt})=>/^\/api\/images\/[a-f0-9]{32}$/.test(src||'')?<img src={src} alt={alt||'Изображение'}/>:<span className="muted">[Изображение: {alt||'внешний источник'}]</span>,
 }}>{text||''}</ReactMarkdown></div>
}
