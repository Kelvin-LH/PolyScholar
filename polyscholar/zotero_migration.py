# SPDX-License-Identifier: AGPL-3.0-only
"""Closed-library Zotero snapshots and loss-aware local migration.

只以 OS 只读句柄读取源文件；SQLite 仅打开应用拥有的副本。
Source files are never opened by SQLite, checkpointed or modified.
"""
from datetime import datetime, timezone
import copy
from contextlib import redirect_stderr, redirect_stdout
import multiprocessing
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
import uuid
from .bibliographic import BibliographicPolicy
from .metadata import validate_date
from .migration_recovery import MigrationRecovery
from .zotero_resources import ZoteroResourcePlanner, resource_identity, tree_manifest

MIGRATION_SCHEMA = '''
CREATE TABLE IF NOT EXISTS desktop_zotero_migrations(
 id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
 receipt TEXT NOT NULL, archive TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS desktop_zotero_mapping(
 migration_id TEXT NOT NULL REFERENCES desktop_zotero_migrations(id) ON DELETE CASCADE,
 source_id TEXT NOT NULL, local_id TEXT REFERENCES desktop_documents(id) ON DELETE SET NULL,
 PRIMARY KEY(migration_id,source_id));
'''
TABLES = (
    'version', 'libraries', 'groups', 'items', 'itemTypes', 'itemTypesCombined',
    'fields', 'fieldsCombined', 'baseFieldMappings', 'baseFieldMappingsCombined',
    'itemData', 'itemDataValues', 'creators', 'creatorTypes', 'creatorTypesCombined',
    'itemCreators', 'collections', 'collectionItems', 'tags', 'itemTags',
    'itemNotes', 'itemAttachments', 'itemAnnotations', 'itemRelations',
    'relationPredicates', 'collectionRelations', 'savedSearchRelations', 'deletedItems', 'deletedCollections', 'deletedSearches',
    'savedSearches', 'savedSearchConditions', 'syncedSettings',
)
TYPES = {'journalArticle': 'article-journal', 'conferencePaper': 'paper-conference',
         'book': 'book', 'thesis': 'thesis'}
FIELDS = {'title': 'title', 'DOI': 'doi', 'publicationTitle': 'publicationTitle',
          'proceedingsTitle': 'publicationTitle', 'publisher': 'publisher',
          'place': 'place', 'volume': 'volume', 'issue': 'issue', 'pages': 'pages',
          'ISBN': 'isbn', 'edition': 'edition', 'conferenceName': 'eventTitle',
          'university': 'institution', 'thesisType': 'thesisType'}


def unsafe_link(path):
    # Windows junctions are also redirects; do not follow them as source directories.
    # Windows 目录联接同样会重定向路径，不能当作安全来源目录。
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


class ZoteroMigrationPolicy:
    max_database_bytes = 256 * 1024 * 1024
    max_archive_bytes = 64 * 1024 * 1024
    max_resources = 20000
    max_items = 10000
    max_rows = 500000
    max_pdf_bytes = 100 * 1024 * 1024
    max_total_pdf_bytes = 1024 * 1024 * 1024
    timeout_seconds = 30
    supported_versions = frozenset({121, 123, 130})


