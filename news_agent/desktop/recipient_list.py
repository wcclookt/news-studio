# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

"""Editable recipient lists used by the desktop push-channel settings."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget, QPushButton,
)

from .widgets import label


class RecipientTable(QWidget):
    """A small table that keeps recipient IDs and user-editable notes."""

    changed = Signal()
    refreshRequested = Signal()

    def __init__(self, identifier_key='id', name_key='name', identifier_title='标识',
                 name_title='名称', hint=''):
        super().__init__()
        self.identifier_key = identifier_key
        self.name_key = name_key
        self._hint = hint
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(7)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([identifier_title, name_title, '备注'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(92)
        self.table.setMaximumHeight(220)
        self.table.setAccessibleName(identifier_title + '接收列表')
        self.table.itemChanged.connect(lambda *_: self.changed.emit())
        outer.addWidget(self.table)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        self.add_button = QPushButton('＋ 添加')
        self.remove_button = QPushButton('删除所选')
        self.refresh_button = QPushButton('刷新')
        self.add_button.clicked.connect(self.add_row)
        self.remove_button.clicked.connect(self.remove_selected)
        self.refresh_button.clicked.connect(self.refreshRequested.emit)
        actions.addWidget(self.add_button)
        actions.addWidget(self.remove_button)
        actions.addWidget(self.refresh_button)
        actions.addStretch()
        outer.addLayout(actions)
        if hint:
            outer.addWidget(label(hint, 'muted', True))

    def set_refresh_enabled(self, enabled):
        self.refresh_button.setEnabled(enabled)

    def _text(self, row, column):
        item = self.table.item(row, column)
        return item.text().strip() if item else ''

    def values(self):
        result = []
        for row in range(self.table.rowCount()):
            identifier = self._text(row, 0)
            result.append({
                self.identifier_key: identifier,
                self.name_key: self._text(row, 1),
                'note': self._text(row, 2),
            })
        return result

    def identifiers(self):
        return [item[self.identifier_key] for item in self.values()]

    def set_values(self, values):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for value in values if isinstance(values, list) else []:
            if isinstance(value, dict):
                identifier = value.get(self.identifier_key, value.get('id', ''))
                name = value.get(self.name_key, value.get('name', value.get('title', '')))
                note = value.get('note', '')
            else:
                identifier, name, note = value, '', ''
            identifier = str(identifier or '').strip()
            if not identifier:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            for column, text in enumerate((identifier, name, note)):
                self.table.setItem(row, column, QTableWidgetItem(str(text or '').strip()))
        self.table.blockSignals(False)

    def setPlainText(self, text):
        """Import legacy recipient text without using it as the visible editor."""
        import re
        values = []
        for value in re.split(r'[,，;；\s]+', str(text or '')):
            value = value.strip()
            if value:
                values.append({self.identifier_key: value})
        self.set_values(values)
        self.changed.emit()

    def toPlainText(self):
        return '\n'.join(self.identifiers())

    def add_row(self):
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column in range(3):
            self.table.setItem(row, column, QTableWidgetItem(''))
        self.table.setCurrentCell(row, 0)
        self.table.editItem(self.table.item(row, 0))
        self.changed.emit()

    def remove_selected(self):
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()}, reverse=True)
        for row in rows:
            self.table.removeRow(row)
        if rows:
            self.changed.emit()
