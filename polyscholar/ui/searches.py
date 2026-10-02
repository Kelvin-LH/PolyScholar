# SPDX-License-Identifier: AGPL-3.0-only
"""Native metadata-condition editor; PDF contents are outside this search scope."""
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QTableWidget, QLineEdit, QPushButton, QDialogButtonBox, QHeaderView)

FIELDS=[('标题','title'),('作者 / 编者','creator'),('DOI','doi'),('年份','year'),('条目类型','itemType'),('期刊 / 论文集','publicationTitle'),('出版者','publisher'),('ISBN','isbn'),('标签','tag'),('笔记','notes')]
OPERATORS=[('包含','contains'),('不包含','not_contains'),('等于','is'),('不等于','is_not'),('为空','is_empty'),('非空','is_not_empty')]
TYPES=[('期刊论文','article-journal'),('会议论文','paper-conference'),('图书','book'),('学位论文','thesis')]


class SearchDialog(QDialog):
    def __init__(self, query=None, parent=None):
        super().__init__(parent);self.setWindowTitle('高级元数据检索');self.resize(700,420)
        layout=QVBoxLayout(self);hint=QLabel('检索本地条目元数据。结果还会应用当前快速搜索、集合和标签筛选。');hint.setWordWrap(True);layout.addWidget(hint)
        self.match=QComboBox();self.match.addItem('满足所有条件','all');self.match.addItem('满足任一条件','any');layout.addWidget(self.match)
        self.table=QTableWidget(0,3);self.table.setHorizontalHeaderLabels(['字段','条件','值']);self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);layout.addWidget(self.table)
        controls=QHBoxLayout();self.add_button=QPushButton('添加条件');self.add_button.clicked.connect(lambda:self.add_condition());controls.addWidget(self.add_button)
        remove=QPushButton('删除选中条件');remove.clicked.connect(self.remove_condition);controls.addWidget(remove);layout.addLayout(controls)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        query=query or {'match':'all','conditions':[]};self.match.setCurrentIndex(max(0,self.match.findData(query['match'])))
        for condition in query['conditions']:self.add_condition(condition)

    def add_condition(self, condition=None):
        if self.table.rowCount()>=20:return
        condition=condition or {'field':'title','operator':'contains','value':''}
        row=self.table.rowCount();self.table.insertRow(row);field=QComboBox()
        for label,value in FIELDS:field.addItem(label,value)
        field.setCurrentIndex(max(0,field.findData(condition['field'])));self.table.setCellWidget(row,0,field)
        operator=QComboBox();self.table.setCellWidget(row,1,operator)
        field.currentIndexChanged.connect(lambda *_:self.configure_condition(field))
        operator.currentIndexChanged.connect(lambda *_:self.configure_value(field))
        self.configure_condition(field)
        operator.setCurrentIndex(max(0,operator.findData(condition['operator'])))
        value=self.table.cellWidget(row,2)
        if isinstance(value,QComboBox):value.setCurrentIndex(max(0,value.findData(condition.get('value',''))))
        else:value.setText(condition.get('value',''))
        self.table.setCurrentCell(row,0);self.add_button.setEnabled(self.table.rowCount()<20)

    def row_for(self,field):
        return next((row for row in range(self.table.rowCount()) if self.table.cellWidget(row,0) is field),-1)

    def configure_condition(self,field):
        row=self.row_for(field)
        if row<0:return
        operator=self.table.cellWidget(row,1);previous=operator.currentData();operator.blockSignals(True);operator.clear()
        choices=OPERATORS
        if field.currentData()=='year':choices=OPERATORS+[('早于','before'),('晚于','after')]
        elif field.currentData()=='itemType':choices=[item for item in OPERATORS if item[1] not in ('contains','not_contains')]
        for label,value in choices:operator.addItem(label,value)
        operator.setCurrentIndex(max(0,operator.findData(previous)));operator.blockSignals(False)
        old=self.table.cellWidget(row,2);text=old.text() if isinstance(old,QLineEdit) else ''
        if field.currentData()=='itemType':
            value=QComboBox()
            for label,kind in TYPES:value.addItem(label,kind)
        else:value=QLineEdit();value.setText(text);value.setPlaceholderText('YYYY' if field.currentData()=='year' else '检索值')
        self.table.setCellWidget(row,2,value);self.configure_value(field)

    def configure_value(self,field):
        row=self.row_for(field)
        if row>=0:
            value=self.table.cellWidget(row,2)
            if value:value.setEnabled(self.table.cellWidget(row,1).currentData() not in ('is_empty','is_not_empty'))

    def remove_condition(self):
        row=self.table.currentRow()
        if row>=0:self.table.removeRow(row)
        self.add_button.setEnabled(self.table.rowCount()<20)

    def query(self):
        conditions=[]
        for row in range(self.table.rowCount()):
            operator=self.table.cellWidget(row,1).currentData();value=self.table.cellWidget(row,2)
            conditions.append({'field':self.table.cellWidget(row,0).currentData(),'operator':operator,'value':'' if operator in ('is_empty','is_not_empty') else value.currentData() if isinstance(value,QComboBox) else value.text().strip()})
        return {'match':self.match.currentData(),'conditions':conditions}