class ZoteroSnapshotReader:
    """Copy bounded stable ordinary files; query only the private snapshot.

    复制有界、稳定的普通文件；不通过只读 SQLite 触碰源 WAL/SHM。
    """
    def __init__(self, staging, cancelled, policy):
        self.staging, self.cancelled, self.policy = staging, cancelled, policy
        self.deadline = time.monotonic() + policy.timeout_seconds

    def check(self):
        if self.cancelled.is_set():
            raise ValueError('迁移已取消，未导入任何条目。')
        if time.monotonic() >= self.deadline:
            raise ValueError('Zotero 预览超过处理时限，请缩小资料范围。')

    def _read(self, path, destination=None, maximum=None):
        self.check()
        # O_NOFOLLOW protects the opened leaf; ancestor validation is repeated.
        # 叶节点禁止符号链接，同时再次核对祖先；不追踪目录逃逸。
        if unsafe_link(path) or any(unsafe_link(p) for p in path.parents):
            raise ValueError('Zotero 来源不能通过符号链接访问。')
        # A FIFO can block before fstat; lstat plus NONBLOCK protects both the
        # ordinary check and a replacement race. FIFO 在 fstat 前即可阻塞；
        # 先核对 lstat，再用非阻塞打开防止普通文件被竞态替换为管道。
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError('Zotero 来源必须是普通文件。')
        flags = (os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                 | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
        fd = os.open(path, flags)
        output = None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('Zotero 来源必须是普通文件。')
            maximum = maximum or self.policy.max_database_bytes
            if info.st_size > maximum:
                raise ValueError('Zotero 来源文件超过迁移上限。')
            digest, size = hashlib.sha256(), 0
            if destination:
                output = destination.open('xb')
                session = getattr(self,'recovery_session',None)
                if session:
                    session.register_stage_file(destination,os.fstat(output.fileno()))
            while True:
                self.check()
                data = os.read(fd, 1024 * 1024)
                if not data:
                    break
                size += len(data)
                if size > maximum:
                    raise ValueError('Zotero 来源文件超过迁移上限。')
                digest.update(data)
                if output:
                    output.write(data)
            end = os.fstat(fd)
            current = path.stat(follow_symlinks=False)
            identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
            if identity(info) != identity(end) or identity(end) != identity(current):
                raise ValueError('Zotero 来源已改变，请关闭 Zotero 后重新预览。')
            if output:
                output.flush()
                os.fsync(output.fileno())
            return [*identity(end), digest.hexdigest()]
        finally:
            if output:
                output.close()
            os.close(fd)

    def manifest(self, directory, copy_files=False):
        result = {}
        for suffix in ('', '-wal', '-shm', '-journal'):
            name = 'zotero.sqlite' + suffix
            source = directory / name
            if source.exists() or source.is_symlink():
                result[name] = self._read(source, self.staging / name if copy_files else None)
        if 'zotero.sqlite' not in result:
            raise ValueError('请选择包含 zotero.sqlite 的资料目录。')
        return result

    def graph(self, directory):
        before = self.manifest(directory, True)
        if before != self.manifest(directory):
            raise ValueError('Zotero 来源已改变，请关闭 Zotero 后重新预览。')
        # Recovery, WAL integration and integrity checks affect only this copy.
        # 恢复、WAL 整合和完整性检查只作用于应用副本。
        session = getattr(self,'recovery_session',None)
        if session:
            # SQLite may create derived WAL/SHM while recovering the private copy.
            # 先登记空侧车 inode，SQLite 恢复副本时派生的 WAL/SHM 也可安全回收。
            for name in ('zotero.sqlite-wal','zotero.sqlite-shm'):
                sidecar = self.staging/name
                if not sidecar.exists():
                    with sidecar.open('xb') as output:
                        session.register_stage_file(sidecar,os.fstat(output.fileno()))
                        output.flush()
                        os.fsync(output.fileno())
        db = sqlite3.connect(self.staging / 'zotero.sqlite', timeout=1)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA trusted_schema=OFF')
        db.set_progress_handler(lambda: int(self.cancelled.is_set() or time.monotonic() >= self.deadline), 1000)
        try:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Zotero 副本完整性校验失败。')
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND sql NOT LIKE 'CREATE VIRTUAL TABLE%'")}
            all_tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.unarchived_tables = sorted(all_tables - set(TABLES))
            required = {'items', 'itemData', 'itemDataValues', 'version'}
            if not required <= names:
                raise ValueError('Zotero 数据结构不受支持。')
            columns = {
                'items': {'itemID','itemTypeID','key','libraryID'},
                'itemData': {'itemID','fieldID','valueID'},
                'itemDataValues': {'valueID','value'},
                'version': {'schema','version'},
            }
            for table, needed in columns.items():
                available = {r[1] for r in db.execute('PRAGMA table_info("'+table+'")')}
                if not needed <= available:
                    raise ValueError('Zotero 命名列结构尚未适配。')
            graph, count, bytes_used = {}, 0, 0
            for table in TABLES:
                if table not in names:
                    continue
                rows = []
                for row in db.execute('SELECT * FROM "' + table + '" ORDER BY rowid'):
                    self.check()
                    value = dict(row)
                    if any(isinstance(v, bytes) for v in value.values()):
                        raise ValueError('Zotero 数据含不受支持的二进制字段。')
                    count += 1
                    bytes_used += len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
                    if count > self.policy.max_rows or bytes_used > self.policy.max_archive_bytes:
                        raise ValueError('Zotero 档案超过迁移资源上限，未截断数据。')
                    rows.append(value)
                graph[table] = rows
            versions = {row['schema']: row['version'] for row in graph['version']}
            if versions.get('userdata') not in self.policy.supported_versions:
                raise ValueError('Zotero 数据库版本尚未适配，未导入任何条目。')
            if len(graph['items']) > self.policy.max_items:
                raise ValueError('Zotero 条目超过 10000 项，未截断数据。')
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('Zotero 来源关系不完整，未导入任何条目。')
            ids = {r['itemID'] for r in graph['items']}
            if len(ids) != len(graph['items']):
                raise ValueError('Zotero 来源身份重复。')
            for table in ('itemData','itemCreators','itemTags','itemNotes','itemAttachments','itemAnnotations','itemRelations','deletedItems','collectionItems'):
                for row in graph.get(table, []):
                    if row.get('itemID') not in ids or (row.get('parentItemID') is not None and row['parentItemID'] not in ids):
                        raise ValueError('Zotero 来源条目关系不完整。')
            self.check()
            return graph, before, versions['userdata']
        except sqlite3.Error:
            self.check()
            raise ValueError('Zotero 副本无法安全读取。') from None
        finally:
            db.close()


class ZoteroMigrationPlanner:
    """Keep every source row; native adaptation is an explicit subset.

    原始图谱完整留存，原生适配与档案保留分开计数。
    """
    def plan(self, graph):
        def lookup(table, key, value):
            return {row[key]: row[value] for row in graph.get(table, [])}
        types = lookup('itemTypes', 'itemTypeID', 'typeName')
        types.update(lookup('itemTypesCombined', 'itemTypeID', 'typeName'))
        fields = lookup('fields', 'fieldID', 'fieldName')
        fields.update(lookup('fieldsCombined', 'fieldID', 'fieldName'))
        roles = lookup('creatorTypes', 'creatorTypeID', 'creatorType')
        roles.update(lookup('creatorTypesCombined', 'creatorTypeID', 'creatorType'))
        values = lookup('itemDataValues', 'valueID', 'value')
        creators = {row['creatorID']: row for row in graph.get('creators', [])}
        metadata, creator_rows, tags = {}, {}, {}
        for row in graph.get('itemData', []):
            if row['fieldID'] not in fields or row['valueID'] not in values:
                raise ValueError('Zotero 字段映射不完整。')
            metadata.setdefault(row['itemID'], {})[fields[row['fieldID']]] = values[row['valueID']]
        for row in sorted(graph.get('itemCreators', []), key=lambda r: (r['itemID'], r['orderIndex'])):
            if row['creatorID'] not in creators or row['creatorTypeID'] not in roles:
                raise ValueError('Zotero 作者关系不完整。')
            creator_rows.setdefault(row['itemID'], []).append((roles[row['creatorTypeID']], creators[row['creatorID']]))
        tag_names = lookup('tags', 'tagID', 'name')
        for row in graph.get('itemTags', []):
            if row['tagID'] not in tag_names:
                raise ValueError('Zotero 标签关系不完整。')
            tags.setdefault(row['itemID'], []).append(tag_names[row['tagID']])
        deleted = {row['itemID'] for row in graph.get('deletedItems', [])}
        items = []
        note_titles = {r['itemID']: r.get('title') or '' for r in graph.get('itemNotes',[])}
        for row in graph['items']:
            kind = types.get(row['itemTypeID'])
            if kind is None:
                raise ValueError('Zotero 条目类型映射不完整。')
            raw = metadata.get(row['itemID'], {})
            result = dict(sourceId=str(row['itemID']), key=row.get('key'), itemType=kind,
                          title=raw.get('title', '') or note_titles.get(row['itemID'], ''), status='archive', metadata=None,
                          deleted=row['itemID'] in deleted, warnings=[])
            if kind in TYPES:
                adapted = {'itemType': TYPES[kind], 'tags': tags.get(row['itemID'], []), 'creators': []}
                for source, target in FIELDS.items():
                    if source in raw:
                        adapted[target] = raw[source]
                for role, creator in creator_rows.get(row['itemID'], []):
                    if role not in ('author', 'editor'):
                        result['warnings'].append('未适配作者角色保留在档案：' + role)
                        continue
                    name = dict(role=role, type='person', literal='', family='', given='')
                    if creator.get('fieldMode') == 1:
                        name['literal'] = creator.get('lastName', '')
                        result['warnings'].append('单字段姓名按完整姓名保存，未推断机构身份。')
                    else:
                        name.update(family=creator.get('lastName', ''), given=creator.get('firstName', ''))
                    adapted['creators'].append(name)
                if raw.get('date'):
                    try:
                        adapted['date'] = validate_date(raw['date'])
                    except ValueError:
                        result['warnings'].append('未规范日期仅保留在档案。')
                unsupported = set(raw) - set(FIELDS) - {'date'}
                if unsupported:
                    result['warnings'].append('额外字段完整保留在档案：' + ', '.join(sorted(unsupported)))
                try:
                    result['metadata'] = BibliographicPolicy.metadata_change({'title': '', 'authors': '', 'year': '', 'doi': '', 'tags': [], 'notes': ''}, adapted)
                    result['metadata'] = {k: v for k, v in result['metadata'].items() if k in BibliographicPolicy.editable_fields}
                    result['status'] = 'native'
                except ValueError:
                    result['warnings'].append('原生字段校验未通过，完整记录仅保留档案。')
            elif kind not in ('attachment', 'note', 'annotation'):
                result['warnings'].append('此类型尚未适配原生工作流，完整保存于迁移档案。')
            items.append(result)
        return items


def _validate_pdf_worker(path, connection):
    """Only fixed JSON leaves the PDF subprocess / PDF 子进程只返回固定 JSON。"""
    with open(os.devnull, 'w') as quiet, redirect_stdout(quiet), redirect_stderr(quiet):
        if os.name == 'posix':
            os.dup2(quiet.fileno(), 1)
            os.dup2(quiet.fileno(), 2)
        valid = False
        try:
            import pymupdf
            with pymupdf.open(path, filetype='pdf') as document:
                valid = not document.is_encrypted and document.page_count > 0
        except BaseException:
            pass  # Private parser diagnostics do not cross the process boundary. / 私人诊断不跨进程。
        try:
            connection.send_bytes(b'{"valid":true}' if valid else b'{"valid":false}')
        except (OSError, BrokenPipeError):
            pass
        finally:
            connection.close()


class ZoteroPdfValidator:
    """Supervise native parsing, including complete IPC reception.

    监督原生解析及完整 IPC 接收；挂起或崩溃不影响桌面进程。
    """
    timeout_seconds = 10

    def __init__(self):
        self._lock = threading.RLock()
        self._workers = set()
        self._closed = False
        self._worker_target = _validate_pdf_worker

    @staticmethod
    def _stop(worker):
        if worker.is_alive():
            worker.terminate()
        worker.join(timeout=1)
        if worker.is_alive():
            worker.kill()
            worker.join(timeout=1)

    def close(self):
        with self._lock:
            self._closed = True
            workers = list(self._workers)
        for worker in workers:
            self._stop(worker)

    def validate(self, path, cancelled, deadline):
        context = multiprocessing.get_context('spawn')
        receiver, sender = context.Pipe(duplex=False)
        worker = context.Process(target=self._worker_target, args=(str(path), sender), daemon=True)
        finished, responses = threading.Event(), []
        reader = None
        deadline = min(deadline, time.monotonic() + self.timeout_seconds)

        def receive():
            try:
                responses.append(receiver.recv_bytes(maxlength=64))
            except (OSError, EOFError):
                responses.append(None)
            finally:
                finished.set()

        try:
            with self._lock:
                if self._closed or cancelled.is_set():
                    raise ValueError('迁移已取消，未导入任何条目。')
                worker.start()
                self._workers.add(worker)
            sender.close()
            reader = threading.Thread(target=receive, daemon=True)
            reader.start()
            while not finished.wait(0.05):
                if self._closed or cancelled.is_set():
                    raise ValueError('迁移已取消，未导入任何条目。')
                if time.monotonic() >= deadline:
                    return False, 'pdf-validation-timeout'
            if self._closed or cancelled.is_set():
                raise ValueError('迁移已取消，未导入任何条目。')
            if time.monotonic() >= deadline:
                return False, 'pdf-validation-timeout'
            if responses and responses[0] == b'{"valid":true}':
                return True, None
            return False, 'pdf-validation-failed'
        finally:
            sender.close()
            if worker.pid is not None:
                self._stop(worker)
            if reader:
                reader.join(timeout=1)
            receiver.close()
            with self._lock:
                self._workers.discard(worker)


class ZoteroMigrationImporter:
    def __init__(self, root, policy=None):
        self.root, self.policy = Path(root), policy or ZoteroMigrationPolicy()
        self._cancelled = threading.Event()
        self._lock = threading.RLock()
        self._previews = {}
        self._closed = False
        self._busy = False
        self._pdf_validator = ZoteroPdfValidator()

    def cancel(self):
        self._cancelled.set()

    def close(self):
        self._closed = True
        self._cancelled.set()
        self._pdf_validator.close()
        with self._lock:
            for state in self._previews.values():
                session = state.get('recoverySession')
                if session:
                    try:
                        session.finish(None)
                    except (OSError,ValueError):
                        pass  # Preserve the journal; unsafe cleanup must not prevent shutdown. / 保留日志，危险清理不能阻止关闭。
            self._previews.clear()

    def preview(self, directory, linked_directory=None):
        with self._lock:
            if self._closed or self._busy:
                raise ValueError('迁移处理正在进行或已关闭。')
            self._busy = True
            self._cancelled = threading.Event()
        stage, session = None, None
        try:
            directory = Path(directory).absolute()
            linked_directory = Path(linked_directory).absolute() if linked_directory else None
            for path in (directory, linked_directory):
                if path is not None and (not path.is_dir() or unsafe_link(path) or any(unsafe_link(p) for p in path.parents)):
                    raise ValueError('请选择普通本地目录，不通过链接访问资料。')
            linked_identity = self.directory_identity(linked_directory) if linked_directory else None
            stage = Path(tempfile.mkdtemp(prefix='zotero-', dir=self.root))
            session = MigrationRecovery(self.root).begin_stage(stage)
            reader = ZoteroSnapshotReader(stage, self._cancelled, self.policy)
            reader.recovery_session = session
            graph, manifest, version = reader.graph(directory)
            items = ZoteroMigrationPlanner().plan(graph)
            child_ids = {r['itemID'] for table in ('itemAttachments', 'itemNotes', 'itemAnnotations')
                         for r in graph.get(table, []) if r.get('parentItemID') is not None}
            for row in items:
                row['selectable'] = int(row['sourceId']) not in child_ids
            planner = ZoteroResourcePlanner(reader, self._pdf_validator, self._cancelled, self.policy, unsafe_link)
            resources, pdfs, archive_files, trees = planner.plan(directory, graph, items, linked_directory)
            if manifest != reader.manifest(directory) or (linked_directory and linked_identity != self.directory_identity(linked_directory)):
                raise ValueError('Zotero 来源已改变，请重新预览。')
            collection_warnings = []
            for collection in graph.get('collections', []):
                name = collection.get('collectionName')
                if not isinstance(name, str) or not name.strip() or len(name) > 128 or any(ord(c) < 32 or c in '\x7f\x85\u2028\u2029' for c in name):
                    collection_warnings.append('集合名称未通过原生校验，该集合及其子树仅保留档案：' + str(collection['collectionID']))
            # 明确资源范围参与收据身份；目录或资源变化必须重新预览。
            # Include explicit resource scope in receipt identity; changed sources require a fresh preview.
            fingerprint_data = dict(database=manifest, linkedDirectory=str(linked_directory) if linked_directory else None,
                                    resources=[dict(id=resource_identity(r), sha256=r.get('sha256'), status=r['status']) for r in resources])
            fingerprint = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True).encode()).hexdigest()
            token = uuid.uuid4().hex
            counts = dict(items=len(items), native=sum(i['status'] == 'native' for i in items),
                          archive=sum(i['status'] == 'archive' for i in items), pdfs=len(pdfs), resources=len(resources),
                          missing=sum(r['status'] == 'missing' for r in resources),
                          unsupported=sum(r['status'] not in ('pdf-ready', 'archive-ready', 'not-required') for r in resources),
                          archivedResources=len(archive_files))
            warnings = ['未归档的内部/未适配表：' + ', '.join(reader.unarchived_tables),
                        '请保持 Zotero 关闭；未适配字段、笔记 HTML、批注和关联保存在本地档案，未加载外链。',
                        '链接附件只读取明确选定目录内的文件；批注缓存仅保存字节，尚未恢复原生批注。',
                        '选择部分条目时只保存所选条目及其子图；全库保存搜索及设置仅全选时归档。']
            preview = dict(token=token, sourceName=directory.name, fingerprint=fingerprint, schemaVersion=version,
                           items=items, collections=graph.get('collections', []), resources=resources,
                           counts=counts, warnings=warnings + collection_warnings)
            archive_size = len(json.dumps(dict(tables=graph, items=items, resources=resources), ensure_ascii=False).encode('utf-8'))
            if archive_size > self.policy.max_archive_bytes:
                raise ValueError('Zotero 完整档案超过迁移上限，未截断数据。')
            with self._lock:
                reader.check()
                if len(self._previews) >= 2:
                    raise ValueError('请先完成或关闭已有迁移预览。')
                self._previews[token] = dict(preview=preview, graph=graph, stage=stage, recoverySession=session,
                    directory=directory, manifest=manifest, pdfs=pdfs, archiveFiles=archive_files,
                    resourceTrees=trees, linkedDirectory=linked_directory, linkedIdentity=linked_identity,
                    maxArchiveBytes=self.policy.max_archive_bytes)
            return copy.deepcopy(preview)
        except Exception:
            if session:
                try:
                    session.finish(None)
                except (OSError, ValueError):
                    pass  # 留待身份安全恢复，不掩盖原始错误。 / Defer safe recovery without masking the original failure.
            elif stage:
                shutil.rmtree(stage, ignore_errors=True)
            raise
        finally:
            with self._lock:
                self._busy = False

    @staticmethod
    def directory_identity(path):
        if unsafe_link(path) or any(unsafe_link(p) for p in path.parents):
            raise ValueError('链接附件目录已改变，请重新预览。')
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('链接附件目录已改变，请重新预览。')
        return [info.st_dev, info.st_ino]

    def selected(self, preview, selected_ids=None):
        with self._lock:
            state = self._previews.get(preview.get('token')) if isinstance(preview, dict) else None
            if not state or state['preview'] != preview:
                raise ValueError('迁移预览无效或已改变，请重新预览。')
            self.validate_state(state)
            possible = {row['sourceId'] for row in preview['items'] if row['selectable']}
            selected_ids = list(possible) if selected_ids is None else selected_ids
            if not isinstance(selected_ids, list) or not selected_ids or len(selected_ids) > self.policy.max_items or any(type(x) is not str or x not in possible for x in selected_ids) or len(set(selected_ids)) != len(selected_ids):
                raise ValueError('请选择有效、唯一的源书目条目。')
            return state, set(selected_ids)

    def validate_state(self, state):
        reader = ZoteroSnapshotReader(state['stage'], self._cancelled, self.policy)
        if reader.manifest(state['directory']) != state['manifest']:
            raise ValueError('Zotero 来源已改变，请重新预览。')
        if state.get('linkedDirectory') and self.directory_identity(state['linkedDirectory']) != state['linkedIdentity']:
            raise ValueError('链接附件目录已改变，请重新预览。')
        for tree in state.get('resourceTrees', []):
            if tree_manifest(Path(tree['rootPath']), reader, unsafe_link) != tree['manifest']:
                raise ValueError('快照目录已改变，请重新预览。')
        for resource in state['pdfs'] + state['archiveFiles']:
            if reader._read(resource['source'], maximum=self.policy.max_pdf_bytes) != resource['identity']:
                raise ValueError('Zotero 附件已改变，请重新预览。')

    def consumed(self, preview):
        with self._lock:
            state = self._previews.pop(preview['token'])
            session = state.get('recoverySession')
            if session:
                try:
                    session.finish(None)
                except (OSError,ValueError):
                    pass  # A committed receipt remains successful; startup retries cleanup. / 已提交收据仍成功，启动时重试清理。


