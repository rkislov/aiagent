"""Local operator CLI. OS account/file ownership is the administration boundary."""
import getpass
import os
import stat
import sys
import warnings
from pathlib import Path

from .auth import Auth


def local_auth(root, *, initialize=False):
    root = root.resolve()
    if (root / '.tenant').exists():
        raise ValueError('Укажите корень сервера, а не пространство пользователя')
    db = root / 'data/auth.db'
    if not db.exists() and not initialize:
        raise ValueError('База пользователей не найдена; проверьте --root')
    for path in (root, root / 'data', db):
        if path.exists():
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022 or (path == db and info.st_mode & 0o077):
                raise ValueError('Администрирование требует владения каталогом/БД без записи для группы и других пользователей')
    return Auth(root)


def password():
    if not sys.stdin.isatty():
        raise ValueError('Пароль вводится только в интерактивном терминале без эха')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        first = getpass.getpass('Новый пароль (12–256 символов): ')
        second = getpass.getpass('Повторите пароль: ')
    if first != second: raise ValueError('Пароли не совпадают')
    if not 12 <= len(first) <= 256: raise ValueError('Пароль должен содержать 12–256 символов')
    return first


def confirm(username):
    if not sys.stdin.isatty() or input(f'Для подтверждения введите логин {username}: ').strip() != username:
        raise ValueError('Операция отменена')


def add_parsers(commands):
    auth = commands.add_parser('auth', help='Локальное восстановление доступа').add_subparsers(dest='action', required=True)
    auth.add_parser('init').add_argument('username')
    reset = auth.add_parser('reset-admin', help='Сброс пароля администратора без изменения контента')
    reset.add_argument('username')
    reset.add_argument('--reset-2fa', action='store_true', help='Также аннулировать аутентификатор и резервные коды')
    auth.add_parser('reset-2fa').add_argument('username')
    users = commands.add_parser('users', help='Пользователи сервера').add_subparsers(dest='action', required=True)
    users.add_parser('list')
    users.add_parser('create').add_argument('username')


def dispatch(args, console):
    auth = local_auth(args.root, initialize=args.command == 'auth' and args.action == 'init')
    if args.action == 'list':
        with auth.connect() as db:
            rows = db.execute('SELECT username,admin,enrolled FROM users ORDER BY username').fetchall()
        for name, admin, enrolled in rows:
            console.print(f'{name}\t{"admin" if admin else "user"}\t2FA: {"включена" if enrolled else "ожидает настройки"}', markup=False)
        return
    if args.action in ('init','create'):
        if args.action == 'create':
            with auth.connect() as db:
                if not db.execute('SELECT 1 FROM users').fetchone(): raise ValueError('Сначала выполните auth init')
        auth.create(args.username, password(), bootstrap=args.action == 'init')
        with auth.connect() as db:
            uid = db.execute('SELECT id FROM users WHERE username=?',(args.username,)).fetchone()[0]
            auth.security_event(db, f'cli:uid:{os.geteuid()}', uid, 'cli_user_created')
        console.print('Пользователь создан. При входе потребуется настройка 2FA.')
        return
    # Validate target before asking for secrets.
    with auth.connect() as db:
        row = db.execute('SELECT id,admin FROM users WHERE username=?',(args.username,)).fetchone()
    if not row: raise ValueError('Пользователь не найден')
    if args.action == 'reset-admin' and not row[1]: raise ValueError('Указанный пользователь не администратор')
    console.print('Все сессии и запросы входа будут аннулированы. Контент сохраняется.')
    confirm(args.username)
    new = password() if args.action == 'reset-admin' else None
    auth.local_reset(args.username, new, reset_2fa=args.action == 'reset-2fa' or getattr(args,'reset_2fa',False), admin_only=args.action == 'reset-admin')
    console.print('Доступ обновлён. Войдите заново. Пароль не выводится и не записывается в журнал.')
