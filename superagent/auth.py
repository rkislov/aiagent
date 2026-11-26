import contextvars
import hashlib
import secrets
import shutil
import sqlite3
import threading
import time
from pathlib import Path
import re
import pyotp
import segno
import io
import base64
from argon2 import PasswordHasher

current_service = contextvars.ContextVar('current_service')


class ServiceProxy:
    def __setattr__(self, key, value):
        setattr(current_service.get(), key, value)

    def __getattr__(self, key):
        return getattr(current_service.get(), key)


class Auth:
    def __init__(self, root):
        self.root = root
        (root / 'data').mkdir(exist_ok=True)
        self.db = root / 'data/auth.db'
        self.lock = threading.RLock()
        self.services = {}
        self._pool_signature=None
        self._pool_cache={}
        self.hasher = PasswordHasher()
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT UNIQUE, hash TEXT, totp TEXT, enrolled INTEGER, last_step INTEGER, admin INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user TEXT, expires REAL, challenge INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS attempts(key TEXT, time REAL)')
            db.execute('CREATE TABLE IF NOT EXISTS recovery(user TEXT, hash TEXT, used INTEGER DEFAULT 0, PRIMARY KEY(user, hash))')
            if 'disabled' not in {row[1] for row in db.execute('PRAGMA table_info(users)')}:
                db.execute('ALTER TABLE users ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0')
        self.db.chmod(0o600)

    def connect(self): return sqlite3.connect(self.db, timeout=15)

    def create(self, username, password, bootstrap=False):
        if not re.fullmatch('[a-z][a-z0-9_]{2,40}', username) or len(password)<12 or len(password)>256:
            raise ValueError('Логин: 3–41 латинских символа; пароль: 12–256 символов')
        with self.lock, self.connect() as db:
            count = db.execute('SELECT count(*) FROM users').fetchone()[0]
            if bootstrap and count: raise ValueError('Первоначальная настройка уже завершена')
            uid = secrets.token_hex(16)
            try:
                db.execute('INSERT INTO users(id,username,hash,totp,enrolled,last_step,admin) VALUES(?,?,?,?,0,-1,?)',(uid,username,self.hasher.hash(password),pyotp.random_base32(),int(count==0)))
            except sqlite3.IntegrityError:
                raise ValueError('Такой логин уже существует') from None
        return {'username':username}

    def login(self, username, password):
        now=time.time()
        with self.lock, self.connect() as db:
            db.execute('DELETE FROM attempts WHERE time < ?', (now-900,))
            if db.execute('SELECT count(*) FROM attempts WHERE key=?',(username,)).fetchone()[0]>=5:
                raise ValueError('Слишком много попыток. Повторите через 15 минут')
            db.execute('INSERT INTO attempts VALUES(?,?)',(username,now))
        with self.connect() as db:
            row=db.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
        try:
            self.hasher.verify(row[2] if row else self.hasher.hash('dummy-password'),password)
            if not row or row[7]: raise ValueError()
        except Exception:
            raise ValueError('Неверный логин или пароль') from None
        token=secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM users WHERE id=? AND hash=? AND totp=? AND disabled=0',(row[0],row[2],row[3])).fetchone():
                raise ValueError('Учётные данные изменились. Повторите вход.')
            db.execute('INSERT INTO sessions VALUES(?,?,?,1)',(self.digest(token),row[0],now+300))
        result={'challenge':token,'enroll':not bool(row[4])}
        if not row[4]:
            result.update(secret=row[3], uri=pyotp.TOTP(row[3]).provisioning_uri(name=username,issuer_name='Superagent'))
            image = io.BytesIO()
            segno.make(result['uri'], micro=False).save(image, kind='png', scale=5, border=4)
            result['qr'] = 'data:image/png;base64,' + base64.b64encode(image.getvalue()).decode()
        return result

    def new_recovery(self, db, uid):
        codes = []
        for _ in range(10):
            raw = secrets.token_hex(10)
            codes.append('-'.join(raw[i:i+5] for i in range(0,20,5)))
        db.execute('DELETE FROM recovery WHERE user=?', (uid,))
        db.executemany('INSERT INTO recovery(user,hash) VALUES(?,?)', [(uid,self.recovery_hash(uid,c)) for c in codes])
        return codes

    def recovery_hash(self, uid, code):
        return self.digest(uid + ':' + re.sub(r'[-\s]', '', str(code)).lower())

    @staticmethod
    def digest(token): return hashlib.sha256(token.encode()).hexdigest()

    def verify(self, token, code):
        codes = None
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT u.* FROM users u JOIN sessions s ON s.user=u.id WHERE s.token=? AND s.challenge=1 AND s.expires>? AND u.disabled=0',(self.digest(token),time.time())).fetchone()
            if not row: raise ValueError('Запрос входа истёк')
            key='totp:'+row[0]
            if db.execute('SELECT count(*) FROM attempts WHERE key=? AND time>?',(key,time.time()-900)).fetchone()[0]>=5:
                raise ValueError('Слишком много попыток 2FA')
            step=int(time.time()//30)
            matched=next((n for n in (step-1,step,step+1) if n>row[5] and secrets.compare_digest(pyotp.TOTP(row[3]).at(n*30),str(code))),None)
            recovered = False
            if matched is None and row[4]:
                recovered = db.execute('UPDATE recovery SET used=1 WHERE user=? AND hash=? AND used=0', (row[0], self.recovery_hash(row[0],code))).rowcount == 1
            if matched is None and not recovered:
                db.execute('INSERT INTO attempts VALUES(?,?)',(key,time.time()))
                failed=True
            else:
                failed=False
                db.execute("DELETE FROM attempts WHERE key IN (?,?)", (row[1], key))
                db.execute('UPDATE users SET enrolled=1,last_step=? WHERE id=?',(matched if matched is not None else row[5],row[0]))
                if matched is not None and not db.execute('SELECT 1 FROM recovery WHERE user=?',(row[0],)).fetchone():
                    codes = self.new_recovery(db, row[0])
                db.execute('DELETE FROM sessions WHERE token=?',(self.digest(token),))
                session=secrets.token_urlsafe(32)
                db.execute('INSERT INTO sessions VALUES(?,?,?,0)',(self.digest(session),row[0],time.time()+43200))
        if failed: raise ValueError('Неверный или уже использованный код 2FA')
        return session, codes

    def rotate_recovery(self, uid, password, code):
        # Re-authenticate with both factors; use the same rate limits and replay checks.
        with self.connect() as db:
            row = db.execute('SELECT username FROM users WHERE id=?',(uid,)).fetchone()
        challenge = self.login(row[0], password)
        session, _ = self.verify(challenge['challenge'], code)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM sessions WHERE token=?',(self.digest(session),))
            return self.new_recovery(db, uid)

    def user(self, token):
        if not token: return None
        with self.connect() as db:
            row=db.execute('SELECT u.id,u.username,u.admin FROM users u JOIN sessions s ON s.user=u.id WHERE s.token=? AND s.challenge=0 AND s.expires>? AND u.disabled=0',(self.digest(token),time.time())).fetchone()
        return dict(zip(('id','username','admin'),row)) if row else None

    def service(self, user):
        from .service import Platform
        with self.lock:
            if user['id'] not in self.services:
                with self.connect() as db:
                    account = db.execute('SELECT username FROM users WHERE id=?', (user['id'],)).fetchone()
                root=self.root/'data/users'/user['id']
                root.mkdir(parents=True,exist_ok=True,mode=0o700)
                (root/'.tenant').touch()
                for folder in ('agents','skills'):
                    (root/folder).mkdir(exist_ok=True)
                from .shared_agents import SharedAgents
                self.services[user['id']] = Platform(root, shared_store=SharedAgents(self.root), username=account[0] if account else None)
            from .gateway.config import load_config
            with self.connect() as db:admins=db.execute('SELECT id FROM users WHERE admin=1 AND disabled=0 ORDER BY username').fetchall()
            paths=[self.root/'data/users'/uid/'config.yaml' for (uid,) in admins]
            stamp=tuple((str(path),path.stat().st_mtime_ns,path.stat().st_ino) for path in paths if path.is_file())
            if stamp!=self._pool_signature:
                pool={}
                for filename,_,_ in stamp:
                    for name,cfg in load_config(Path(filename).parent).providers.items():
                        if cfg.key() or cfg.type=='ollama':pool.setdefault(name,cfg.model_copy(deep=True))
                self._pool_cache=pool
                self._pool_signature=stamp
            service=self.services[user['id']]
            service.config._model_pool=self._pool_cache.copy()
            for cfg in self._pool_cache.values():
                if cfg.key() and cfg.key() not in service.audit.secrets:service.audit.secrets.append(cfg.key())
            return service

    def reauthenticate(self, uid, password, code):
        with self.connect() as db:
            row = db.execute('SELECT username FROM users WHERE id=?',(uid,)).fetchone()
        challenge = self.login(row[0], password)
        session, _ = self.verify(challenge['challenge'], code)
        return self.digest(session)

    def change_password(self, uid, password, code, new_password):
        if not isinstance(new_password,str) or not 12 <= len(new_password) <= 256:
            raise ValueError('Новый пароль должен содержать 12–256 символов')
        if new_password == password: raise ValueError('Новый пароль совпадает с текущим')
        with self.lock:
            proof = self.reauthenticate(uid,password,code)
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if not db.execute('SELECT 1 FROM sessions WHERE token=? AND user=?',(proof,uid)).fetchone():
                    raise ValueError('Повторите проверку личности')
                db.execute('UPDATE users SET hash=? WHERE id=?',(self.hasher.hash(new_password),uid))
                db.execute('DELETE FROM sessions WHERE user=?',(uid,))
                self.security_event(db,uid,uid,'password_changed')

    def security_event(self, db, actor, target, action):
        db.execute('CREATE TABLE IF NOT EXISTS security_events(time REAL, actor TEXT, target TEXT, action TEXT)')
        db.execute('INSERT INTO security_events VALUES(?,?,?,?)',(time.time(),actor,target,action))

    def list_users(self,actor):
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM users WHERE id=? AND admin=1 AND disabled=0',(actor,)).fetchone():raise PermissionError('Только администратор')
            return [dict(zip(('id','username','admin','enrolled','disabled'),row)) for row in db.execute('SELECT id,username,admin,enrolled,disabled FROM users ORDER BY username')]

    def manage_user(self,actor,target,action,password,code,*,new_password=None):
        if action not in ('block','unblock','promote','demote','reset_password','revoke_sessions'):raise ValueError('Неизвестное действие')
        if action=='reset_password' and (not isinstance(new_password,str) or not 12<=len(new_password)<=256):raise ValueError('Пароль должен содержать 12–256 символов')
        with self.lock:
            with self.connect() as db:
                if not db.execute('SELECT 1 FROM users WHERE id=? AND admin=1 AND disabled=0',(actor,)).fetchone():raise PermissionError('Только администратор')
            proof=self.reauthenticate(actor,password,code)
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if not db.execute('SELECT 1 FROM users u JOIN sessions s ON s.user=u.id WHERE u.id=? AND u.admin=1 AND u.disabled=0 AND s.token=? AND s.expires>?',(actor,proof,time.time())).fetchone():raise PermissionError('Права администратора изменились')
                row=db.execute('SELECT id,admin,disabled FROM users WHERE username=?',(target,)).fetchone()
                if not row:raise ValueError('Пользователь не найден')
                uid,admin,disabled=row
                if uid==actor and action in ('block','demote','reset_password'):raise ValueError('Для своей учётной записи используйте смену пароля; блокировка и снятие собственной роли запрещены')
                if admin and not disabled and action in ('block','demote') and db.execute('SELECT count(*) FROM users WHERE admin=1 AND disabled=0').fetchone()[0]<=1:raise ValueError('Нельзя отключить последнего администратора')
                db.execute('DELETE FROM sessions WHERE token=?',(proof,))
                if action in ('block','unblock'):db.execute('UPDATE users SET disabled=? WHERE id=?',(int(action=='block'),uid))
                elif action in ('promote','demote'):db.execute('UPDATE users SET admin=? WHERE id=?',(int(action=='promote'),uid))
                elif action=='reset_password':db.execute('UPDATE users SET hash=? WHERE id=?',(self.hasher.hash(new_password),uid))
                db.execute('DELETE FROM sessions WHERE user=?',(uid,))
                self.security_event(db,actor,uid,'admin_'+action)
                return uid

    def reset_two_factor(self, actor, target, password, code):
        with self.lock:
            with self.connect() as db:
                if not db.execute('SELECT 1 FROM users WHERE id=? AND admin=1 AND disabled=0',(actor,)).fetchone():
                    raise PermissionError('Только администратор')
                victim = db.execute('SELECT id FROM users WHERE username=?',(target,)).fetchone()
                if not victim: raise ValueError('Пользователь не найден')
                if victim[0] == actor: raise ValueError('Сброс собственной 2FA через администратора запрещён')
            proof = self.reauthenticate(actor,password,code)
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if not db.execute('SELECT 1 FROM sessions s JOIN users u ON u.id=s.user WHERE s.token=? AND s.user=? AND u.admin=1 AND u.disabled=0 AND s.expires>?',(proof,actor,time.time())).fetchone():
                    raise ValueError('Повторите проверку личности')
                db.execute('DELETE FROM sessions WHERE token=?',(proof,))
                db.execute('UPDATE users SET totp=?,enrolled=0,last_step=-1 WHERE id=?',(pyotp.random_base32(),victim[0]))
                db.execute('DELETE FROM sessions WHERE user=?',(victim[0],))
                db.execute('DELETE FROM recovery WHERE user=?',(victim[0],))
                db.execute('DELETE FROM attempts WHERE key=?',('totp:'+victim[0],))
                self.security_event(db,actor,victim[0],'two_factor_reset')
                return victim[0]

    def local_reset(self, username, password=None, *, reset_2fa=False, admin_only=False):
        """Only the local ownership-checked CLI calls this recovery operation."""
        if password is not None and (not isinstance(password,str) or not 12 <= len(password) <= 256):
            raise ValueError('Пароль должен содержать 12–256 символов')
        if password is None and not reset_2fa: raise ValueError('Не задана операция восстановления')
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id,admin FROM users WHERE username=?',(username,)).fetchone()
            if not row or (admin_only and not row[1]): raise ValueError('Администратор не найден')
            uid = row[0]
            if password is not None:
                db.execute('UPDATE users SET hash=? WHERE id=?',(self.hasher.hash(password),uid))
            if reset_2fa:
                db.execute('UPDATE users SET totp=?,enrolled=0,last_step=-1 WHERE id=?',(pyotp.random_base32(),uid))
                db.execute('DELETE FROM recovery WHERE user=?',(uid,))
            db.execute('DELETE FROM sessions WHERE user=?',(uid,))
            db.execute('DELETE FROM attempts WHERE key IN (?,?)',(username,'totp:'+uid))
            import os
            actor = f'cli:uid:{os.geteuid()}'
            if password is not None: self.security_event(db,actor,uid,'cli_admin_password_reset')
            if reset_2fa: self.security_event(db,actor,uid,'cli_two_factor_reset')


class RevocableSessionEvent(threading.Event):
    """Check persisted revocation even when the operator resets via another process."""
    def __init__(self, auth, token):
        super().__init__()
        self.auth, self.token = auth, token

    def is_set(self):
        if not super().is_set():
            try:
                if self.auth.user(self.token) is None: self.set()
            except Exception:
                self.set()
        return super().is_set()
