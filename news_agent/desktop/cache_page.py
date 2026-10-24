"""Paged cache administration; SQLite work never runs on the UI thread."""

from datetime import datetime

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..news_cache import CacheLibrary
from .widgets import Card, label, note


def date_text(value):
    return datetime.fromtimestamp(value).strftime('%Y-%m-%d %H:%M') if value else '—'


class CachePage(QWidget):
    def __init__(self, window, db_path):
        super().__init__()
        self.window = window
        self.library = CacheLibrary(db_path)
        self.snapshot = None
        self.page_number = 0
        self._all_selection = None
        self._updating = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 2, 32, 16)
        outer.setSpacing(13)
        policy = Card('缓存库与自动清理', '默认每 7 天于 03:00 清空全部新闻，可自定义或关闭自动清理。', 'clock')
        row = QHBoxLayout()
        row.addWidget(window.field('cache.auto_clear', '自动清理', 'boolean', True), 1)
        row.addWidget(window.field('cache.interval_days', '清理周期', 'integer', 7, low=1, high=36500, suffix=' 天'), 1)
        row.addWidget(window.field('cache.clear_time', '执行时间（本机时间）', 'time', '03:00'), 1)
        save = QPushButton('保存清理设置')
        save.clicked.connect(self.save_policy)
        row.addWidget(save)
        policy.layout.addLayout(row)
        self.schedule_label = label('首次使用时开始计时；后台或配置窗口运行时检查，到期离线会在下次启动补执行。', 'muted', True)
        policy.layout.addWidget(self.schedule_label)
        outer.addWidget(policy)
        outer.addWidget(note('缓存条数不设上限，实际容量取决于磁盘。重复链接、标题和长正文指纹会在 AI 处理前检查。删除会同时移除去重与待发记录，下次采集可能再次入库和推送；定时清空也遵循此规则。'))
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索新闻标题、来源或摘要，按 Enter 查询')
        self.search.returnPressed.connect(self.search_changed)
        search_row.addWidget(self.search, 1)
        self.refresh_button = self.action('刷新', self.refresh)
        search_row.addWidget(self.refresh_button)
        self.select_page_button = self.action('全选当前页', self.select_page)
        search_row.addWidget(self.select_page_button)
        self.select_all_button = self.action('全选全部结果', self.select_all_results)
        search_row.addWidget(self.select_all_button)
        search_row.addWidget(self.action('取消选择', self.clear_selection))
        outer.addLayout(search_row)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(['新闻标题', '来源', '综合分', '推送状态', '发布时间', '入库时间', '链接'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(41)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in [(1, 100), (2, 60), (3, 82), (4, 132), (5, 132), (6, 100)]:
            self.table.setColumnWidth(column, width)
        self.table.setMinimumHeight(200)
        self.table.itemSelectionChanged.connect(self.selection_changed)
        outer.addWidget(self.table, 1)
        controls = QHBoxLayout()
        self.selection_label = label('尚未加载', 'muted')
        controls.addWidget(self.selection_label, 1)
        self.previous_button = self.action('上一页', lambda: self.change_page(-1))
        self.next_button = self.action('下一页', lambda: self.change_page(1))
        controls.addWidget(self.previous_button)
        controls.addWidget(self.next_button)
        self.delete_button = self.action('删除所选', self.delete_selected)
        controls.addWidget(self.delete_button)
        self.clear_button = self.action('清空缓存库', self.clear_library)
        controls.addWidget(self.clear_button)
        outer.addLayout(controls)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText('选择新闻查看完整标题、链接、摘要、原始简介和 AI 解读。支持 Ctrl/Shift 多选。')
        self.details.setMaximumHeight(115)
        outer.addWidget(self.details)
        self.status = label('缓存管理只处理本地数据，不调用模型、不发送测试消息。', 'muted', True)
        outer.addWidget(self.status)
        self.timer = QTimer(self)
        self.timer.setInterval(60000)
        self.timer.timeout.connect(self.maintain)
        if window.monitor_services:
            self.timer.start()
        self.update_selection()

    @staticmethod
    def action(text, callback):
        control = QPushButton(text)
        control.setCursor(Qt.CursorShape.PointingHandCursor)
        control.clicked.connect(callback)
        return control

    def _submit(self, action, callback):
        if 'cache' in self.window.background_tasks or self.window._closing:
            return False
        def work():
            try:
                return {'action': action, 'data': callback()}
            except ValueError as exc:
                return {'action': action, 'error': str(exc)}
        if self.window.run_background('cache', work):
            self.set_busy(True)
            self.status.setText('正在读取缓存…' if action == 'page' else '正在处理缓存…')
            return True
        return False

    def set_busy(self, value):
        self.table.setEnabled(not value)
        for widget in (self.refresh_button, self.previous_button, self.next_button,
                       self.select_page_button, self.select_all_button, self.delete_button, self.clear_button):
            widget.setEnabled(not value)
        if not value:
            self.update_selection()

    def refresh(self):
        search = self.search.text().strip()
        page = self.page_number
        # Destructive maintenance always uses saved policy, never unsaved edits.
        return self._submit('page', lambda: self.library.snapshot(self.window.store.read(), search, page))

    def search_changed(self):
        self.page_number = 0
        self.refresh()

    def change_page(self, offset):
        self.page_number = max(0, self.page_number + offset)
        self.refresh()

    def maintain(self):
        if not self.window._closing:
            self._submit('maintenance', lambda: self.library.run('cache_maintenance', self.window.store.read()))

    def save_policy(self):
        if self.window.save():
            self.refresh()

    def finish(self, result):
        self.set_busy(False)
        if not isinstance(result, dict) or 'error' in result:
            self.status.setText(result.get('error', '缓存操作未完成，请稍后重试。') if isinstance(result, dict) else '缓存操作未完成。')
            return
        action, data = result['action'], result['data']
        if action == 'page':
            self.render(data)
        elif action == 'maintenance':
            if data['removed']:
                self.status.setText(f"定时清空完成，删除 {data['removed']} 条新闻。")
                if self.snapshot is not None:
                    self.refresh()
            self.render_schedule(data)
        elif action == 'delete':
            self.status.setText(f'已删除 {data} 条新闻。')
            self.refresh()

    def render_schedule(self, value):
        next_at = value['next_cleanup_at']
        self.schedule_label.setText(
            ('下次自动清空：' + date_text(next_at) if next_at else '自动清空已关闭：新闻保留到手动删除。')
            + ' · 最近重置：' + date_text(value['last_cleared_at']) + '（' + value['last_reason'] + '）')

    def render(self, data):
        selected = self.selected_keys()
        self.snapshot, self.page_number = data, data['page']
        self._all_selection = None
        self._updating = True
        self.table.setRowCount(len(data['rows']))
        self.table.clearSelection()
        for number, row in enumerate(data['rows']):
            state = ('已实时推送' if row['pushed_realtime'] else '已进日报' if row['in_daily']
                     else '已过滤' if not row['valuable'] or row['credibility'] == '不可信' else '未推送')
            values = [row['title'], row['source'] or '—', f"{row['score']*.6 + row['utility']*.4:.1f}",
                      state, row['published_at'] or '时间未知', date_text(row['created_at']), row['url']]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row['url_hash'])
                self.table.setItem(number, column, item)
            if row['url_hash'] in selected:
                for column in range(self.table.columnCount()):
                    self.table.item(number, column).setSelected(True)
        self._updating = False
        self.render_schedule(data['maintenance'])
        self.status.setText(f"已加载 {len(data['rows'])} 条 · 缓存共 {data['total']} 条 · 不设条数上限")
        self.selection_changed()

    def selected_keys(self):
        return [self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
                for index in self.table.selectionModel().selectedRows()]

    def selection_changed(self):
        if self._updating:
            return
        self._all_selection = None
        self.update_selection()
        selected = self.table.selectionModel().selectedRows()
        if selected and self.snapshot:
            row = self.snapshot['rows'][selected[0].row()]
            self.details.setPlainText(f"{row['title']}\n{row['url']}\n可信度：{row['credibility'] or '未标注'}"
                                      f"\n摘要：{row['summary']}\nAI 解读：{row['comment']}\n原始简介：{row['description']}")
        else:
            self.details.clear()

    def update_selection(self):
        count = self._all_selection['count'] if self._all_selection else len(self.selected_keys())
        data = self.snapshot or {'count': 0, 'total': 0, 'page_size': 100, 'page': 0}
        pages = max(1, (data['count'] + data['page_size'] - 1) // data['page_size'])
        self.selection_label.setText(f"第 {data['page']+1}/{pages} 页 · 匹配 {data['count']} 条 · 已选 {count} 条" + ('（含其他页）' if self._all_selection else ''))
        self.delete_button.setEnabled(count > 0)
        self.clear_button.setEnabled(data['total'] > 0)
        self.previous_button.setEnabled(data['page'] > 0)
        self.next_button.setEnabled(data['page'] + 1 < pages)
        self.select_all_button.setEnabled(data['count'] > 0)

    def select_page(self):
        self.table.selectAll()
        self._all_selection = None
        self.update_selection()

    def select_all_results(self):
        if not self.snapshot:
            return
        self.table.selectAll()
        self._all_selection = {name: self.snapshot[name] for name in ('search', 'cutoff', 'generation', 'count')}
        self.update_selection()

    def clear_selection(self):
        self._all_selection = None
        self.table.clearSelection()
        self.update_selection()

    def delete_selected(self):
        if not self.snapshot:
            return
        keys = self.selected_keys()
        selected = self._all_selection
        count = selected['count'] if selected else len(keys)
        if not count:
            return
        if QMessageBox.question(self, '删除缓存新闻', f'删除选中的 {count} 条新闻？\n去重与待发记录也会移除，下次采集可能再次入库和推送。',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        options = {'expected_generation': self.snapshot['generation']}
        if selected:
            options.update(all_matching=True, search=selected['search'], cutoff=selected['cutoff'])
        self._submit('delete', lambda: self.library.run('delete_cached', keys, **options))

    def clear_library(self):
        if not self.snapshot or not self.snapshot['total']:
            return
        if QMessageBox.question(self, '清空全部新闻缓存', f"清空全部 {self.snapshot['total']} 条新闻，包括其他页与未推送新闻？\n不会删除账号配置或日报调度完成记录；后续可能重新采集和推送。",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        generation = self.snapshot['generation']
        self._submit('delete', lambda: self.library.run('delete_cached', all_matching=True, expected_generation=generation))
