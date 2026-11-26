from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from rich.console import Console
from rich.table import Table

from .service import Platform


def main():
    parser = argparse.ArgumentParser(description="Superagent · локальная платформа агентов")
    parser.add_argument("--root", type=Path, default=Path(os.environ.get("SUPERAGENT_HOME", ".")), help="Workspace with config.yaml, agents/ and skills/")
    parser.add_argument("--user", help="Пространство пользователя (локальный оператор сервера)")
    commands = parser.add_subparsers(dest="command", required=True)
    skills = commands.add_parser("skills").add_subparsers(dest="action", required=True)
    skills.add_parser("list")
    skills.add_parser("validate").add_argument("name")
    skills.add_parser("show").add_argument("name")
    test = skills.add_parser("test")
    test.add_argument("name")
    test.add_argument("--input", required=True)
    agents = commands.add_parser("agents").add_subparsers(dest="action", required=True)
    agents.add_parser("list")
    agents.add_parser("show").add_argument("name")
    agents.add_parser("create").add_argument("file", type=Path)
    for action in ("build", "run", "chat"):
        cmd = agents.add_parser(action)
        cmd.add_argument("name")
        if action != "build":
            cmd.add_argument("--session", help="Resume a conversation")
            cmd.add_argument("--message", help="Single non-interactive turn")
    channels = commands.add_parser("channels")
    channels.add_argument("action", choices=["run", "list"])
    channels.add_argument("name", nargs="?")
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8080)
    from . import cli_admin, cli_workspace
    cli_admin.add_parsers(commands)
    cli_workspace.add_parsers(commands)
    args = parser.parse_args()
    console = Console()
    app = None
    try:
        if args.command in ('auth','users'):
            if args.user: raise ValueError('--user не применяется к администрированию сервера')
            cli_admin.dispatch(args,console)
            return
        workspace_root = args.root
        if args.user:
            if args.command == 'serve': raise ValueError('serve запускается от корня сервера, без --user')
            auth = cli_admin.local_auth(args.root)
            with auth.connect() as db:
                row = db.execute('SELECT id FROM users WHERE username=?',(args.user,)).fetchone()
            if not row: raise ValueError('Пользователь не найден')
            # Initialize only this user's workspace, never copy another user's content.
            scoped = auth.service({'id':row[0]})
            scoped.close()
            workspace_root = scoped.root
        app = Platform(workspace_root, username=args.user)
        if args.command in ('status','config','models','audit','sessions') or (args.command=='agents' and args.action in ('list','show','create')) or (args.command=='channels' and args.action=='list'):
            cli_workspace.dispatch(args,app,console)
        elif args.command == "channels":
            if not args.name: raise ValueError('Укажите имя канала')
            from .channels import run
            run(app, args.name)
        elif args.command == "serve":
            import uvicorn
            from .web.app import create_app
            console.print(f"Superagent → http://localhost:{args.port}", markup=False)
            uvicorn.run(create_app(app), host="127.0.0.1", port=args.port, proxy_headers=True, forwarded_allow_ips="127.0.0.1")
        elif args.command == "skills":
            if args.action == "list":
                table = Table("Навык", "Версия", "Описание", "Статус")
                for skill in app.registry.skills.values():
                    table.add_row(skill.name, skill.version, skill.description, "valid")
                console.print(table)
                for key, error in app.registry.errors.items():
                    console.print(f"{key}: {error}", markup=False)
            elif args.action == "show":
                skill,metadata,code = app.registry.read_skill(args.name)
                console.print_json(data=skill.model_dump())
                console.print(code,markup=False)
            elif args.action == "validate":
                skill, _, _ = app.registry.read_skill(args.name)
                from .sandbox.policy import resolve_permissions
                resolve_permissions(app.root, skill.permissions)
                console.print(f"✓ {skill.name}: valid", markup=False)
            else:
                skill, _, code = app.registry.read_skill(args.name)
                payload = json.loads(args.input)
                from .integrations.catalog import BUILTINS
                from .integrations.task_writes import WRITE_TOOLS
                write_plan = app.runner.builtins.prepare_write(skill.name,payload) if skill.name in WRITE_TOOLS else None
                if write_plan is not None: console.print_json(data=write_plan.preview)
                protected = skill.name in BUILTINS or skill.irreversible or skill.permissions.subprocess or skill.permissions.filesystem or skill.permissions.network
                approved = not protected or input(f"Execute {skill.name} with {skill.permissions.model_dump()}? [y/N] ").strip().lower() == "y"
                started = time.monotonic()
                if not approved:
                    app.audit.record(agent="__test__", kind="skill", skill=skill.name, input=payload, output={"error": "Confirmation denied"}, status="denied")
                    raise ValueError("Confirmation denied")
                try:
                    result = write_plan.execute(confirmed=True) if write_plan is not None else app.runner.builtins.run(skill.name, payload) if skill.name in BUILTINS else app.runner.executor.run(skill, payload, code)
                except Exception as exc:
                    app.audit.record(agent="__test__", kind="skill", skill=skill.name, input=payload, output={"error": str(exc)}, status="error", duration_ms=(time.monotonic()-started)*1000)
                    raise
                app.audit.record(agent="__test__", kind="skill", skill=skill.name, input=payload, output=result, duration_ms=(time.monotonic()-started)*1000)
                console.print_json(data=result)
        elif args.action == "build":
            agent = app.registry.build(args.name)
            console.print(f"✓ {agent.name}: {agent.model}, {len(agent.skills)} skills", markup=False)
        else:
            app.registry.build(args.name)
            session = args.session or uuid4().hex
            console.print(f"Session: {session}. /exit — выход", markup=False)

            def confirm(approval):
                console.print_json(data=approval)
                try:
                    return input("Подтвердить действие? [y/N] ").strip().lower() == "y"
                except EOFError:
                    return False

            while True:
                try:
                    text = args.message or input("Вы › ")
                except EOFError:
                    break
                if text.strip() in ("/exit", "/quit"):
                    break
                if not text.strip():
                    continue
                failed = False
                for event in app.runner.run(args.name, text, session, confirm):
                    if event["type"] == "delta":
                        console.print(event["text"], end="", markup=False, highlight=False)
                    elif event["type"] == "skill_result":
                        console.print(f"  ↳ {event['skill']}: {event['status']}", markup=False)
                    elif event["type"] == "error":
                        console.print(event["error"], markup=False)
                        failed = True
                console.print()
                if args.message:
                    if failed:
                        raise SystemExit(1)
                    break
    except KeyboardInterrupt:
        console.print("\nОстановлено.")
        raise SystemExit(130)
    except Exception as exc:
        from pydantic import ValidationError
        message = '; '.join('.'.join(map(str,e['loc']))+': '+e['msg'] for e in exc.errors(include_input=False,include_context=False)) if isinstance(exc,ValidationError) else str(exc)
        console.print('Ошибка: '+(app.audit.redact(message) if app else message),markup=False)
        raise SystemExit(1)
    finally:
        if app:
            app.close()
