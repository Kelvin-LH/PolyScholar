# SPDX-License-Identifier: AGPL-3.0-only
"""Native typed bibliographic editor with generated local PDF and no network."""
from pathlib import Path
import json
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pymupdf
from PySide6.QtWidgets import QApplication, QMessageBox, QDialog, QFileDialog
from polyscholar.app import Window
from polyscholar.service import LocalService
from polyscholar.ui.creators import CreatorsDialog


def main():
    app=QApplication([])
    with tempfile.TemporaryDirectory(prefix='polyscholar-metadata-ui-') as directory:
        root=Path(directory);source=root/'sample.pdf'
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((60,80),'Local typed bibliographic metadata.');pdf.save(source)
        service=LocalService(data_dir=root/'data');document=service.import_pdf(source);window=Window(service)
        try:
            window.show();app.processEvents();window.document_tree.setCurrentItem(window.document_tree.topLevelItem(0))
            creators=[{'role':'author','type':'person','family':'Chen','given':'Li','literal':''},
                {'role':'author','type':'organization','family':'','given':'','literal':'Research Collective'},
                {'role':'editor','type':'person','family':'Smith','given':'Sam','literal':''},
                {'role':'author','type':'person','family':'','given':'','literal':'Unparsed Legacy Name'}]
            dialog=CreatorsDialog(creators,window)
            dialog.table.setCurrentCell(3,4);dialog.move_creator(-1)
            expected=[creators[0],creators[1],creators[3],creators[2]]
            assert dialog.creators()==expected
            dialog.add_creator({'role':'author','type':'person','literal':'Temporary'});dialog.remove_creator();assert dialog.creators()==expected
            with patch('polyscholar.ui.library.CreatorsDialog',return_value=dialog),patch.object(dialog,'exec',return_value=QDialog.DialogCode.Accepted):window.edit_creators()
            window.fields['title'].setText('Typed native editor')
            window.fields['date'].setText('2024-02-29')
            window.fields['publicationTitle'].setText('Local Journal');window.fields['volume'].setText('12');window.fields['issue'].setText('3');window.fields['pages'].setText('41-55')
            window.save_doc();app.processEvents()
            stored=service.list_documents()[0];assert stored['creators']==expected and stored['date']=='2024-02-29'
            # Ordinary title edits must retain creator identities, roles and order.
            window.fields['title'].setText('Typed preserved creators');window.save_doc()
            assert service.list_documents()[0]['creators']==expected
            for kind,field,value in [('paper-conference','eventTitle','Local Conference'),('book','isbn','9780306406157'),('thesis','institution','Local University'),('article-journal','publicationTitle','Local Journal')]:
                window.item_type.setCurrentIndex(window.item_type.findData(kind));window.fields[field].setText(value)
                window.save_doc();stored=service.list_documents()[0]
                assert stored['itemType']==kind and stored[field]==value
                assert stored['creators']==expected and stored['issue']=='3' and stored['date']=='2024-02-29'
                window.citation_items.item(0).setSelected(True)
                for name in ('CSL-JSON','BibTeX','RIS'):
                    window.citation_format.setCurrentText(name);preview=window.citation_preview.toPlainText()
                    assert 'Typed preserved creators' in preview and 'Research Collective' in preview
                    target=root/(kind+'-'+window.citation_kind()+'.txt')
                    with patch.object(QFileDialog,'getSaveFileName',return_value=(str(target),'')):window.export_citations()
                    assert target.read_text(encoding='utf-8')==preview
            assert '保留' in window.retained_fields_info.text()
            window.fields['date'].setText('2024-02-30')
            with patch.object(QMessageBox,'warning') as warning:
                window.save_doc();assert warning.called
            assert service.list_documents()[0]['date']=='2024-02-29' and window.fields['date'].text()=='2024-02-30'
            window.fields['date'].setText('2024-02-29')
            window._metadata_creators=[{'role':'author','type':'organization','family':'Invalid','given':'','literal':'Institution'}]
            with patch.object(QMessageBox,'warning') as warning:
                window.save_doc();assert warning.called
            assert service.list_documents()[0]['creators']==expected
            # arXiv preprints are a first-class type: year derives from date and
            # journal-only fields are hidden without losing retained values.
            window._metadata_creators=list(expected)
            window.item_type.setCurrentIndex(window.item_type.findData('arxiv-preprint'))
            window.save_doc();stored=service.list_documents()[0]
            assert stored['itemType']=='arxiv-preprint' and stored['year']=='2024'
        finally:window.close();service.close()
    print('Native typed metadata: four item types, creator order and identities, retained fields, citation preview and validation passed')


if __name__=='__main__':main()