def verify_staged(path, expected_digest, check):
    """Verify the private copy again before publication / 发布前再次校验私有副本。"""
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while True:
            check()
            chunk = source.read(1024*1024)
            if not chunk:
                break
            digest.update(chunk)
    if digest.hexdigest() != expected_digest:
        raise ValueError('迁移暂存文件已改变，请重新预览。')


def scoped_graph(graph, selected, items):
    """Keep selected items' transitive children without importing private siblings.

    只保留所选条目的传递子项；全选时保留完整图谱，包括空集合和保存搜索。
    """
    possible = {row['sourceId'] for row in items if row['selectable']}
    if selected == possible:
        return graph
    included = {int(identifier) for identifier in selected}
    relationships = [r for table in ('itemNotes','itemAttachments','itemAnnotations') for r in graph.get(table, [])]
    while True:
        expanded = included | {r['itemID'] for r in relationships if r.get('parentItemID') in included}
        if expanded == included:
            break
        included = expanded
    result = {}
    item_tables = {'items','itemData','itemCreators','itemTags','itemNotes','itemAttachments','itemAnnotations','itemRelations','deletedItems','collectionItems'}
    for table, rows in graph.items():
        result[table] = [r for r in rows if r['itemID'] in included] if table in item_tables else rows
    collection_ids = {r['collectionID'] for r in result.get('collectionItems', [])}
    while True:
        parents = {r['parentCollectionID'] for r in graph.get('collections',[]) if r['collectionID'] in collection_ids and r.get('parentCollectionID') is not None}
        if parents <= collection_ids:
            break
        collection_ids |= parents
    for table in ('collections','collectionRelations','deletedCollections'):
        if table in result:
            result[table] = [r for r in result[table] if r['collectionID'] in collection_ids]
    library_ids = {r['libraryID'] for r in result['items']}
    for table in ('libraries','groups'):
        if table in result:
            result[table] = [r for r in result[table] if r.get('libraryID') in library_ids]
    used_values = {r['valueID'] for r in result.get('itemData',[])}
    result['itemDataValues'] = [r for r in result['itemDataValues'] if r['valueID'] in used_values]
    used_creators = {r['creatorID'] for r in result.get('itemCreators',[])}
    if 'creators' in result:
        result['creators'] = [r for r in result['creators'] if r['creatorID'] in used_creators]
    used_tags = {r['tagID'] for r in result.get('itemTags',[])}
    if 'tags' in result:
        result['tags'] = [r for r in result['tags'] if r['tagID'] in used_tags]
    # Saved searches/settings are library-wide; a partial item selection does not copy them.
    # 保存搜索及设置属于整个库，部分条目迁移不复制这些私人范围。
    for table in ('savedSearches','savedSearchConditions','savedSearchRelations','deletedSearches','syncedSettings'):
        if table in result:
            result[table] = []
    return result


