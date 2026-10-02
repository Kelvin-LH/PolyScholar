# SPDX-License-Identifier: AGPL-3.0-only
"""Durable, identity-bound migration publication and conservative recovery.

迁移发布日志独立于 SQLite 事务；恢复只处理身份匹配的受管路径。
This is process-crash recovery, not a universal power-loss durability claim.
这是进程崩溃恢复机制，不承诺所有设备上的断电持久性。
"""
import hashlib
import itertools
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import uuid

MAX_JOURNAL = 8 * 1024 * 1024
MAX_FILE = 100 * 1024 * 1024
MAX_ENTRIES = 20000


def _identity(info):
    return dict(dev=info.st_dev, ino=info.st_ino)


def _redirect(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def _ordinary(path, directory=False):
    if any(_redirect(p) for p in (path, *path.parents)):
        raise ValueError('unsafe_path')
    info = path.lstat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError('unsafe_type')
    return info


def _sync_directory(path):
    if os.name == 'posix':
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('invalid_journal')
        result[key] = value
    return result


class MigrationRecovery:
    """No object sweep: only explicit journals can authorize cleanup.

    不扫描清空对象库；只有合法日志能授权逐项清理。
    """
    def __init__(self, root):
        self.root = Path(root)
        self.directory = self.root / 'migration-recovery'

    def _prepare(self):
        _ordinary(self.root, True)
        if self.directory.exists() or _redirect(self.directory):
            _ordinary(self.directory, True)
        else:
            self.directory.mkdir(mode=0o700)
            _sync_directory(self.root)
        return _identity(_ordinary(self.directory, True))

    def begin_stage(self, stage):
        self._prepare()
        if stage.parent != self.root or not re.fullmatch(r'zotero-[a-z0-9_]{8,}', stage.name):
            raise ValueError('unsafe_stage')
        identifier = str(uuid.uuid4())
        state = dict(version=1, id=identifier, receiptId=None,
                     root=_identity(_ordinary(self.root, True)),
                     journalDirectory=_identity(_ordinary(self.directory, True)),
                     stage=dict(path=stage.name, identity=_identity(_ordinary(stage, True))),
                     archive=None, files={}, publications=[])
        session = MigrationRecoverySession(self, state)
        session.save()
        return session

    def read(self, path):
        before = _ordinary(path)
        if before.st_nlink != 1 or before.st_size > MAX_JOURNAL:
            raise ValueError('invalid_journal')
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                     | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or _identity(opened) != _identity(before):
                raise ValueError('invalid_journal')
            data = bytearray()
            while True:
                part = os.read(fd, 65536)
                if not part:
                    break
                data.extend(part)
                if len(data) > MAX_JOURNAL:
                    raise ValueError('invalid_journal')
            if _identity(path.lstat()) != _identity(opened):
                raise ValueError('invalid_journal')
            state = json.loads(data.decode('utf-8'), object_pairs_hook=_reject_duplicates)
        finally:
            os.close(fd)
        session = MigrationRecoverySession(self, state)
        session.validate()
        if session.path != path:
            raise ValueError('invalid_journal')
        session.journal_identity = _identity(opened)
        return session

    def recover(self, db):
        report = dict(recovered=0, pending=0, codes=[])
        try:
            self._prepare()
        except (OSError, ValueError):
            return dict(recovered=0, pending=1, codes=['unsafe_recovery_directory'])
        # The lifecycle library lock is held by LocalStore before this scan.
        # LocalStore 已持有生命周期锁后才枚举这个专用日志目录。
        paths = list(itertools.islice(self.directory.iterdir(),1001))
        if len(paths) > 1000:
            return dict(recovered=0, pending=len(paths), codes=['journal_limit'])
        for path in paths:
            if not re.fullmatch(r'[a-f0-9-]{36}\.json', path.name):
                report['pending'] += 1
                report['codes'].append('unknown_journal_entry')
                continue
            try:
                session = self.read(path)
                if session.finish(db):
                    report['recovered'] += 1
                else:
                    report['pending'] += 1
                    report['codes'].append('identity_or_reference_pending')
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                report['pending'] += 1
                report['codes'].append('invalid_or_unsafe_journal')
        report['codes'] = sorted(set(report['codes']))
        return report


class MigrationRecoverySession:
    def __init__(self, manager, state):
        self.manager, self.state = manager, state
        self.journal_identity = None

    @property
    def path(self):
        return self.manager.directory / (self.state['id'] + '.json')

    def validate(self):
        state = self.state
        if (not isinstance(state, dict) or type(state.get('version')) is not int or state.get('version') != 1
                or str(uuid.UUID(state.get('id'))) != state.get('id')
                or state.get('root') != _identity(_ordinary(self.manager.root, True))
                or state.get('journalDirectory') != _identity(_ordinary(self.manager.directory, True))):
            raise ValueError('invalid_journal')
        if state.get('receiptId') is not None and str(uuid.UUID(state['receiptId'])) != state['receiptId']:
            raise ValueError('invalid_journal')
        def checked_identity(value):
            if not isinstance(value,dict) or set(value) != {'dev','ino'} or any(type(v) is not int or v < 0 for v in value.values()):
                raise ValueError('invalid_journal')
        stage = state['stage']
        checked_identity(stage['identity'])
        archive = state.get('archive')
        if (state['receiptId'] is None) != (archive is None):
            raise ValueError('invalid_journal')
        if archive is not None:
            if not isinstance(archive,dict) or archive.get('path') != 'zotero-archive/'+str(state['receiptId']):
                raise ValueError('invalid_journal')
            checked_identity(archive['identity'])
            checked_identity(archive['parent'])
        if not re.fullmatch(r'zotero-[a-z0-9_]{8,}', stage['path']):
            raise ValueError('invalid_journal')
        if not isinstance(state['files'], dict) or not isinstance(state['publications'], list) or len(state['files']) + len(state['publications']) > MAX_ENTRIES:
            raise ValueError('invalid_journal')
        for name, identity in state['files'].items():
            if not re.fullmatch(r'zotero\.sqlite(?:-wal|-shm|-journal)?|[a-f0-9]{32}\.(?:bin|pdf)', name):
                raise ValueError('invalid_journal')
            if not isinstance(identity, dict) or set(identity) != {'dev','ino'} or any(type(v) is not int or v < 0 for v in identity.values()):
                raise ValueError('invalid_journal')
        for entry in state['publications']:
            if not isinstance(entry,dict):
                raise ValueError('invalid_journal')
            if (type(entry.get('owned')) is not bool or entry.get('source') not in state['files']
                    or not re.fullmatch(r'[a-f0-9]{64}', entry.get('sha256',''))
                    or type(entry.get('size')) is not int or not 0 <= entry['size'] <= MAX_FILE):
                raise ValueError('invalid_journal')
            checked_identity(entry['identity'])
            checked_identity(entry['parent'])
            self._target(entry)

    def save(self):
        self.validate()
        data = json.dumps(self.state, sort_keys=True).encode('utf-8')
        if len(data) > MAX_JOURNAL:
            raise ValueError('迁移恢复日志超过资源上限。')
        temporary = self.manager.directory / (uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('xb') as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            _sync_directory(self.manager.directory)
            self.journal_identity = _identity(_ordinary(self.path))
        finally:
            temporary.unlink(missing_ok=True)

    def _stage(self):
        path = self.manager.root / self.state['stage']['path']
        if path.exists() or _redirect(path):
            if _identity(_ordinary(path, True)) != self.state['stage']['identity']:
                raise ValueError('stage_replaced')
        return path

    def register_stage_file(self, path, info):
        stage = self._stage()
        if path.parent != stage or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('unsafe_stage_file')
        self.state['files'][path.name] = _identity(info)
        self.save()  # Durable inode record precedes the first private input byte. / 先持久登记 inode，再写私人输入字节。

    def set_receipt(self, receipt_id, archive_directory):
        if self.state['receiptId'] is not None or self.state['publications']:
            raise ValueError('上次迁移文件清理尚未完成，请先重启复核恢复状态。')
        self.state['receiptId'] = receipt_id
        if archive_directory.parent != self.manager.root / 'zotero-archive' or archive_directory.name != receipt_id:
            raise ValueError('unsafe_archive')
        self.state['archive'] = dict(path='zotero-archive/'+receipt_id,
                                    identity=_identity(_ordinary(archive_directory, True)),
                                    parent=_identity(_ordinary(archive_directory.parent, True)))
        self.save()

    def _target(self, entry):
        digest = entry['sha256']
        allowed = {'objects/'+digest+'.pdf'}
        if self.state.get('receiptId'):
            allowed.add('zotero-archive/'+self.state['receiptId']+'/'+digest+'.bin')
        if entry.get('target') not in allowed or '\\' in entry['target']:
            raise ValueError('invalid_target')
        return self.manager.root.joinpath(*PurePosixPath(entry['target']).parts)

    def _parent(self, entry):
        target = self._target(entry)
        if _identity(_ordinary(target.parent, True)) != entry['parent']:
            raise ValueError('parent_replaced')
        if target.parent.name == self.state.get('receiptId'):
            archive = self.state['archive']
            if _identity(_ordinary(target.parent.parent, True)) != archive['parent']:
                raise ValueError('archive_root_replaced')
        return target

    def _valid_file(self, path, expected, maximum_links):
        info = _ordinary(path)
        if _identity(info) != expected or not 1 <= info.st_nlink <= maximum_links:
            raise ValueError('file_replaced_or_linked')
        return info

    def publish(self, source, target, digest, size, check):
        stage = self._stage()
        if source.parent != stage or source.name not in self.state['files']:
            raise ValueError('unregistered_stage_file')
        source_links = 1 + sum(e['owned'] and e['source']==source.name and self._target(e).exists() for e in self.state['publications'])
        self._valid_file(source, self.state['files'][source.name], source_links)
        exists = target.exists() or _redirect(target)
        entry = dict(source=source.name, target=target.relative_to(self.manager.root).as_posix(),
                     sha256=digest, size=size, owned=not exists,
                     identity=_identity(_ordinary(target)) if exists else self.state['files'][source.name],
                     parent=_identity(_ordinary(target.parent, True)))
        self._target(entry)
        if exists:
            previous = next((e for e in self.state['publications'] if e['target']==entry['target']),None)
            max_links = 2 if previous and previous['owned'] and (stage/previous['source']).exists() else 1
            self._valid_file(target, entry['identity'], max_links)
            if target.stat().st_size != size:
                raise ValueError('已有受管对象尺寸不一致。')
            hasher = hashlib.sha256()
            with target.open('rb') as stream:
                while chunk := stream.read(1024*1024):
                    check()
                    hasher.update(chunk)
            if hasher.hexdigest() != digest:
                raise ValueError('已有受管对象校验失败。')
        if not any(e['target']==entry['target'] for e in self.state['publications']):
            self.state['publications'].append(entry)
            self.save()
        check()
        if not exists:
            # The target inherits an inode already recorded durably; no unknown copy window.
            # 目标继承已持久登记的 inode，不产生先复制后登记身份的窗口。
            os.link(source, target, follow_symlinks=False)
            _sync_directory(target.parent)
        return target

    def _referenced(self, db, entry):
        if entry['target'].startswith('objects/'):
            return db.execute('SELECT 1 FROM desktop_documents WHERE sha256=? LIMIT 1',(entry['sha256'],)).fetchone() is not None
        quoted = json.dumps(entry['target'])
        return db.execute('SELECT 1 FROM desktop_zotero_migrations WHERE instr(archive,?)>0 LIMIT 1',(quoted,)).fetchone() is not None

    def finish(self, db=None, keep_stage=False):
        if not self.path.exists() and not _redirect(self.path):
            return True
        self.validate()
        if db is not None and db.in_transaction:
            return False  # Never mistake caller-visible uncommitted rows for durable receipts. / 不把调用方未提交的行当持久收据。
        if db is None and self.state['receiptId'] is not None:
            return False  # Without SQL evidence a publication cannot be declared abandoned. / 无 SQL 证据不把发布判为废弃。
        committed = bool(self.state['receiptId'] and db.execute('SELECT 1 FROM desktop_zotero_migrations WHERE id=?',(self.state['receiptId'],)).fetchone())
        pending = False
        for entry in self.state['publications']:
            try:
                target = self._target(entry)
                if not target.exists() and not _redirect(target):
                    if committed:
                        pending = True
                    continue
                target = self._parent(entry)
                stage_file = self._stage()/entry['source']
                if not target.exists() and not _redirect(target):
                    if committed:
                        pending = True
                    continue
                max_links = 2 if entry['owned'] and stage_file.exists() else 1
                self._valid_file(target, entry['identity'], max_links)
                if not committed and entry['owned'] and not self._referenced(db,entry):
                    if os.name == 'nt':
                        target.chmod(stat.S_IRUSR|stat.S_IWUSR)
                    target.unlink()
                    _sync_directory(target.parent)
            except (OSError, ValueError):
                pending = True
        if keep_stage:
            archive = self.state.get('archive')
            if archive and not committed:
                directory = self.manager.root/archive['path']
                try:
                    if directory.exists():
                        if _identity(_ordinary(directory,True)) != archive['identity']:
                            raise ValueError('archive_replaced')
                        directory.rmdir()
                except (OSError,ValueError):
                    pending = True
            if not pending:
                self.state['publications'] = []
                self.state['receiptId'] = None
                self.state['archive'] = None
                self.save()
            return not pending
        # A committed missing/replaced target may have its only valid copy in staging.
        # 已提交目标缺失或被替换时，暂存可能是唯一有效副本，不能清掉它。
        if pending:
            return False
        stage = self._stage()
        for name, identity in self.state['files'].items():
            path = stage/name
            try:
                if not path.exists() and not _redirect(path):
                    continue
                links = 1 + sum(e['owned'] and e['source']==name and self._target(e).exists() for e in self.state['publications'])
                self._valid_file(path,identity,links)
                if os.name == 'nt':
                    path.chmod(stat.S_IRUSR|stat.S_IWUSR)
                path.unlink()
            except (OSError, ValueError):
                pending = True
        # Restore the immutable attribute after removing our own Windows hardlink.
        # 删除 Windows 暂存硬链接后恢复不可写属性；不永久改变已提交对象权限。
        for entry in self.state['publications']:
            target = self._target(entry)
            if target.exists() and (committed or self._referenced(db,entry)):
                try:
                    self._parent(entry)
                    self._valid_file(target,entry['identity'],1)
                    target.chmod(stat.S_IRUSR)
                except (OSError, ValueError):
                    pending = True
        if stage.exists():
            try:
                stage.rmdir()
                _sync_directory(self.manager.root)
            except OSError:
                pending = True  # Unknown/replaced leaves are preserved, never swept. / 未知或替换叶节点保留，不遍历删除。
        archive = self.state.get('archive')
        if archive and not committed:
            path = self.manager.root/archive['path']
            try:
                if path.exists():
                    if _identity(_ordinary(path,True)) != archive['identity']:
                        raise ValueError('archive_replaced')
                    path.rmdir()
            except (OSError, ValueError):
                pending = True
        if not pending:
            if self.journal_identity is not None and _identity(_ordinary(self.path)) != self.journal_identity:
                return False
            self.path.unlink(missing_ok=True)
            _sync_directory(self.manager.directory)
            if committed:
                row = db.execute('SELECT receipt FROM desktop_zotero_migrations WHERE id=?',(self.state['receiptId'],)).fetchone()
                receipt = json.loads(row[0])
                receipt['publicationCleanupComplete'] = True
                db.execute('UPDATE desktop_zotero_migrations SET receipt=? WHERE id=?',
                           (json.dumps(receipt,ensure_ascii=False),self.state['receiptId']))
                db.commit()
        return not pending
