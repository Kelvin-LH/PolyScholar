# SPDX-License-Identifier: AGPL-3.0-only
"""有界本地资源计划；不解析 HTML、访问 URL 或猜测外部目录。

Bounded local resource planning without HTML execution, URL access or guessed directories.
"""
import os
from pathlib import Path
import re
import stat
import uuid


def resource_identity(resource):
    """一条源附件可有多个档案文件。 / One source attachment may own several archive files."""
    return resource.get('resourceId', resource['sourceId'])


def tree_manifest(root, reader, is_link, max_entries=20000):
    """枚举限定目录内的身份；不跟随链接或读取管道。

    Enumerate identities within a bounded directory without following links or opening pipes.
    """
    records, pending = {}, [(Path(root), 0)]
    while pending:
        directory, depth = pending.pop()
        reader.check()
        if depth > 32 or is_link(directory) or any(is_link(p) for p in directory.parents):
            raise ValueError('快照目录含链接或超过层级上限。')
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('快照目录不是普通目录。')
        relative = directory.relative_to(root).as_posix()
        records[relative] = ['directory', info.st_dev, info.st_ino, info.st_mtime_ns]
        with os.scandir(directory) as entries:
            for entry in entries:
                reader.check()
                if len(records) >= max_entries:
                    raise ValueError('快照资源数量超过上限，未截断文件。')
                path = Path(entry.path)
                value = path.lstat()
                name = path.relative_to(root).as_posix()
                kind = 'unsafe' if is_link(path) else ('directory' if stat.S_ISDIR(value.st_mode) else ('file' if stat.S_ISREG(value.st_mode) else 'unsafe'))
                records[name] = [kind, value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns]
                if kind == 'directory':
                    pending.append((path, depth + 1))
    return records


