import json
from pathlib import Path
import yaml

from .settings import public, update
from .gateway.models import Agent


def add_parsers(commands):
    commands.add_parser('status')
    config = commands.add_parser('config').add_subparsers(dest='action',required=True)
    config.add_parser('show')
    config.add_parser('import').add_argument('file',type=Path)
    models = commands.add_parser('models').add_subparsers(dest='action',required=True)
    models.add_parser('list')
    models.add_parser('test').add_argument('model')
    models.add_parser('refresh').add_argument('provider')
    models.add_parser('set-key').add_argument('provider')
    audit = commands.add_parser('audit')
    audit.add_argument('--agent');audit.add_argument('--skill');audit.add_argument('--status');audit.add_argument('--date')
    audit.add_argument('--limit',type=int,default=100)
    sessions=commands.add_parser('sessions').add_subparsers(dest='action',required=True)
    sessions.add_parser('list').add_argument('agent')
    history=sessions.add_parser('history');history.add_argument('agent');history.add_argument('session')


def dispatch(args, app, console):
    if args.command == 'status':
        console.print_json(data={'root':str(app.root),'agents':len(app.registry.agents),'skills':len(app.registry.skills),'errors':app.registry.errors})
    elif args.command == 'config':
        if args.action=='show': console.print_json(data=public(app.config))
        else:
            try: draft=yaml.safe_load(args.file.read_text()); update(app,draft)
            except Exception: raise ValueError('Не удалось импортировать конфигурацию; проверьте файл и схему (секреты скрыты)') from None
            console.print('Конфигурация импортирована.')
    elif args.command == 'models':
        if args.action=='list':
            for name,p in app.config.providers.items():
                for model in p.models: console.print(f'{name}:{model}',markup=False)
        elif args.action=='test':
            from .core.providers.factory import make_provider
            try: result=make_provider(args.model,app.config).chat([{'role':'user','content':'Reply OK'}],[])
            except Exception: raise ValueError('Не удалось проверить модель; проверьте подключение и ключ') from None
            console.print_json(data=app.audit.redact(result))
        else:
            provider=app.config.providers.get(args.provider)
            if not provider: raise ValueError('Провайдер не найден')
            if args.action=='refresh':
                from .core.providers.discovery import list_models
                models=list_models(provider.base_url,provider.key())
                candidate=app.config.model_copy(deep=True);candidate.providers[args.provider].models=models
                update(app,candidate.model_dump());console.print(f'Сохранено моделей: {len(models)}')
            else:
                import sys,getpass,warnings
                if not sys.stdin.isatty(): raise ValueError('Ключ вводится только в интерактивном терминале')
                with warnings.catch_warnings():
                    warnings.simplefilter('error',getpass.GetPassWarning)
                    key=getpass.getpass('API-ключ: ')
                if not key.strip(): raise ValueError('Пустой ключ')
                from pydantic import SecretStr
                candidate=app.config.model_copy(deep=True)
                candidate.providers[args.provider].api_key=SecretStr(key)
                candidate.providers[args.provider].api_key_env=None
                update(app,candidate.model_dump());console.print('Ключ сохранён.')
    elif args.command=='audit':
        if not 1<=args.limit<=500: raise ValueError('limit: 1–500')
        console.print_json(data=app.audit.query(args.agent,args.skill,args.status,args.date,args.limit))
    elif args.command=='sessions':
        memory=app.runner.memory(app.registry.build(args.agent))
        console.print_json(data=memory.sessions(args.agent) if args.action=='list' else memory.load(args.session))
    elif args.command=='agents':
        if args.action=='list':
            for agent in app.registry.agents.values(): console.print(f'{agent.name}\t{agent.model}',markup=False)
        elif args.action=='show': console.print_json(data=app.registry.build(args.name).model_dump())
        else:
            try: agent=Agent.model_validate(yaml.safe_load(args.file.read_text()))
            except Exception: raise ValueError('Некорректный YAML агента') from None
            if agent.name in app.registry.agents: raise ValueError('Агент уже существует')
            app.registry.save_agent(agent);console.print(f'Агент {agent.name} создан.',markup=False)
    elif args.command=='channels':
        console.print_json(data=public(app.config)['channels'])
