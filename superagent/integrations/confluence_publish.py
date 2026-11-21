"""Single-use, version-checked Confluence release-note publication."""
import json
import threading
from markdown_it import MarkdownIt
from .work_tools import numeric


class ConfluencePublishPlan:
    def __init__(self,hub,p):
        self.hub=hub;self.cfg=hub.config.connections.confluence
        if not self.cfg.enabled or not self.cfg.key():raise ValueError('Настройте подключение Confluence')
        if not p['title'].strip() or len(p['title'])>255:raise ValueError('Нужен заголовок длиной до 255 символов')
        if not p['markdown'].strip() or len(p['markdown'])>100000:raise ValueError('Нужен текст release notes длиной до 100000 символов')
        self.page_id=numeric(p['page_id']) if p['page_id'] else ''
        self.parent_id=numeric(p['parent_id']) if p['parent_id'] else ''
        self.base=hub._confluence_base(self.cfg)
        self.create=not self.page_id
        if not self.create and any(p[k] for k in ('space_id','space_key','parent_id')):
            raise ValueError('При обновлении задайте только page_id, title и markdown; перенос страницы не поддерживается')
        cloud=self.cfg.edition=='cloud'
        if self.create:
            if cloud:
                numeric(p['space_id'])
                if p['space_key']:raise ValueError('Для Cloud используйте space_id, а не space_key')
            else:
                if not p['space_key'].strip() or len(p['space_key'])>255:raise ValueError('Для Server/Data Center нужен space_key')
                if p['space_id']:raise ValueError('Для Server/Data Center используйте space_key')
        self.original=self.read_page(self.page_id) if self.page_id else None
        self.parent=self.read_page(self.parent_id) if self.parent_id else None
        if self.parent and (self.parent['space_id'] if cloud else self.parent['space_key']) != (p['space_id'] if cloud else p['space_key']):
            raise ValueError('Родительская страница принадлежит другому пространству')
        renderer=MarkdownIt('commonmark',{'html':False,'xhtmlOut':True}).disable('image')
        rendered=renderer.render(p['markdown'])
        if len(rendered)>500000:raise ValueError('Слишком большой результат преобразования Markdown')
        body={'representation':'storage','value':rendered}
        if cloud:
            request={'title':p['title'],'status':'current','body':body}
            if self.create:
                request['spaceId']=p['space_id']
                if self.parent_id:request['parentId']=self.parent_id
            else:request.update(id=self.page_id,version={'number':self.original['version']+1})
        else:
            request={'type':'page','title':p['title'],'status':'current','body':{'storage':body}}
            if self.create:
                request['space']={'key':p['space_key']}
                if self.parent_id:request['ancestors']=[{'id':self.parent_id}]
            else:request.update(id=self.page_id,version={'number':self.original['version']+1})
        self.request_json=json.dumps(request,ensure_ascii=False)
        self.preview_json=json.dumps({'service':'confluence','server':self.base,'action':'create_page' if self.create else 'replace_page_body','page_id':self.page_id,'space':{'id':p['space_id'],'key':p['space_key']} if self.create else {'id':self.original['space_id'],'key':self.original['space_key']},'parent_id':self.parent_id,'title':p['title'],'markdown':p['markdown'],'current':self.original,'request_fields':request,'notice':'Обновление заменяет всё тело страницы, а не добавляет текст.' if not self.create else 'Будет опубликована новая страница.'},ensure_ascii=False)
        self.used=False;self.lock=threading.Lock()

    @property
    def preview(self):return json.loads(self.preview_json)

    def read_page(self,page_id):
        if self.cfg.edition=='cloud':
            item=self.hub._work_request(self.cfg,'/api/v2/pages/'+page_id,base=self.base,params={'body-format':'storage'})
        else:
            item=self.hub._work_request(self.cfg,'/rest/api/content/'+page_id,base=self.base,params={'expand':'body.storage,version,space'})
        version=item.get('version',{}).get('number')
        if item.get('status')!='current' or type(version) is not int or version<1:
            raise ValueError('Можно обновлять только опубликованную страницу с известной версией')
        if str(item.get('id'))!=page_id:raise ValueError('Сервис вернул другую страницу')
        return {'id':page_id,'title':item.get('title',''),'version':version,'status':item['status'],'space_id':str(item.get('spaceId','')),'space_key':item.get('space',{}).get('key',''),'body':item.get('body',{}).get('storage',{}).get('value','')}

    def execute(self,*,confirmed=False):
        if confirmed is not True:raise PermissionError('Публикация требует явного подтверждения')
        with self.lock:
            if self.used:raise PermissionError('Подтверждение уже использовано')
            self.used=True
        if self.original and self.read_page(self.page_id)!=self.original:
            raise ValueError('Страница изменилась. Нужны новый предпросмотр и подтверждение.')
        if self.parent:
            current=self.read_page(self.parent_id)
            if (current['space_id'],current['space_key'])!=(self.parent['space_id'],self.parent['space_key']):
                raise ValueError('Родительская страница перемещена. Нужен новый предпросмотр.')
        path=('/api/v2/pages' if self.cfg.edition=='cloud' else '/rest/api/content')+('/'+self.page_id if self.page_id else '')
        result=self.hub._work_request(self.cfg,path,base=self.base,method='POST' if self.create else 'PUT',body=json.loads(self.request_json),writing=True)
        identity=str(result.get('id',''))
        if not identity.isdigit() or (self.page_id and identity!=self.page_id):
            raise ValueError('Confluence не вернул ожидаемый ID страницы; проверьте результат перед повтором')
        return {'saved':True,'id':identity,'url':self.base+'/pages/viewpage.action?pageId='+identity}
