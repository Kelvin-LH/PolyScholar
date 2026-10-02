# SPDX-License-Identifier: AGPL-3.0-only
"""有序作者及编者编辑器；旧姓名按原文保留，不猜测拆分。

Ordered creator editor; legacy names remain literal rather than guessed.
"""
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QComboBox, QPushButton, QDialogButtonBox, QHeaderView)


class CreatorsDialog(QDialog):
    def __init__(self, creators, parent=None):
        super().__init__(parent)
        self.setWindowTitle('作者与编者')
        self.resize(820,380)
        layout=QVBoxLayout(self)
        hint=QLabel('按引用顺序排列。个人填写姓与名，或保留完整姓名；机构仅填写完整名称。无需拆分已有姓名。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table=QTableWidget(0,5)
        self.table.setHorizontalHeaderLabels(['角色','身份','姓','名','完整姓名 / 机构名称'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        for creator in creators:
            self.add_creator(creator)
        actions=QHBoxLayout()
        for label,callback in [('添加',lambda:self.add_creator()),('删除',self.remove_creator),('上移',lambda:self.move_creator(-1)),('下移',lambda:self.move_creator(1))]:
            button=QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        layout.addLayout(actions)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def add_creator(self, creator=None):
        creator=creator or {'role':'author','type':'person'}
        row=self.table.rowCount()
        self.table.insertRow(row)
        for column,choices,key in [(0,[('作者','author'),('编者','editor')],'role'),(1,[('个人','person'),('机构','organization')],'type')]:
            combo=QComboBox()
            for label,value in choices:
                combo.addItem(label,value)
            combo.setCurrentIndex(max(0,combo.findData(creator.get(key))))
            self.table.setCellWidget(row,column,combo)
        for column,key in [(2,'family'),(3,'given'),(4,'literal')]:
            self.table.setItem(row,column,QTableWidgetItem(creator.get(key,'') or ''))
        self.table.setCurrentCell(row,4)

    def remove_creator(self):
        row=self.table.currentRow()
        if row>=0:
            self.table.removeRow(row)

    def creators(self):
        # 个人姓/名与完整姓名互斥、机构仅用完整名称，统一交给后端验证。
        # Backend validation enforces personal name modes and literal-only organizations.
        result=[]
        for row in range(self.table.rowCount()):
            creator={'role':self.table.cellWidget(row,0).currentData(),'type':self.table.cellWidget(row,1).currentData()}
            for column,key in [(2,'family'),(3,'given'),(4,'literal')]:
                creator[key]=self.table.item(row,column).text().strip()
            result.append(creator)
        return result

    def move_creator(self, offset):
        row=self.table.currentRow()
        target=row+offset
        if row<0 or target<0 or target>=self.table.rowCount():
            return
        # 重排整条身份记录，姓名、角色与机构类型始终一起移动。
        # Move complete creator records so names, roles and organization identity stay together.
        values=self.creators()
        values[row],values[target]=values[target],values[row]
        self.table.setRowCount(0)
        for value in values:
            self.add_creator(value)
        self.table.setCurrentCell(target,4)