class ZoteroMigrationLibrary:
    """One DB commit covers native records, provenance, archive and audit.

    原生数据、来源映射、完整档案与审计在同一事务提交。
    """
    def import_zotero_state(self, state, selected, cancelled=None, verify_source=None):
        preview = state['preview']
        graph = scoped_graph(state['graph'], selected, preview['items'])
        scoped_items = [i for i in preview['items'] if int(i['sourceId']) in {r['itemID'] for r in graph['items']}]
        scoped_resources = [copy.deepcopy(r) for r in preview['resources'] if r['sourceId'] in {i['sourceId'] for i in scoped_items}]
        receipt_id = str(uuid.uuid4())
        session = state['recoverySession']
        archive_directory = self.root / 'zotero-archive' / receipt_id
        def check():
            if cancelled is not None and cancelled.is_set():
                raise ValueError('迁移已取消，未导入任何条目。')
        check()
        documents, mapping = [], {}
        for item in preview['items']:
            if item['sourceId'] in selected and item['status'] == 'native':
                document = self._new_bibliographic_document(item['metadata'])
                documents.append(document)
                mapping[item['sourceId']] = document['id']
        committed = False
        try:
            with self.lock, self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT 1 FROM desktop_zotero_migrations WHERE fingerprint=?',(preview['fingerprint'],)).fetchone():
                    raise ValueError('此资料快照已经迁移，请查看已有收据。')
                check()
                self._insert_bibliographic_documents(db, documents, None)
                collections, pending = {}, list(graph.get('collections', []))
                deleted_collections = {row['collectionID'] for row in graph.get('deletedCollections', [])}
                while pending:
                    progressed = False
                    for row in pending[:]:
                        parent = row.get('parentCollectionID')
                        if parent is not None and parent not in collections and parent not in deleted_collections:
                            continue
                        pending.remove(row)
                        progressed = True
                        if row['collectionID'] in deleted_collections or parent in deleted_collections:
                            deleted_collections.add(row['collectionID'])
                            continue
                        name = row['collectionName']
                        if not isinstance(name,str) or not name.strip() or len(name)>128 or any(ord(c)<32 or c in '\x7f\x85\u2028\u2029' for c in name):
                            deleted_collections.add(row['collectionID'])
                            continue  # Raw collection is still retained in the archive. / 原集合仍完整保留档案。
                        parent_id = collections.get(parent)
                        final, counter = name, 1
                        while db.execute("SELECT 1 FROM desktop_collections WHERE name=? AND COALESCE(parent_id,'')=COALESCE(?,'')",(final,parent_id)).fetchone():
                            counter += 1
                            final = name[:105] + ' · Zotero ' + str(counter)
                        identifier = str(uuid.uuid4())
                        db.execute('INSERT INTO desktop_collections VALUES(?,?,?)',(identifier,final,parent_id))
                        collections[row['collectionID']] = identifier
                    if not progressed:
                        raise ValueError('Zotero 集合关系包含循环或缺失父集合。')
                for row in graph.get('collectionItems', []):
                    local = mapping.get(str(row['itemID']))
                    collection = collections.get(row['collectionID'])
                    if local and collection:
                        db.execute('INSERT OR IGNORE INTO desktop_memberships VALUES(?,?)',(local,collection))
                archive_parent = archive_directory.parent
                if archive_parent.is_symlink():
                    raise ValueError('迁移档案存储路径不安全。')
                archive_parent.mkdir(exist_ok=True)
                archive_directory.mkdir()
                session.set_receipt(receipt_id,archive_directory)
                scoped_resource_ids = {r['sourceId'] for r in scoped_resources}
                archived_count = 0
                for record in state['archiveFiles']:
                    check()
                    resource = record['resource']
                    if resource['sourceId'] not in scoped_resource_ids:
                        continue
                    verify_staged(record['staged'], resource['sha256'], check)
                    target = archive_directory / (resource['sha256'] + '.bin')
                    session.publish(record['staged'],target,resource['sha256'],resource['sizeBytes'],check)
                    resource_id = resource.get('resourceId',resource['sourceId'])
                    next(r for r in scoped_resources if r.get('resourceId',r['sourceId'])==resource_id)['archivedPath'] = str(target.relative_to(self.root))
                    archived_count += 1
                deleted = {str(row['itemID']) for row in graph.get('deletedItems', [])}
                for pdf in state['pdfs']:
                    check()
                    resource = pdf['resource']
                    parent = mapping.get(resource['parentSourceId'])
                    if not parent:
                        continue
                    digest = resource['sha256']
                    verify_staged(pdf['staged'], digest, check)
                    target = self.objects / (digest + '.pdf')
                    session.publish(pdf['staged'],target,digest,resource['sizeBytes'],check)
                    doc = dict(id=str(uuid.uuid4()), title=resource['filename'], filename=resource['filename'],
                               sha256=digest,sizeBytes=resource['sizeBytes'],authors='',doi='',year='',tags=[],notes='',
                               sourcePaths=[str(pdf['source'])],fileKind='pdf',createdAt=datetime.now(timezone.utc).isoformat())
                    db.execute('INSERT INTO desktop_documents VALUES(?,?,?)',(doc['id'],digest,json.dumps(doc,ensure_ascii=False)))
                    db.execute('INSERT INTO desktop_attachment_links VALUES(?,?,?,?)',(parent,doc['id'],'supplement',resource['filename'][:1024]))
                    mapping[resource['sourceId']] = doc['id']
                    root = next(d for d in documents if d['id']==parent)
                    if not root['primaryPdfId'] and resource['sourceId'] not in deleted:
                        root['primaryPdfId'] = doc['id']
                        db.execute('UPDATE desktop_documents SET data=? WHERE id=?',(json.dumps(root,ensure_ascii=False),parent))
                for source_id, local_id in mapping.items():
                    if source_id in deleted:
                        db.execute('INSERT INTO desktop_trash VALUES(?,?)',(local_id,datetime.now(timezone.utc).isoformat()))
                receipt = dict(id=receipt_id, publicationCleanupComplete=False, fingerprint=preview['fingerprint'], sourceName=preview['sourceName'], sourceDirectory=str(state['directory']), linkedDirectory=str(state.get('linkedDirectory') or ''), schemaVersion=preview['schemaVersion'],
                               createdAt=datetime.now(timezone.utc).isoformat(), selectedSourceIds=sorted(selected),
                               counts=dict(selected=len(selected),native=len(documents),pdfs=len(mapping)-len(documents),archive=len(scoped_items), nativeActive=sum(i['sourceId'] in selected and i['status']=='native' and not i['deleted'] for i in scoped_items),nativeTrashed=sum(i['sourceId'] in selected and i['status']=='native' and i['deleted'] for i in scoped_items),resources=len(scoped_resources),archivedResources=archived_count),
                               mappings=mapping, sourceIdentities={str(r['itemID']): {'libraryID':r['libraryID'],'key':r['key'],'localId':mapping.get(str(r['itemID']))} for r in graph['items']}, collectionMappings={str(k):v for k,v in collections.items()}, warnings=preview['warnings'])
                archive = dict(tables=graph, items=scoped_items, resources=scoped_resources)
                archive_json = json.dumps(archive, ensure_ascii=False)
                if len(archive_json.encode('utf-8')) > state['maxArchiveBytes']:
                    raise ValueError('Zotero 完整档案超过迁移上限，未截断数据。')
                db.execute('INSERT INTO desktop_zotero_migrations VALUES(?,?,?,?,?)',(receipt_id,preview['fingerprint'],receipt['createdAt'],json.dumps(receipt,ensure_ascii=False),archive_json))
                for item in scoped_items:
                    db.execute('INSERT INTO desktop_zotero_mapping VALUES(?,?,?)',(receipt_id,item['sourceId'],mapping.get(item['sourceId'])))
                check()
                if verify_source:
                    verify_source(state)
                db.execute('INSERT INTO desktop_audit(point,outcome,created_at) VALUES(?,?,?)',('zotero_migrated','succeeded',receipt['createdAt']))
            committed = True
            with self.connection() as db:
                cleanup_complete = session.finish(db)
            receipt['publicationCleanupComplete'] = cleanup_complete
            return receipt
        except Exception:
            if committed:
                receipt['publicationCleanupComplete'] = False
                return receipt  # Commit succeeded; cleanup failure is separately retryable. / 提交已成功，清理失败单独重试。
            # SQL has rolled back; journal cleanup retains the reusable trusted preview.
            # SQL 已回滚；只清理本轮发布，保留可信预览以便重试。
            with self.connection() as db:
                session.finish(db,keep_stage=True)
            raise

    def export_zotero_resource(self, receipt_id, source_id, destination):
        record = self.read_zotero_migration(receipt_id)
        resources = record['archive']['resources']
        resource = next((row for row in resources if resource_identity(row) == source_id), None)
        if not resource or not resource.get('archivedPath'):
            raise ValueError('此资源没有可导出的本地档案文件。')
        digest = resource.get('sha256')
        if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise ValueError('迁移资源身份无效。')
        expected = self.root / 'zotero-archive' / receipt_id / (digest + '.bin')
        if resource['archivedPath'] != str(expected.relative_to(self.root)):
            raise ValueError('迁移资源路径无效。')
        if any(unsafe_link(path) for path in (expected, *expected.parents)):
            raise ValueError('迁移资源路径不安全。')
        if not stat.S_ISREG(expected.lstat().st_mode):
            raise ValueError('迁移资源必须是普通文件。')
        fd = os.open(expected, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                     | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
        try:
            info = os.fstat(fd)
            size = resource.get('sizeBytes')
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or type(size) is not int or not 0 <= size <= ZoteroMigrationPolicy.max_pdf_bytes
                    or info.st_size != size):
                raise ValueError('迁移资源尺寸或文件身份无效。')
            chunks, hasher, total = [], hashlib.sha256(), 0
            while True:
                chunk = os.read(fd, 1024*1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > size:
                    raise ValueError('迁移资源已改变。')
                chunks.append(chunk)
                hasher.update(chunk)
            after = os.fstat(fd)
            current = expected.stat(follow_symlinks=False)
            identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
            if identity(info) != identity(after) or identity(after) != identity(current) or hasher.hexdigest() != digest:
                raise ValueError('迁移资源校验失败。')
        finally:
            os.close(fd)
        # Export bytes only; the archive is never executed or rendered as active HTML.
        # 只导出原始字节，不执行档案，不将 HTML 渲染为活动页面。
        self.write_export(destination, b''.join(chunks))
        self.audit('artifact_exported')
        return dict(sourceId=resource['sourceId'], resourceId=resource_identity(resource), sizeBytes=size, sha256=digest)

    def list_zotero_migrations(self):
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT receipt FROM desktop_zotero_migrations ORDER BY created_at DESC')]

    def read_zotero_migration(self, receipt_id):
        with self.connection() as db:
            row = db.execute('SELECT receipt,archive FROM desktop_zotero_migrations WHERE id=?',(receipt_id,)).fetchone()
            if not row:
                raise ValueError('迁移收据不存在。')
            return dict(receipt=json.loads(row[0]), archive=json.loads(row[1]))
