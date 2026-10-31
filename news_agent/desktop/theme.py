# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtGui import QPainter


PATHS = {
    'spark': '<path d="m12 2 2.5 7.5L22 12l-7.5 2.5L12 22l-2.5-7.5L2 12l7.5-2.5Z"/><path d="m20 2 .7 2.3L23 5l-2.3.7L20 8l-.7-2.3L17 5l2.3-.7Z"/>',
    'grid': '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    'send': '<path d="m22 2-7 20-4-9L2 9Z"/><path d="M22 2 11 13"/>',
    'cpu': '<rect x="6" y="6" width="12" height="12" rx="3"/><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4"/><rect x="10" y="10" width="4" height="4" rx="1"/>',
    'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    'rss': '<path d="M4 4a16 16 0 0 1 16 16M4 11a9 9 0 0 1 9 9"/><circle cx="5" cy="19" r="1"/>',
    'file': '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z"/><path d="M14 3v6h6M8 13h8m-8 4h6"/>',
    'arrow': '<path d="M5 12h14m-5-5 5 5-5 5"/>',
    'refresh': '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 6a8 8 0 0 1 13 3M5 15a8 8 0 0 0 13 3"/>',
    'check': '<path d="m5 12 4 4L19 6"/>',
    'qq': '<path d="M7 11V9a5 5 0 0 1 10 0v2l3 5-3-1v4H7v-4l-3 1Z"/><path d="M9 21h-3m9 0h3M9 11h6"/>',
    'mail': '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m3 7 9 6 9-6"/>',
    'folder': '<path d="M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
}


def make_icon(name, color='#687570', size=22):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{PATHS.get(name, PATHS["grid"])}</svg>'
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    return pixmap


STYLE = """
QWidget { color: #273d36; font-family: 'Microsoft YaHei UI'; font-size: 13px; }
QMainWindow, QWidget#shell { background: #f5f7f6; }
QFrame#sidebar { background: #fcfdfc; border-right: 1px solid #e3e9e5; }
QLabel { background: transparent; }
QLabel#brand { font-size: 22px; font-weight: 700; color: #183f33; }
QLabel#eyebrow { color: #88948e; font-size: 10px; letter-spacing: 2px; }
QLabel#sideCaption { color: #99a59e; font-size: 11px; }
QLabel#pageTitle { font-size: 28px; font-weight: 700; color: #1d372c; }
QLabel#subtitle { color: #839088; font-size: 12px; }
QLabel#cardTitle { font-size: 16px; font-weight: 700; }
QLabel#muted { color: #85928b; font-size: 12px; }
QLabel#fieldLabel { color: #455b50; font-size: 12px; font-weight: 600; }
QLabel#metric { font-size: 29px; font-weight: 700; color: #214e3d; }
QLabel#chip { color: #267a61; background: #eaf5ee; border-radius: 12px; padding: 5px 12px; font-size: 11px; }
QLabel#purpleChip { color: #7b70a2; background: #f0edf8; border-radius: 10px; padding: 4px 9px; font-size: 10px; }
QFrame#card { background: white; border: 1px solid #e3eae5; border-radius: 14px; }
QFrame#note { background: #edf5ef; border: 1px solid #dceade; border-radius: 10px; }
QFrame#footer { background: #fcfdfc; border-top: 1px solid #e3e9e5; }
QFrame#separator { background: #e9eeeb; max-height: 1px; }
QPushButton { border: 1px solid #dce5de; border-radius: 8px; background: white; padding: 9px 16px; color: #496353; font-weight: 600; }
QPushButton:hover { background: #f0f6f1; border-color: #a6c7b1; }
QPushButton:pressed { background: #e4eee7; }
QPushButton:disabled { color: #a6b0a9; background: #f1f4f2; border-color: #e7ece8; }
QPushButton#primary { color: white; background: #237b58; border-color: #237b58; }
QPushButton#primary:hover { background: #196746; }
QPushButton#primary:disabled { background: #c4d7ca; border-color: #c4d7ca; color: #fff; }
QPushButton#nav { text-align: left; border: none; background: transparent; padding: 13px 17px; color: #7f8d84; font-size: 13px; font-weight: 500; }
QPushButton#nav:hover { background: #f0f5f1; }
QPushButton#nav:checked { background: #e8f1e9; color: #246b49; font-weight: 700; }
QPushButton#link { border: none; background: transparent; color: #448064; padding: 6px 0; text-align: left; }
QPushButton#reveal { padding: 5px 9px; font-size: 11px; }
QPushButton#modelSegment { background: #edf2ee; color: #71877a; border-color: #e0e8e1; padding: 10px 26px; }
QPushButton#modelSegment:checked { color: #236a49; background: #dfeee2; border-color: #a9cbb1; }
QPushButton#modelSegment:hover { border-color: #8eb69c; }
QComboBox QAbstractItemView { background: white; color: #344d3e; selection-background-color: #deeee2; selection-color: #245737; border: 1px solid #dce6de; padding: 5px; }
QLineEdit, QSpinBox, QDoubleSpinBox, QTimeEdit, QComboBox, QPlainTextEdit {
 background: #fbfcfb; border: 1px solid #dfe7e0; border-radius: 8px; padding: 9px 11px; color: #344d3e; selection-background-color: #d1e9d8;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border: 1px solid #5ba479; background: white; }
QLineEdit:disabled { background: #f1f4f2; color: #9ca69f; }
QSpinBox::up-button, QDoubleSpinBox::up-button, QTimeEdit::up-button { width: 20px; border: none; border-left: 1px solid #e1e8e3; }
QSpinBox::down-button, QDoubleSpinBox::down-button, QTimeEdit::down-button { width: 20px; border: none; border-left: 1px solid #e1e8e3; }
QCheckBox { spacing: 9px; color: #627b6c; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #c7d5cb; border-radius: 5px; background: white; }
QCheckBox::indicator:checked { background: #267b56; border-color: #267b56; image: none; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: #d2ddd5; border-radius: 3px; min-height: 35px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QTableWidget { background: white; border: 1px solid #e1e8e2; border-radius: 8px; gridline-color: #eff3ef; selection-background-color: #e4f1e8; selection-color: #234b34; }
QTableWidget::item { padding: 9px; border-bottom: 1px solid #edf2ee; }
QHeaderView::section { background: #f4f7f4; color: #6e8375; border: none; padding: 10px; font-size: 11px; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: transparent; color: #8a978f; padding: 10px 20px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #26764f; border-bottom: 2px solid #26764f; }
QToolTip { background: #233e30; color: white; border: none; padding: 8px; }
QMessageBox { background: #f9fbf9; }
"""
