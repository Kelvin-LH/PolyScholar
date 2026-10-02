# SPDX-License-Identifier: AGPL-3.0-only
"""合成库的原生迁移回归，不冒充 Zotero 应用或安装包验收。

Native migration regression with a synthetic library, not real Zotero/package acceptance.
"""
from pathlib import Path
import sys
import json
import traceback
import sqlite3
import tempfile
import time
from contextlib import closing
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from polyscholar.app import Window, STYLE
from polyscholar.service import LocalService
from polyscholar.zotero_migration import ZoteroMigrationPolicy
from test_zotero_migration import fixture


def wait(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate(), 'Zotero migration UI did not settle'


def idle(window, dialog):
    # 后端预算30秒；观察期限覆盖进程回收及Qt排队，不能先于业务预算失败。
    # Observe the full domain budget plus process cleanup and queued Qt completion.
    try:
        wait(lambda: window.io_worker is None and not dialog._busy and dialog._pending_action is None,
             timeout=ZoteroMigrationPolicy.timeout_seconds + 10)
    except AssertionError:
        def worker_flag(worker, method):
            if worker is None:
                return None
            try:
                return getattr(worker, method)()
            except RuntimeError:
                return 'deleted'
        state=dict(ioPresent=window.io_worker is not None, ioRunning=worker_flag(window.io_worker, 'isRunning'),
                   ioFinished=worker_flag(window.io_worker, 'isFinished'),
                   dialogBusy=dialog._busy, watchedPresent=dialog._watched_worker is not None,
                   watchedRunning=worker_flag(dialog._watched_worker, 'isRunning'),
                   watchedIsCurrent=dialog._watched_worker is window.io_worker,
                   pending=dialog._pending_action is not None, imported=dialog._imported,
                   dialogClosed=dialog._closed, windowClosing=window._closing,
                   validatorWorkers=len(window.service._zotero_importer._pdf_validator._workers))
        print('Native migration timeout state:', json.dumps(state), flush=True)
        # 只输出合成检查的调用位置；不包含变量、环境或源文件内容。
        # Emit synthetic-check call locations only, without locals, environment or source contents.
        for frame in list(sys._current_frames().values())[:16]:
            positions=[(Path(item.filename).name, item.lineno, item.name)
                       for item in traceback.extract_stack(frame, limit=16)]
            print('Native migration timeout stack:', json.dumps(positions), flush=True)
        raise


def main():
    callback_errors = []
    original_hook = sys.excepthook
    def record_callback_error(kind, value, traceback):
        callback_errors.append(value)
        original_hook(kind, value, traceback)
    sys.excepthook = record_callback_error
    app = QApplication([])
    app.setStyleSheet(STYLE)
    unexpected_warnings=[]
    def reject_unexpected_warnings():
        # 无人值守检查必须暴露失败，不让模态警告永久占住测试事件循环。
        # Surface failures in unattended checks instead of blocking forever in a modal warning.
        for widget in app.topLevelWidgets():
            if isinstance(widget, QMessageBox) and widget.isVisible():
                unexpected_warnings.append(widget.text())
                widget.reject()
    warning_timer=QTimer(app)
    warning_timer.timeout.connect(reject_unexpected_warnings)
    warning_timer.start(25)
    with tempfile.TemporaryDirectory(prefix='polyscholar-zotero-ui-') as directory:
        root = Path(directory).resolve()
        source = root / 'synthetic-zotero'
        fixture(source)
        with closing(sqlite3.connect(source / 'zotero.sqlite')) as db:
            db.executescript("INSERT INTO items VALUES(8,2,'HHHHHHHH',1); INSERT INTO itemAttachments VALUES(8,5,0,'text/html','storage:note.html'); INSERT INTO items VALUES(9,2,'IIIIIIII',1); INSERT INTO itemAttachments VALUES(9,1,2,'application/pdf','attachments:linked.pdf');")
        html = b'<html><script>do not run</script><body>Synthetic archive</body></html>'
        html_directory = source / 'storage' / 'HHHHHHHH'
        html_directory.mkdir()
        (html_directory / 'note.html').write_bytes(html)
        (html_directory / 'picture.png').write_bytes(b'synthetic image companion bytes')
        linked = root / 'explicit-linked'
        linked.mkdir()
        (linked / 'linked.pdf').write_bytes((source / 'storage/CCCCCCCC/paper.pdf').read_bytes())
        service = LocalService(root / 'local')
        window = Window(service)
        try:
            window.resize(1024, 720)
            window.show()
            QTest.mouseClick(window.import_export_button, Qt.MouseButton.LeftButton)
            center = window.import_export_dialog
            assert [button.text() for button in center.actions] == ['PDF 文件', 'BibTeX / RIS / CSL-JSON', 'Zotero 资料目录', '引文与书目', '译文 PDF', '迁移档案文件']
            QTest.mouseClick(center.actions[2], Qt.MouseButton.LeftButton)
            assert center._closed
            dialog = window.zotero_migration_dialog
            dialog.path.setText(str(source))
            dialog.linked_path.setText(str(linked))
            wait(lambda: dialog.preview_button.isEnabled())
            QTest.mouseClick(dialog.preview_button, Qt.MouseButton.LeftButton)
            idle(window, dialog)
            assert dialog._preview is not None, dialog.status.text()
            assert not dialog._selected and not dialog.import_button.isEnabled()
            assert next(r for r in dialog._preview['resources'] if r['sourceId']=='9')['status'] == 'pdf-ready'
            for row in range(dialog.records.count()):
                item = dialog.records.item(row)
                record = item.data(Qt.ItemDataRole.UserRole)
                assert bool(item.flags() & Qt.ItemFlag.ItemIsUserCheckable) == record['selectable']
            QTest.mouseClick(dialog.select_all_button, Qt.MouseButton.LeftButton)
            assert dialog._selected == {'1', '2', '5'}
            dialog.linked_path.setText(str(root / 'other-linked'))
            assert not dialog.import_button.isEnabled()
            dialog.linked_path.setText(str(linked))
            wait(lambda: dialog.import_button.isEnabled())
            dialog.path.setText(str(source / 'wrong'))
            assert not dialog.import_button.isEnabled()
            dialog.path.setText(str(source))
            wait(lambda: dialog.import_button.isEnabled())
            QTest.mouseClick(dialog.import_button, Qt.MouseButton.LeftButton)
            idle(window, dialog)
            assert dialog._imported, dialog.status.text()
            assert not dialog.import_button.isEnabled()
            receipt = service.list_zotero_migrations()[0]
            assert receipt['counts']['native'] == 2 and receipt['counts']['pdfs'] == 3
            assert any('itemNotes' in key for key in dialog._archive)
            key = next(key for key in dialog._archive if 'itemNotes' in key)
            dialog.archive_sections.setCurrentIndex(dialog.archive_sections.findData(key))
            assert '<script>' in dialog.archive_text.toPlainText()
            assert '<img src=' in dialog.archive_text.toPlainText()
            # 档案笔记是普通文字，不加载 HTML/外链。 / Source notes remain plain text, with no HTML or external loading.
            assert '&lt;script&gt;' in dialog.archive_text.toHtml()
            assert dialog.archive_resources.count() == 3
            dialog.archive_resources.setCurrentIndex(1)
            destination = root / 'exported-note.html'
            wait(lambda: dialog.export_resource_button.isEnabled())
            with patch('polyscholar.ui.zotero_migration.QFileDialog.getSaveFileName', return_value=(str(destination), '')):
                QTest.mouseClick(dialog.export_resource_button, Qt.MouseButton.LeftButton)
                idle(window, dialog)
            assert destination.read_bytes() == html
            dialog.archive_resources.setCurrentIndex(2)
            companion = root / 'exported-picture.png'
            wait(lambda: dialog.export_resource_button.isEnabled())
            with patch('polyscholar.ui.zotero_migration.QFileDialog.getSaveFileName', return_value=(str(companion), '')):
                QTest.mouseClick(dialog.export_resource_button, Qt.MouseButton.LeftButton)
                idle(window, dialog)
            assert companion.read_bytes() == (html_directory / 'picture.png').read_bytes()
            dialog.close()
            window.open_zotero_migration()
            reopened = window.zotero_migration_dialog
            wait(lambda: reopened.refresh_receipts_button.isEnabled())
            QTest.mouseClick(reopened.refresh_receipts_button, Qt.MouseButton.LeftButton)
            idle(window, reopened)
            assert reopened.receipts.count() == 2
            reopened.receipts.setCurrentIndex(1)
            idle(window, reopened)
            assert any('itemNotes' in key for key in reopened._archive)
            # 一万条预览分页状态与跨页勾选保持；只测试展示，不声称引擎负载验收。
            # Verify paging and retained selection without claiming engine load acceptance.
            reopened._preview = dict(items=[dict(sourceId=str(index), itemType='webpage', status='archive', title='Synthetic', selectable=True) for index in range(205)])
            reopened._preview_valid = True
            reopened._preview_source = reopened.source_paths()
            reopened.show_page()
            assert reopened.records.count() == 100
            reopened.records.item(0).setCheckState(Qt.CheckState.Checked)
            reopened.change_page(1)
            assert reopened.records.count() == 100
            reopened.change_page(1)
            assert reopened.records.count() == 5
            reopened.change_page(-2)
            assert reopened.records.item(0).checkState() == Qt.CheckState.Checked
            reopened.display_archive({'hugeNote': 'x' * 140000})
            assert len(reopened._archive) == 3
            assert all(len(value) <= 65536 for value in reopened._archive.values())
            reopened.close()
            window.open_import_export()
            QTest.mouseClick(window.import_export_dialog.actions[3], Qt.MouseButton.LeftButton)
            assert window.nav.currentRow() == 4
            window.open_import_export()
            QTest.mouseClick(window.import_export_dialog.actions[4], Qt.MouseButton.LeftButton)
            assert window.nav.currentRow() == 2
            window.nav.setCurrentRow(0)
            if '--capture' in sys.argv:
                dialog = window.zotero_migration_dialog
                dialog.close()
                window.open_zotero_migration()
                dialog = window.zotero_migration_dialog
                dialog.path.setText(str(source))
                dialog.linked_path.setText(str(linked))
                dialog.load()
                idle(window, dialog)
                dialog.resize(1000, 720)
                app.processEvents()
                output = Path(__file__).resolve().parents[1] / 'design/screenshots/zotero-migration-python.png'
                assert dialog.grab().save(str(output))
                print('Saved native synthetic migration preview:', output)
                dialog.close()
                window.open_import_export()
                app.processEvents()
                center_output = output.parent / 'import-export-python.png'
                assert window.import_export_dialog.grab().save(str(center_output))
        finally:
            window.close()
            wait(lambda: window.io_worker is None)
            app.processEvents()
            service.close()
    assert not callback_errors, f'Qt callbacks failed: {callback_errors}'
    assert not unexpected_warnings, f'Unexpected native warning: {unexpected_warnings}'
    sys.excepthook = original_hook
    print('Synthetic Zotero migration native regression passed')


if __name__ == '__main__':
    main()