class ZoteroResourcePlanner:
    """共用来源只读 IO 与独立 PDF 校验；资源保留完整身份。

    Reuse source read-only IO and isolated PDF validation while retaining resource identities.
    """
    def __init__(self, reader, validator, cancelled, policy, is_link):
        self.reader, self.validator, self.cancelled = reader, validator, cancelled
        self.policy, self.is_link = policy, is_link
        self.resources, self.pdfs, self.archive_files, self.trees = [], [], [], []
        self.total_bytes = 0

    def _add(self, resource):
        if len(self.resources) >= self.policy.max_resources:
            raise ValueError('迁移资源数量超过上限，未截断文件。')
        self.resources.append(resource)

    def _copy(self, resource, source, native_pdf=False):
        self.reader.check()
        resource['sourcePath'] = str(source)
        if not source.exists() and not self.is_link(source):
            resource.update(status='missing', reason='本地资源缺失，未伪造文件。')
            return
        staged = self.reader.staging / (uuid.uuid4().hex + '.bin')
        try:
            identity = self.reader._read(source, staged, self.policy.max_pdf_bytes)
        except (OSError, ValueError):
            self.reader.check()
            staged.unlink(missing_ok=True)
            resource.update(status='unavailable', reason='资源无法安全复制或超过单文件上限；来源信息保留。')
            return
        self.total_bytes += identity[2]
        if self.total_bytes > self.policy.max_total_pdf_bytes:
            raise ValueError('迁移资源总量超过上限，未截断文件。')
        resource.update(status='archive-ready', reason='真实资源字节已复制到本地档案，未执行内容。',
                        filename=source.name, sha256=identity[-1], sizeBytes=identity[2])
        record = dict(resource=resource, staged=staged, source=source, identity=identity)
        if native_pdf:
            valid, error = self.validator.validate(staged, self.cancelled, self.reader.deadline)
            if valid:
                resource.update(status='pdf-ready', reason='真实 PDF 已在独立进程验证。')
                self.pdfs.append(record)
                return
            resource.update(validationCode=error, reason='PDF 校验超时或失败；原始字节已保留档案。')
        self.archive_files.append(record)

    def _storage_path(self, directory, item, raw):
        key = item.get('key', '')
        if not re.fullmatch(r'[A-Z0-9]{8}', key or '') or not raw.startswith('storage:'):
            return None
        name = raw.removeprefix('storage:')
        if name in ('', '.', '..') or '/' in name or re.match(r'^[A-Za-z]:|^\\\\', name) or (os.name == 'nt' and '\\' in name):
            return None
        return directory / 'storage' / key / name

    def _linked_path(self, raw, linked_directory):
        if linked_directory is None:
            return None
        if raw.startswith('attachments:'):
            relative = raw.removeprefix('attachments:').replace('\\', '/')
            parts = relative.split('/')
            if any(part in ('', '.', '..') or ':' in part for part in parts):
                return None
            return linked_directory.joinpath(*parts)
        source = Path(raw)
        if not source.is_absolute() or '..' in source.parts:
            return None
        # 外平台绝对路径不猜测重定位；只读取显式目录范围内的真实路径。
        # Never guess relocation of foreign absolute paths; read only within the selected directory.
        return source if source.is_relative_to(linked_directory) else None

    def _snapshot(self, resource, source):
        root = source.parent
        try:
            before = tree_manifest(root, self.reader, self.is_link)
        except (OSError, ValueError):
            self.reader.check()
            resource.update(ancillaryStatus='unavailable', ancillaryReason='快照伴随目录无法安全枚举；主文件状态见附件报告。')
            return
        self.trees.append(dict(rootPath=str(root), manifest=before))
        complete = True
        for relative, info in sorted(before.items()):
            if relative == '.' or info[0] == 'directory' or root / relative == source:
                continue
            child = dict(sourceId=resource['sourceId'], resourceId=resource['sourceId'] + ':snapshot:' + relative,
                         parentSourceId=resource['parentSourceId'], kind='snapshot-companion', relativePath=relative,
                         path=relative, status='unsafe', reason='快照含非普通文件，未跟随或复制。')
            if info[0] == 'file':
                self._copy(child, root / relative)
            complete &= child['status'] == 'archive-ready'
            self._add(child)
        after = tree_manifest(root, self.reader, self.is_link)
        if before != after:
            raise ValueError('快照目录已改变，请重新预览。')
        resource.update(ancillaryStatus='complete' if complete else 'partial',
                        ancillaryReason='安全伴随文件已逐一归档；HTML 未执行。' if complete else '部分伴随资源无法复制，详见逐项状态。')

    def plan(self, directory, graph, items, linked_directory=None):
        by_id = {row['sourceId']: row for row in items}
        source_items = {row['itemID']: row for row in graph['items']}
        for attachment in graph.get('itemAttachments', []):
            self.reader.check()
            identifier = str(attachment['itemID'])
            parent_id = str(attachment['parentItemID']) if attachment.get('parentItemID') is not None else None
            resource = dict(sourceId=identifier, resourceId=identifier, parentSourceId=parent_id,
                            path=attachment.get('path'), status='pending', reason='此资源模式尚未适配，来源信息保留。')
            raw = attachment.get('path') or ''
            mode = attachment.get('linkMode')
            source = None
            if mode in (0, 1, 4):
                source = self._storage_path(directory, source_items[attachment['itemID']], raw)
                if source is None:
                    resource.update(status='unsafe', reason='附件路径超出受管存储边界，未读取。')
            elif mode == 2:
                source = self._linked_path(raw, linked_directory)
                resource.update(status='pending-linked' if linked_directory is None else 'outside-linked-root',
                                reason='请选择链接附件所在目录后重新预览。' if linked_directory is None else '链接路径不在选定目录内或无法可靠定位，未读取。')
            elif mode == 3:
                resource.update(status='remote-url', reason='远程 URL 仅保留来源，未下载。')
            self._add(resource)
            if source is not None:
                parent = by_id.get(parent_id)
                native = attachment.get('contentType') == 'application/pdf' and parent and parent['status'] == 'native'
                self._copy(resource, source, native_pdf=bool(native))
                if attachment.get('contentType') in ('text/html', 'application/xhtml+xml'):
                    if mode in (0, 1) and resource['status'] == 'archive-ready':
                        self._snapshot(resource, source)
                    else:
                        resource.update(ancillaryStatus='pending', ancillaryReason='未扫描链接 HTML 周围无关文件；伴随资源仍待明确映射。')
        libraries = {r['libraryID']: r.get('type') for r in graph.get('libraries', [])}
        groups = {r['libraryID']: r.get('groupID') for r in graph.get('groups', [])}
        for annotation in graph.get('itemAnnotations', []):
            self.reader.check()
            item = source_items[annotation['itemID']]
            identifier = str(annotation['itemID'])
            resource = dict(sourceId=identifier, resourceId=identifier + ':annotation-cache',
                            parentSourceId=str(annotation['parentItemID']), path=None, kind='annotation-cache',
                            status='not-required', reason='此批注类型不要求缓存图片；原始文字与坐标已保留。')
            if annotation.get('type') in (3, 4):
                key, library_id = item.get('key'), item.get('libraryID')
                source = None
                if re.fullmatch(r'[A-Z0-9]{8}', key or ''):
                    if libraries.get(library_id) == 'user':
                        source = directory / 'cache/library' / (key + '.png')
                    elif libraries.get(library_id) == 'group' and type(groups.get(library_id)) is int and groups[library_id] > 0:
                        source = directory / 'cache/groups' / str(groups[library_id]) / (key + '.png')
                if source is not None:
                    resource['path'] = str(source.relative_to(directory))
                    self._copy(resource, source)
                    if resource['status'] == 'archive-ready':
                        resource['reason'] = '批注缓存字节已归档，未解码验证图片，也未转换为已验证证据。'
                else:
                    resource.update(status='pending-annotation-cache', reason='批注资料库类型或键无法可靠定位，缓存未读取。')
            self._add(resource)
        return self.resources, self.pdfs, self.archive_files, self.trees
