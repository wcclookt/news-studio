"""The News Studio configuration window. Opening it never starts a news task."""

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import re

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTime, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QBoxLayout, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QStackedWidget, QTableWidget,
    QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget,
)

from ..config_store import ConfigStore, get_value, set_value, validate_config
from .. import __author__, PROJECT_ATTRIBUTION
from ..config_io import atomic_update_json
from ..model_catalog import ADAPTER_CHOICES, PROVIDER_PRESETS, PROVIDERS_BY_ID, provider_requires_api_key, resolve_adapter
from .services import ServiceManager
from .qq_test import TEST_MESSAGE, send_test_message
from .cache_page import CachePage
from .theme import make_icon
from .widgets import Card, Hero, Toggle, label, note


PAGE_INFO = [
    ('总览', '每一条好消息，都从这里开始。', 'grid'),
    ('推送渠道', '选择消息抵达你的方式，按需开启即可。', 'send'),
    ('AI 模型', '为新闻处理与 QQ 聊天安排合适的模型。', 'cpu'),
    ('时间与筛选', '调整推送节奏，让信息恰到好处。', 'clock'),
    ('新闻来源', '保持好奇，让你关心的领域持续更新。', 'rss'),
    ('运行日志', '查看后台新闻服务最近记录的运行情况。', 'file'),
    ('服务与网络', '管理后台运行，确认每一段连接的真实状态。', 'cpu'),
    ('新闻缓存库', '查看已处理新闻，管理去重记录与自动清理。', 'file'),
]


def button(text, callback=None, primary=False, icon=None):
    control = QPushButton(text)
    control.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        control.setObjectName('primary')
    if icon:
        control.setIcon(QIcon(make_icon(icon, '#ffffff' if primary else '#63846c', 17)))
    if callback:
        control.clicked.connect(callback)
    return control


class ProbeSignals(QObject):
    done = Signal(bool, str)


class TaskSignals(QObject):
    done = Signal(str, object)


class BackgroundTask(QRunnable):
    """Keep process inspection, service control and HTTP probes off the GUI thread."""

    def __init__(self, name, callback):
        super().__init__()
        self.name = name
        self.callback = callback
        self.signals = TaskSignals()

    def run(self):
        try:
            result = self.callback()
        except Exception:
            result = {'error': '操作未完成，请检查路径、服务权限和网络后重试。'}
        self.signals.done.emit(self.name, result)


class ConnectionProbe(QRunnable):
    def __init__(self, base, token):
        super().__init__()
        self.base = base
        self.token = token
        self.signals = ProbeSignals()

    def run(self):
        import requests
        try:
            headers = {'Authorization': f'Bearer {self.token}'} if self.token else {}
            with requests.Session() as session:
                session.trust_env = False
                with session.post(
                    self.base.rstrip('/') + '/get_login_info', json={},
                    headers=headers, timeout=(2, 4), allow_redirects=False,
                ) as response:
                    response.raise_for_status()
                    if response.status_code != 200:
                        self.signals.done.emit(False, 'NapCat 接口返回异常 HTTP 状态，请检查地址。')
                        return
                    data = response.json()
            if not isinstance(data, dict) or data.get('status') != 'ok' or not isinstance(data.get('data'), dict):
                self.signals.done.emit(False, 'NapCat 返回异常，请检查接口鉴权和登录状态。')
            elif not data['data'].get('user_id'):
                self.signals.done.emit(False, '接口可访问，但 QQ 尚未登录。')
            else:
                self.signals.done.emit(True, 'QQ 已登录，NapCat HTTP 连接正常。')
        except requests.Timeout:
            self.signals.done.emit(False, '连接超时，请确认 NapCat 已启动且端口正确。')
        except Exception:
            # Never expose a token, a full response, or request headers in UI errors.
            self.signals.done.emit(False, '无法连接，请检查 HTTP 地址、NapCat 服务和鉴权 Token。')


class ConfigWindow(QMainWindow):
    def __init__(self, config_path, monitor_services=True, cache_path=None):
        super().__init__()
        self.store = ConfigStore(config_path)
        self.initial_error = ''
        try:
            self.store.ensure_initial()
        except (OSError, ValueError):
            self.initial_error = '旧配置快照暂时无法移除，请检查配置目录写入权限。'
        self.fields = {}
        self.baseline = {}
        self.channel_toggles = {}
        self.channel_forms = {}
        self.loading = True
        self.probe = None
        self.background_tasks = {}
        self._closing = False
        self.monitor_services = monitor_services
        self._subscription_stamp = None
        self._napcat_qr_epoch = 0
        self._napcat_qr_version = None
        self._napcat_qr_signature = None
        self._napcat_qr_status = {'state': 'unknown'}
        self._napcat_qq_status = {}
        self.service_manager = ServiceManager(Path(__file__).resolve().parents[2], self.store.path)
        self.setWindowTitle('星闻 · News Studio — 配置中心')
        self.resize(1240, 850)
        self.setMinimumSize(1030, 720)
        self._build_shell()
        self._build_overview()
        self._build_channels()
        self._build_models()
        self._build_schedule()
        self._build_sources()
        self._build_logs()
        self._build_services()
        self.cache_page = CachePage(self, cache_path or Path(__file__).resolve().parents[2] / 'data' / 'news.db')
        cache_area = QScrollArea()
        cache_area.setWidgetResizable(True)
        cache_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        cache_area.setWidget(self.cache_page)
        self.stack.addWidget(cache_area)
        self.populate()
        self.select_page(0)
        QShortcut(QKeySequence.StandardKey.Save, self, activated=self.save)
        self.log_timer = QTimer(self)
        self.log_timer.setInterval(3000)
        self.log_timer.timeout.connect(self.refresh_logs)
        self.log_timer.start()
        self.subscription_timer = QTimer(self)
        self.subscription_timer.setInterval(2000)
        self.subscription_timer.timeout.connect(self.refresh_subscribers)
        self.subscription_timer.start()
        self.service_timer = QTimer(self)
        self.service_timer.setInterval(8000)
        self.service_timer.timeout.connect(self.refresh_services)
        self.napcat_qr_timer = QTimer(self)
        self.napcat_qr_timer.setInterval(2000)
        self.napcat_qr_timer.timeout.connect(self.refresh_napcat_qr)
        if monitor_services:
            self.service_timer.start()
            self.napcat_qr_timer.start()
            QTimer.singleShot(350, self.refresh_services)

    def _build_shell(self):
        shell = QWidget()
        shell.setObjectName('shell')
        outer = QHBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(218)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 29, 18, 23)
        side.setSpacing(7)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(make_icon('spark', '#2b7752', 31))
        brand.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(1)
        names.addWidget(label('星闻', 'brand'))
        names.addWidget(label('NEWS STUDIO', 'eyebrow'))
        brand.addLayout(names)
        brand.addStretch()
        side.addLayout(brand)
        side.addSpacing(35)
        side.addWidget(label('  工作空间', 'sideCaption'))
        side.addSpacing(5)
        self.nav_buttons = []
        for index, (title, _, icon) in enumerate(PAGE_INFO):
            control = button('  ' + title, lambda checked=False, i=index: self.select_page(i))
            control.setObjectName('nav')
            control.setCheckable(True)
            control.setIcon(QIcon(make_icon(icon, '#7b9584', 19)))
            side.addWidget(control)
            self.nav_buttons.append(control)
        side.addStretch()
        sidebar_note = note('把时间留给思考，\n把消息交给星闻。')
        side.addWidget(sidebar_note)
        side.addSpacing(10)
        self.developer_credit = label('西新个人作品 · AI 辅助', 'sideCaption', True)
        self.developer_credit.setAccessibleName('开发者署名')
        self.developer_credit.setToolTip(PROJECT_ATTRIBUTION)
        side.addWidget(self.developer_credit)
        self.about_button = button('关于星闻', self.show_about, icon='spark')
        self.about_button.setObjectName('link')
        side.addWidget(self.about_button)
        side.addWidget(label('  本地配置中心  /  PySide6', 'sideCaption'))
        outer.addWidget(sidebar)
        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        header = QHBoxLayout()
        header.setContentsMargins(32, 20, 32, 0)
        header.addWidget(label('工作空间   /   新闻助手', 'muted'))
        header.addStretch()
        self.header_status = label('本地配置', 'chip')
        header.addWidget(self.header_status)
        main.addLayout(header)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(32, 22, 32, 19)
        titles = QVBoxLayout()
        titles.setSpacing(6)
        self.page_title = label('', 'pageTitle')
        self.page_subtitle = label('', 'subtitle')
        titles.addWidget(self.page_title)
        titles.addWidget(self.page_subtitle)
        title_row.addLayout(titles, 1)
        self.date_label = label(datetime.now().strftime('%Y.%m.%d'), 'muted')
        title_row.addWidget(self.date_label, alignment=Qt.AlignmentFlag.AlignTop)
        main.addLayout(title_row)
        self.stack = QStackedWidget()
        main.addWidget(self.stack, 1)
        footer = QFrame()
        footer.setObjectName('footer')
        row = QHBoxLayout(footer)
        row.setContentsMargins(32, 14, 32, 14)
        self.save_status = label('所有更改已保存', 'muted')
        row.addWidget(self.save_status, 1)
        self.restore_button = button('恢复默认…', self.restore_defaults, icon='refresh')
        self.restore_button.setToolTip('恢复不含历史密钥、账号或接收人的全新配置。')
        row.addWidget(self.restore_button)
        row.addWidget(button('重新载入', self.reload, icon='refresh'))
        self.save_button = button('保存配置', self.save, primary=True, icon='check')
        row.addWidget(self.save_button)
        main.addWidget(footer)
        outer.addLayout(main, 1)
        self.setCentralWidget(shell)

    def show_about(self):
        QMessageBox.information(
            self, '关于星闻 · News Studio',
            f'开发者：{__author__}\n\n{PROJECT_ATTRIBUTION}\n\n'
            '本项目的开发与维护由西新主导，AI 作为辅助工具参与。\n\n'
            'NapCat、Steam++、PySide6 等第三方组件的原作者署名和许可信息保持不变。',
        )

    def page(self):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(32, 2, 32, 26)
        layout.setSpacing(18)
        area.setWidget(content)
        self.stack.addWidget(area)
        return layout

    def select_page(self, index):
        self.stack.setCurrentIndex(index)
        self.page_title.setText(PAGE_INFO[index][0])
        self.page_subtitle.setText(PAGE_INFO[index][1])
        for i, control in enumerate(self.nav_buttons):
            control.setChecked(i == index)
        if index == 5:
            self.refresh_logs()
        elif index == 6 and self.monitor_services:
            self.refresh_services()
        elif index == 7:
            self.cache_page.refresh()

    def field(self, path, title, kind='text', default='', hint='', low=0, high=9999, suffix=''):
        container = QWidget()
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(7)
        column.addWidget(label(title, 'fieldLabel'))
        comment_path = path.rsplit('.', 1)
        description = get_value(self.store.data, comment_path[0] + '._comment_' + comment_path[1], '') if len(comment_path) > 1 else ''
        if kind == 'secret':
            row = QHBoxLayout()
            row.setSpacing(7)
            editor = QLineEdit()
            editor.setEchoMode(QLineEdit.EchoMode.Password)
            reveal = button('显示')
            reveal.setObjectName('reveal')
            reveal.setCheckable(True)
            editor.reveal_button = reveal
            def show_secret(checked):
                editor.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)
                reveal.setText('隐藏' if checked else '显示')
            reveal.toggled.connect(show_secret)
            row.addWidget(editor, 1)
            row.addWidget(reveal)
            column.addLayout(row)
        elif kind == 'boolean':
            editor = QCheckBox('启用自动定时清空' if path == 'cache.auto_clear' else '需要 API Key')
            column.addWidget(editor)
        elif kind == 'adapter':
            editor = QComboBox()
            for adapter_label, adapter_value in ADAPTER_CHOICES:
                editor.addItem(adapter_label, adapter_value)
            column.addWidget(editor)
        elif kind == 'integer':
            editor = QSpinBox()
            editor.setRange(low, high)
            editor.setSuffix(suffix)
            column.addWidget(editor)
        elif kind == 'decimal':
            editor = QDoubleSpinBox()
            editor.setRange(low, high)
            editor.setSingleStep(.5)
            editor.setDecimals(1)
            column.addWidget(editor)
        elif kind == 'time':
            editor = QTimeEdit()
            editor.setDisplayFormat('HH:mm')
            column.addWidget(editor)
        elif kind in ('list', 'qq_list'):
            editor = QPlainTextEdit()
            editor.setFixedHeight(78)
            column.addWidget(editor)
        elif isinstance(kind, tuple):
            editor = QComboBox()
            editor.setEditable(True)
            editor.addItems(kind)
            column.addWidget(editor)
        else:
            editor = QLineEdit()
            column.addWidget(editor)
        editor.setObjectName(path)
        editor.setAccessibleName(title)
        if isinstance(editor, (QLineEdit, QPlainTextEdit)):
            editor.setPlaceholderText(hint or title)
        editor.setToolTip(description or hint)
        if hint:
            column.addWidget(label(hint, 'muted', True))
        self.fields[path] = (editor, kind, default)
        if isinstance(editor, QCheckBox):
            editor.toggled.connect(self.on_change)
        elif isinstance(editor, (QSpinBox, QDoubleSpinBox)):
            editor.valueChanged.connect(self.on_change)
        elif isinstance(editor, QTimeEdit):
            editor.timeChanged.connect(self.on_change)
        elif isinstance(editor, QComboBox):
            editor.currentTextChanged.connect(self.on_change)
        else:
            editor.textChanged.connect(self.on_change)
        return container

    def grid(self, card, items):
        grid = QGridLayout()
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(18)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        for index, item in enumerate(items):
            grid.addWidget(item, index // 2, index % 2)
        card.layout.addLayout(grid)

    def _build_overview(self):
        page = self.page()
        page.addWidget(Hero())
        metrics = QHBoxLayout()
        metrics.setSpacing(15)
        self.metrics = {}
        for key, title, caption, icon in [
            ('channels', '推送渠道', '按需启用，随时调整', 'send'),
            ('sources', '订阅来源', 'RSS 与专题采集', 'rss'),
            ('time', '每日日报', '遵循本机时间', 'clock'),
        ]:
            card = Card(title, icon=icon)
            card.layout.setContentsMargins(20, 18, 20, 18)
            value = label('', 'metric')
            self.metrics[key] = value
            card.layout.addWidget(value)
            card.layout.addWidget(label(caption, 'muted'))
            metrics.addWidget(card, 1)
        page.addLayout(metrics)
        row = QHBoxLayout()
        row.setSpacing(18)
        channels = Card('消息将从这里出发', '点击渠道设置，调整接收方式。', 'send')
        self.channel_summary = label('', wrap=True)
        channels.layout.addWidget(self.channel_summary)
        channels.layout.addWidget(note('只用 QQ？关闭邮箱与 Telegram 后，无需填写它们的账号或 Token。'))
        channels.layout.addWidget(button('管理推送渠道', lambda: self.select_page(1), icon='arrow'))
        row.addWidget(channels, 3)
        quick = Card('准备好开始了吗', '停用渠道立即保存；其他变更编辑后保存。', 'spark')
        quick.layout.addWidget(label('01   连接 NapCat 与接收 QQ\n\n02   填写 AI 模型与密钥\n\n03   选择时间、来源并保存', 'muted'))
        quick.layout.addWidget(button('查看 AI 模型', lambda: self.select_page(2), icon='arrow'))
        quick.layout.addWidget(button('服务启停与网络检查', lambda: self.select_page(6), icon='cpu'))
        row.addWidget(quick, 2)
        page.addLayout(row)
        page.addStretch()

    def _build_channels(self):
        page = self.page()
        page.addWidget(note('关闭渠道会立即保存并停止后续发送；开启渠道和其他编辑需点击“保存配置”。已停用渠道不会随放弃其他编辑而恢复。'))
        specs = [('qq', 'QQ · NapCat', '通过本机 NapCat，将消息送到好友或群聊。', 'qq'),
                 ('email', '邮箱', '发送完整 HTML 日报，适合归档与阅读。', 'mail'),
                 ('telegram', 'Telegram', '通过你的 Telegram Bot 接收新闻。', 'send')]
        for name, title, description, icon in specs:
            card = Card(title, description, icon)
            toggle = Toggle('启用 ' + title)
            self.channel_toggles[name] = toggle
            card.header.addWidget(toggle)
            body = QWidget()
            form = QVBoxLayout(body)
            form.setContentsMargins(0, 0, 0, 0)
            form.setSpacing(15)
            self.channel_forms[name] = body
            if name == 'qq':
                self.grid_into(form, [
                    self.field('push.qq.http_base', 'HTTP 服务地址', default='http://127.0.0.1:3000', hint='需与 NapCat HTTP 服务地址一致'),
                    self.field('push.qq.ws_port', 'WebSocket 端口', 'integer', 3001, low=1, high=65535),
                    self.field('push.qq.access_token', '接口鉴权 Token', 'secret', hint='NapCat 未设置鉴权时可留空'),
                    self.field('push.qq.group_id', '接收 QQ 群号', hint='可选；机器人需已加入该群'),
                ])
                form.addWidget(self.field('push.qq.private_qq', '接收新闻的 QQ 号', 'qq_list', [], '多个 QQ 号用逗号或换行分隔，也可以向机器人发送“订阅新闻”'))
                subscribers_row = QHBoxLayout()
                self.subscription_status = label('自动同步机器人订阅列表，每 2 秒检查一次。', 'muted', True)
                subscribers_row.addWidget(self.subscription_status, 1)
                subscribers_row.addWidget(button('刷新订阅列表', lambda: self.refresh_subscribers(force=True), icon='refresh'))
                form.addLayout(subscribers_row)
                probe_row = QHBoxLayout()
                self.probe_button = button('检查 NapCat 连接', self.check_connection, icon='refresh')
                self.probe_status = label('尚未检查连接', 'muted', True)
                probe_row.addWidget(self.probe_button)
                probe_row.addWidget(self.probe_status, 1)
                form.addLayout(probe_row)
                self.test_qq = QComboBox()
                self.test_qq.setEditable(True)
                self.test_qq.setAccessibleName('测试接收 QQ 号')
                self.test_qq.setPlaceholderText('选择或输入一个测试接收 QQ 号')
                self.test_qq.lineEdit().setPlaceholderText('选择或输入一个测试接收 QQ 号')
                test_row = QHBoxLayout()
                test_row.addWidget(self.test_qq, 1)
                self.test_send_button = button('发送 QQ 测试消息', self.send_qq_test, icon='send')
                test_row.addWidget(self.test_send_button)
                form.addLayout(test_row)
                form.addWidget(label('测试内容：' + TEST_MESSAGE, 'muted', True))
                self.test_send_status = label('每次点击只向所选 QQ 号发送一条测试消息。', 'muted', True)
                form.addWidget(self.test_send_status)
                self.fields['push.qq.private_qq'][0].textChanged.connect(self.update_test_recipients)
            elif name == 'email':
                self.grid_into(form, [
                    self.field('push.email.smtp_host', 'SMTP 服务器', default='smtp.163.com'),
                    self.field('push.email.smtp_port', 'SMTP 端口', 'integer', 465, low=1, high=65535),
                    self.field('push.email.username', '发件邮箱'),
                    self.field('push.email.password', '邮箱授权码', 'secret', hint='填写 SMTP 授权码'),
                ])
                form.addWidget(self.field('push.email.to', '收件邮箱', 'list', [], '多个邮箱用逗号或换行分隔'))
            else:
                self.grid_into(form, [
                    self.field('push.telegram.bot_token', 'Bot Token', 'secret', hint='由 Telegram BotFather 提供'),
                    self.field('push.telegram.chat_id', 'Chat ID', hint='用户、群组或频道 ID'),
                ])
                form.addWidget(self.field('push.telegram.proxy', 'Telegram 代理', hint='可选，例如 http://127.0.0.1:7890'))
            toggle.toggled.connect(lambda checked, n=name: self.channel_visibility(n, checked))
            card.layout.addWidget(body)
            page.addWidget(card)
        page.addStretch()

    def grid_into(self, layout, items):
        grid = QGridLayout()
        grid.setSpacing(18)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        for i, item in enumerate(items):
            grid.addWidget(item, i // 2, i % 2)
        layout.addLayout(grid)

    def channel_visibility(self, name, checked):
        disabled_saved = False
        initial = get_value(self.store.data, 'push.enabled', [])
        if not self.loading and not checked and name in initial:
            try:
                def disable(latest):
                    enabled = get_value(latest, 'push.enabled', [])
                    if not isinstance(enabled, list) or any(not isinstance(value, str) for value in enabled):
                        raise ValueError('推送渠道列表无效')
                    set_value(latest, 'push.enabled', [value for value in enabled if value != name])
                    return latest

                atomic_update_json(self.store.path, disable, backup=True)
                # Only advance the baseline for this switch. Other windows may
                # have enabled a channel this window has not displayed yet.
                set_value(self.store.data, 'push.enabled', [value for value in initial if value != name])
                disabled_saved = True
            except (OSError, ValueError, TypeError):
                toggle = self.channel_toggles[name]
                toggle.blockSignals(True)
                toggle.setChecked(True)
                toggle.blockSignals(False)
                toggle.animation.stop()
                toggle.set_position(1.0)
                checked = True
                QMessageBox.warning(self, '未能停用渠道', '停用未保存，开关已恢复。请检查配置文件与目录权限后重试。')
        self.channel_forms[name].setVisible(checked)
        self.on_change()
        if disabled_saved:
            title = {'qq': 'QQ', 'email': '邮箱', 'telegram': 'Telegram'}.get(name, name)
            dirty = len(self.changes())
            self.save_status.setText(f'{title}已停用并保存' + (f' · 另有 {dirty} 项未保存更改' if dirty else ' · 后续发送立即停止'))

    def _build_models(self):
        # Navigation stays outside scrolling content. Each mode owns its own scroll
        # viewport, so long model forms cannot stretch/cover the tab hit targets.
        root = QWidget()
        page = QVBoxLayout(root)
        page.setContentsMargins(32, 2, 32, 16)
        page.setSpacing(14)
        page.addWidget(note('主流服务可应用预设，也可自由填写模型和地址。预设不会改动 API Key。备用模型不用时留空；本地模型可关闭“需要 API Key”。'))
        segment_row = QHBoxLayout()
        self.model_mode_group = QButtonGroup(self)
        self.model_mode_group.setExclusive(True)
        self.model_mode_buttons = {}
        self.model_presets = {}
        self.model_preset_hints = {}
        self.model_stack = QStackedWidget()
        for index, (section, title) in enumerate([('llm', '新闻处理'), ('chat', 'QQ 互动聊天')]):
            segment = button(title)
            segment.setObjectName('modelSegment')
            segment.setCheckable(True)
            segment.setMinimumHeight(42)
            self.model_mode_group.addButton(segment, index)
            self.model_mode_buttons[section] = segment
            segment_row.addWidget(segment)
        segment_row.addStretch()
        page.addLayout(segment_row)
        self.model_mode_group.idClicked.connect(self.model_stack.setCurrentIndex)
        for section, title in [('llm', '新闻处理'), ('chat', 'QQ 互动聊天')]:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            pane = QWidget()
            layout = QVBoxLayout(pane)
            layout.setContentsMargins(0, 2, 9, 14)
            layout.setSpacing(18)
            for role, role_name in [('primary', '主模型'), ('fallback', '备用模型')]:
                card = Card(title + ' · ' + role_name, '优先处理请求' if role == 'primary' else '主模型不可用时尝试调用', 'cpu')
                prefix = f'{section}.{role}'
                presets_row = QHBoxLayout()
                presets = QComboBox()
                presets.setAccessibleName(title + role_name + '服务商预设')
                presets.addItem('选择服务商预设…', '')
                for preset in PROVIDER_PRESETS:
                    presets.addItem(preset.label, preset.id)
                self.model_presets[prefix] = presets
                presets_row.addWidget(presets, 1)
                presets_row.addWidget(button('应用预设', lambda checked=False, p=prefix: self.apply_model_preset(p)))
                card.layout.addLayout(presets_row)
                preset_hint = label('所有输入均可编辑，兼容小众模型与自建中转服务。', 'muted', True)
                self.model_preset_hints[prefix] = preset_hint
                card.layout.addWidget(preset_hint)
                self.grid(card, [
                    self.field(prefix + '.provider', '服务商标识', tuple(p.id for p in PROVIDER_PRESETS), 'agnes'),
                    self.field(prefix + '.adapter', 'API 调用协议', 'adapter', 'openai-compatible'),
                    self.field(prefix + '.model', '模型名称（可自定义）', (), hint='选择示例或直接输入完整模型 ID'),
                    self.field(prefix + '.base_url', 'API 基础地址', hint='填写基础地址，程序根据协议补全接口路径'),
                    self.field(prefix + '.api_key', 'API Key', 'secret'),
                    self.field(prefix + '.requires_api_key', '认证方式', 'boolean', True),
                ])
                provider_editor = self.fields[prefix + '.provider'][0]
                provider_editor.currentTextChanged.connect(lambda value, p=prefix: self.update_model_suggestions(p, value))
                advanced = button('展开高级设置')
                advanced.setObjectName('link')
                advanced.setCheckable(True)
                card.layout.addWidget(advanced)
                extra = QWidget()
                extra_layout = QVBoxLayout(extra)
                extra_layout.setContentsMargins(0, 0, 0, 0)
                settings = [
                    self.field(prefix + '.timeout', '请求超时（秒）', 'decimal', 90, low=.1, high=600),
                    self.field(prefix + '.retries', '额外重试次数', 'integer', 1, high=10),
                    self.field(prefix + '.max_tokens', '回复 Token 上限', 'integer', 1800, low=1, high=131072),
                ]
                if role == 'primary':
                    settings.extend([
                        self.field(prefix + '.circuit_open_seconds', '熔断等待时间', 'decimal', 600, low=.1, high=86400, suffix=' 秒'),
                        self.field(prefix + '.circuit_failures', '触发熔断的连续失败次数', 'integer', 3, low=1, high=100),
                    ])
                settings.append(self.field(prefix + '.proxy', '模型专用代理', hint='可选；不继承新闻采集代理'))
                self.grid_into(extra_layout, settings)
                extra.hide()
                advanced.toggled.connect(extra.setVisible)
                advanced.toggled.connect(lambda checked, b=advanced: b.setText('收起高级设置' if checked else '展开高级设置'))
                card.layout.addWidget(extra)
                layout.addWidget(card)
            layout.addStretch()
            area.setWidget(pane)
            self.model_stack.addWidget(area)
        self.model_mode_buttons['llm'].setChecked(True)
        self.model_stack.setCurrentIndex(0)
        page.addWidget(self.model_stack, 1)
        self.stack.addWidget(root)

    def update_model_suggestions(self, prefix, provider):
        editor = self.fields[prefix + '.model'][0]
        text = editor.currentText()
        preset = PROVIDERS_BY_ID.get(provider)
        editor.blockSignals(True)
        editor.clear()
        if preset:
            editor.addItems(list(preset.models))
        editor.setCurrentText(text)
        editor.blockSignals(False)
        hint = self.model_preset_hints[prefix]
        hint.setText(preset.hint if preset else '自定义服务：请核对调用协议、基础地址和模型 ID。')

    def apply_model_preset(self, prefix):
        selected = self.model_presets[prefix].currentData()
        preset = PROVIDERS_BY_ID.get(selected)
        if preset is None:
            self.model_preset_hints[prefix].setText('请先选择一个预设；自定义服务同样可以应用后继续修改。')
            return
        self.fields[prefix + '.provider'][0].setCurrentText(preset.id)
        adapter = self.fields[prefix + '.adapter'][0]
        adapter.setCurrentIndex(adapter.findData(preset.adapter))
        self.fields[prefix + '.base_url'][0].setText(preset.base_url)
        self.fields[prefix + '.model'][0].setCurrentText(preset.models[0] if preset.models else '')
        self.fields[prefix + '.requires_api_key'][0].setChecked(preset.key_required)
        if preset.id not in ('agnes', 'custom', 'ollama', 'lmstudio'):
            tokens = self.fields[prefix + '.max_tokens'][0]
            tokens.setValue(max(tokens.value(), 8192))
        self.update_model_suggestions(prefix, preset.id)
        self.on_change()

    def _build_schedule(self):
        page = self.page()
        card = Card('安排你的消息节奏', '采集间隔以每轮任务结束为起点；日报采用电脑本地时间。', 'clock')
        self.grid(card, [
            self.field('schedule.realtime_interval_minutes', '采集间隔', 'integer', 10, low=1, high=1440, suffix=' 分钟'),
            self.field('schedule.daily_report_time', '每日日报时间', 'time', '08:00'),
        ])
        card.layout.addWidget(note('日报到达设定时间后执行；当前采集完成后检查，错过轮询窗口会补执行。'))
        page.addWidget(card)
        card = Card('优先看到真正重要的', '分数越高，筛选越严格；综合分 = 重要性 × 60% + 实用性 × 40%。', 'spark')
        self.grid(card, [
            self.field('scoring.major_threshold', '白天推送门槛 · 06:00–23:00', 'decimal', 8, high=10),
            self.field('scoring.realtime_threshold', '夜间推送门槛 · 23:00–06:00', 'decimal', 7.5, high=10),
            self.field('scoring.min_for_daily', '日报最低重要性评分', 'decimal', 6, high=10),
            self.field('scoring.doubt_threshold', '存疑新闻最低重要性评分', 'decimal', 8.5, high=10),
        ])
        page.addWidget(card)
        card = Card('控制信息量', '为每轮推送、每份日报与模型处理设置上限。')
        self.grid(card, [
            self.field('scoring.max_realtime_per_cycle', '每轮推送条数上限', 'integer', 30, low=1, high=1000),
            self.field('scoring.max_daily_items', '日报条数上限', 'integer', 40, low=1, high=1000),
            self.field('scoring.llm_batch_size', '模型批处理条数', 'integer', 8, low=1, high=100),
        ])
        page.addWidget(card)
        page.addStretch()

    def _build_sources(self):
        page = self.page()
        rss = Card('你的 RSS 订阅', '双击单元格编辑名称或链接；选中一行后可以移除。', 'rss')
        self.rss_table = QTableWidget(0, 2)
        self.rss_table.setHorizontalHeaderLabels(['来源名称', 'RSS / Atom 订阅地址'])
        self.rss_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.rss_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.rss_table.setColumnWidth(0, 175)
        self.rss_table.verticalHeader().hide()
        self.rss_table.verticalHeader().setDefaultSectionSize(43)
        self.rss_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.rss_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.rss_table.setMinimumHeight(290)
        self.rss_table.itemChanged.connect(self.on_change)
        rss.layout.addWidget(self.rss_table)
        actions = QHBoxLayout()
        actions.addWidget(button('＋ 添加订阅源', self.add_rss))
        actions.addWidget(button('移除所选', self.remove_rss))
        actions.addStretch()
        rss.layout.addLayout(actions)
        rss.layout.addWidget(self.field('sources.rss_max_per_source', '每个 RSS 源每轮采集上限', 'integer', 12, low=1, high=1000, suffix=' 条'))
        rss.layout.addWidget(label('列表为空时会启用程序内置的默认订阅源。', 'muted'))
        page.addWidget(rss)
        self.source_toggles = {}
        self.source_forms = {}
        for name, title, description in [
            ('github_trending', 'GitHub Trending', '发现正在流行的开源项目。'),
            ('arxiv', 'arXiv 论文', '关注人工智能与机器学习的研究进展。'),
            ('steam', 'Steam 游戏特惠', '追踪商店折扣与特价游戏。'),
            ('hackernews', 'Hacker News', '看看科技社区正在讨论什么。'),
        ]:
            card = Card(title, description, 'rss')
            toggle = Toggle('采集 ' + title)
            self.source_toggles[name] = toggle
            card.header.addWidget(toggle)
            body = QWidget()
            self.source_forms[name] = body
            inner = QVBoxLayout(body)
            inner.setContentsMargins(0, 0, 0, 0)
            items = [self.field(f'sources.{name}.max_items', '每轮采集上限', 'integer', 20, low=0, high=1000, suffix=' 条')]
            if name == 'github_trending':
                items.extend([
                    self.field('sources.github_trending.language', '编程语言', hint='留空为全部语言，例如 python'),
                    self.field('sources.github_trending.since', '榜单范围', ('daily', 'weekly', 'monthly'), 'daily'),
                ])
            if name == 'arxiv':
                items.append(self.field('sources.arxiv.categories', '论文分类', 'list', ['cs.AI', 'cs.CL', 'cs.LG'], 'cs.AI 人工智能 · cs.CL 计算与语言 · cs.LG 机器学习'))
            self.grid_into(inner, items)
            card.layout.addWidget(body)
            toggle.toggled.connect(body.setVisible)
            toggle.toggled.connect(self.on_change)
            page.addWidget(card)
        network = Card('新闻采集代理', 'RSS 默认直连；其他采集来源按代码中的代理策略使用。')
        network.layout.addWidget(self.field('proxy', '代理地址', hint='可选，例如 http://127.0.0.1:7890；留空不显式指定代理'))
        page.addWidget(network)
        page.addStretch()

    def _build_logs(self):
        page = self.page()
        card = Card('最近的运行记录', '每 3 秒读取一次日志末尾；打开配置界面不会启动新闻服务。', 'file')
        row = QHBoxLayout()
        row.addWidget(button('打开日志文件夹', self.open_logs, icon='folder'))
        row.addWidget(button('刷新', self.refresh_logs, icon='refresh'))
        row.addStretch()
        card.layout.addLayout(row)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(410)
        self.log_view.setStyleSheet("font-family: 'Cascadia Mono', Consolas; font-size: 12px; background: #f8faf8; padding: 16px;")
        self.log_view.document().setMaximumBlockCount(200)
        card.layout.addWidget(self.log_view)
        card.layout.addWidget(label('日志可能包含 QQ 号或来源地址，请谨慎分享。', 'muted'))
        page.addWidget(card)
        page.addStretch()

    def _build_services(self):
        page = self.page()
        card = Card('运行控制台', '按实际进程与接口检测状态，每 8 秒刷新一次。', 'cpu')
        actions = QHBoxLayout()
        self.start_all_button = button('运行全部服务', self.start_all_services, primary=True, icon='arrow')
        self.stop_all_button = button('停止全部服务', self.stop_all_services)
        self.refresh_services_button = button('刷新状态', self.refresh_services, icon='refresh')
        actions.addWidget(self.start_all_button)
        actions.addWidget(self.stop_all_button)
        actions.addWidget(self.refresh_services_button)
        actions.addStretch()
        card.layout.addLayout(actions)
        self.service_labels = {}
        self.service_selectors = {}
        for key, title in [('backend', '新闻与 QQ 互动后台'), ('napcat', 'NapCat 进程'),
                           ('steampp', 'Steam++ / Watt Toolkit'),
                           ('qq_http', 'QQ HTTP 登录状态'), ('qq_ws', 'QQ WebSocket 端口')]:
            row = QHBoxLayout()
            if key in {'backend', 'napcat', 'steampp'}:
                name_label = QCheckBox(title)
                name_label.setChecked(key == 'backend')
                self.service_selectors[key] = name_label
            else:
                name_label = label(title, 'fieldLabel')
            name_label.setMinimumWidth(190)
            row.addWidget(name_label)
            state = label('等待检测', 'muted', True)
            self.service_labels[key] = state
            row.addWidget(state, 1)
            card.layout.addLayout(row)
        selected = QHBoxLayout()
        selected.addWidget(label('所选服务：', 'muted'))
        self.start_selected_button = button('启动所选', lambda: self.control_selected_services('start'), icon='arrow')
        self.stop_selected_button = button('停止所选', lambda: self.control_selected_services('stop'))
        self.restart_selected_button = button('重启所选', lambda: self.control_selected_services('restart'), icon='refresh')
        for control in (self.start_selected_button, self.stop_selected_button, self.restart_selected_button):
            selected.addWidget(control)
        selected.addStretch()
        card.layout.addLayout(selected)
        self.services_timestamp = label('尚未检测', 'muted', True)
        card.layout.addWidget(self.services_timestamp)
        card.layout.addWidget(note('保存后，运行中的后台约 1 秒内感知配置，新请求使用新设置；QQ 地址或 Token 变化会自动重连。NapCat 端口/鉴权仍须与它自身设置一致。Steam++ 路径变更用于下一次启动，可自行勾选重启。'))
        self.service_operation_result = label('', 'muted', True)
        card.layout.addWidget(self.service_operation_result)
        login = Card('QQ 扫码登录', '自动显示本次 NAPCAT 运行生成的最新二维码。', 'qq')
        self.napcat_qr_image = label('暂未启动 NAPCAT 服务', 'napcatQrImage', True)
        self.napcat_qr_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.napcat_qr_image.setFixedSize(240, 240)
        self.napcat_qr_image.setStyleSheet('background: white; border: 1px solid #dce6df; border-radius: 10px; padding: 10px; color: #829088;')
        login.layout.addWidget(self.napcat_qr_image, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.napcat_qr_message = label('启动 NAPCAT 后会自动显示二维码。', 'muted', True)
        login.layout.addWidget(self.napcat_qr_message)
        self.napcat_qr_refresh = button('重新读取二维码', lambda: self.refresh_napcat_qr(force=True), icon='refresh')
        login.layout.addWidget(self.napcat_qr_refresh)
        login.layout.addWidget(note('只从本机读取，不上传、不写入新闻缓存。请勿把登录二维码截图发给别人。'))
        login.layout.addStretch()
        self.service_top_layout = QHBoxLayout()
        self.service_top_layout.setSpacing(18)
        self.service_top_layout.addWidget(card, 3)
        self.service_top_layout.addWidget(login, 2)
        self.service_top_layout.setDirection(QBoxLayout.Direction.LeftToRight if self.width() >= 1210 else QBoxLayout.Direction.TopToBottom)
        page.addLayout(self.service_top_layout)
        network = Card('GitHub 与 Steam 连通性', '优先使用采集代理；连接失败时检查系统代理、VPN 和直连路线。', 'rss')
        self.sites_button = button('检查网站连通性', self.check_sites, icon='refresh')
        network.header.addWidget(self.sites_button)
        self.site_labels = {}
        for key, title in [('github', 'GitHub Trending'), ('steam', 'Steam 商店特惠')]:
            row = QHBoxLayout()
            name = label(title, 'fieldLabel')
            name.setMinimumWidth(190)
            row.addWidget(name)
            state = label('尚未检查', 'muted', True)
            self.site_labels[key] = state
            row.addWidget(state, 1)
            network.layout.addLayout(row)
        self.sites_timestamp = label('点击检查后显示 HTTP 结果和耗时；网站可访问不保证所有新闻条目都可抓取。', 'muted', True)
        network.layout.addWidget(self.sites_timestamp)
        page.addWidget(network)
        paths = Card('应用路径', 'Steam++ 未在常用位置安装时，可在这里选择可执行文件。', 'folder')
        path_row = QHBoxLayout()
        path_row.addWidget(self.field('desktop.steampp_path', 'Steam++ 可执行文件', hint='留空时自动查找运行中的 Steam++ 和常见安装位置'), 1)
        path_row.addWidget(button('浏览…', self.browse_steampp), alignment=Qt.AlignmentFlag.AlignCenter)
        paths.layout.addLayout(path_row)
        paths.layout.addWidget(note('运行全部服务会先保存当前编辑，然后依次启动依赖和新闻后台。停止仅针对本项目后台、NapCat 以及识别到的 Steam++，配置窗口会继续保留。'))
        page.addWidget(paths)
        page.addStretch()

    def browse_steampp(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择 Steam++ / Watt Toolkit', '', '应用程序 (*.exe)')
        if path:
            self.fields['desktop.steampp_path'][0].setText(path)

    def effective_config(self):
        data = deepcopy(self.store.data)
        for path, value in self.changes().items():
            set_value(data, path, value)
        return data

    def run_background(self, name, callback):
        if name in self.background_tasks or self._closing:
            return False
        task = BackgroundTask(name, callback)
        self.background_tasks[name] = task
        task.signals.done.connect(self.background_finished)
        QThreadPool.globalInstance().start(task)
        return True

    def refresh_services(self):
        if self.loading or self._closing or 'control' in self.background_tasks:
            return
        config = self.effective_config()
        if self.run_background('status', lambda: self.service_manager.snapshot(config)):
            self.refresh_services_button.setEnabled(False)

    def start_all_services(self):
        if 'control' in self.background_tasks:
            return
        if self.changes() and not self.save():
            return
        try:
            config = self.store.read()
            errors = validate_config(config)
            if errors:
                QMessageBox.warning(self, '启动前请检查配置', '\n'.join(errors))
                return
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, '无法读取配置', str(exc))
            return
        self.prepare_napcat_qr('start')
        self._control_services('正在启动全部服务…', lambda: self.service_manager.start_all(config))

    def stop_all_services(self):
        if 'control' in self.background_tasks:
            return
        config = self.effective_config()
        self.prepare_napcat_qr('stop')
        self._control_services('正在停止全部服务…', lambda: self.service_manager.stop_all(config))

    def _control_services(self, message, callback):
        self.service_operation_result.setText(message)
        self._enable_service_controls(False)
        self.run_background('control', callback)

    def _enable_service_controls(self, enabled):
        for control in (self.start_all_button, self.stop_all_button, self.start_selected_button,
                        self.stop_selected_button, self.restart_selected_button):
            control.setEnabled(enabled)
        for control in self.service_selectors.values():
            control.setEnabled(enabled)
        self.napcat_qr_refresh.setEnabled(enabled)

    def control_selected_services(self, action):
        if 'control' in self.background_tasks:
            return
        targets = [name for name, control in self.service_selectors.items() if control.isChecked()]
        if not targets:
            self.service_operation_result.setText('请先勾选至少一项服务。')
            return
        if action in {'start', 'restart'}:
            if self.changes() and not self.save():
                return
            try:
                config = self.store.read()
                errors = validate_config(config) if 'backend' in targets else []
                if errors:
                    QMessageBox.warning(self, '请先检查配置', '\n'.join(errors))
                    return
            except (OSError, ValueError):
                QMessageBox.warning(self, '无法读取配置', '请检查配置文件后重试。')
                return
        else:
            config = self.effective_config()
        title = {'start': '启动', 'stop': '停止', 'restart': '重启'}[action]
        if 'napcat' in targets:
            self.prepare_napcat_qr(action)
        self._control_services('正在' + title + '所选服务…',
            lambda: self.service_manager.control_services(config, targets, action))

    def prepare_napcat_qr(self, action):
        self._napcat_qr_epoch += 1
        self._napcat_qr_signature = None
        self._napcat_qr_status = {'state': 'unknown'}
        text = '正在停止 NAPCAT 服务…' if action == 'stop' else '正在启动 NAPCAT，等待新二维码…'
        self.render_napcat_qr({'state': 'waiting', 'message': text})

    def update_napcat_qr_source(self, napcat_status, qq_status):
        signature = (napcat_status.get('state'), tuple((p.get('pid'), p.get('created')) for p in napcat_status.get('processes', [])), bool(qq_status.get('ok')))
        if signature != self._napcat_qr_signature:
            self._napcat_qr_epoch += 1
            self._napcat_qr_signature = signature
            if napcat_status.get('state') == 'running' and not qq_status.get('ok'):
                self.render_napcat_qr({'state': 'waiting', 'message': 'NAPCAT 登录会话已更新，正在读取最新二维码…'})
        self._napcat_qr_status, self._napcat_qq_status = napcat_status, qq_status
        if napcat_status.get('state') != 'running':
            self.render_napcat_qr({'state': napcat_status.get('state', 'unknown'),
                                  'message': '暂未启动 NAPCAT 服务' if napcat_status.get('state') == 'stopped' else '正在确认 NAPCAT 状态，暂不显示旧二维码。'})
        elif qq_status.get('ok'):
            self.render_napcat_qr({'state': 'logged_in', 'message': 'QQ 已登录，无需扫码。'})
        elif self.monitor_services:
            self.refresh_napcat_qr()

    def refresh_napcat_qr(self, force=False):
        if self._closing or 'control' in self.background_tasks:
            return
        if force:
            self.refresh_services()
        if self._napcat_qr_status.get('state') != 'running':
            return
        epoch = self._napcat_qr_epoch
        status, qq = deepcopy(self._napcat_qr_status), deepcopy(self._napcat_qq_status)
        def read():
            try:
                result = self.service_manager.napcat_login_qr(status, qq)
            except Exception:
                result = {'state': 'unknown', 'message': '二维码暂时无法读取，已隐藏旧码，稍后自动重试。'}
            return {'epoch': epoch, 'result': result}
        self.run_background('napcat-qr', read)

    def render_napcat_qr(self, result):
        state = result.get('state', 'unknown')
        if state == 'qr':
            if result.get('version') != self._napcat_qr_version:
                pixmap = QPixmap()
                if not pixmap.loadFromData(result.get('image', b''), 'PNG'):
                    self.render_napcat_qr({'state': 'waiting', 'message': '二维码图片正在更新，稍后重新读取…'})
                    return
                self.napcat_qr_image.clear()
                self.napcat_qr_image.setPixmap(pixmap.scaled(216, 216, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))
                self._napcat_qr_version = result.get('version')
            updated = datetime.fromtimestamp(result['updated_at']).strftime('%H:%M:%S') if result.get('updated_at') else ''
            self.napcat_qr_message.setText(result.get('message', '') + ('\n二维码更新：' + updated if updated else ''))
        else:
            self._napcat_qr_version = None
            self.napcat_qr_image.clear()
            placeholder = {'stopped': '暂未启动 NAPCAT 服务', 'logged_in': 'QQ 已登录',
                           'stale': '等待最新二维码', 'waiting': '等待二维码生成…'}.get(state, '等待确认 NAPCAT 状态…')
            self.napcat_qr_image.setText(placeholder)
            self.napcat_qr_message.setText(result.get('message', '启动 NAPCAT 后会显示二维码。'))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'service_top_layout'):
            self.service_top_layout.setDirection(QBoxLayout.Direction.LeftToRight if self.width() >= 1210 else QBoxLayout.Direction.TopToBottom)

    def check_sites(self):
        config = self.effective_config()
        if self.run_background('sites', lambda: self.service_manager.check_sites(config)):
            self.sites_button.setEnabled(False)
            for state in self.site_labels.values():
                state.setText('正在连接…')

    def render_statuses(self, labels, results):
        for key, state_label in labels.items():
            status = results.get(key, {})
            message = status.get('message', '未能取得状态')
            if status.get('elapsed_ms') is not None:
                message += f" · {status['elapsed_ms']} ms"
            state_label.setText(message)
            state_label.setToolTip(status.get('recommendation', ''))
            state_label.setStyleSheet('color: #277c55;' if status.get('ok') else 'color: #977349;')

    def background_finished(self, name, result):
        self.background_tasks.pop(name, None)
        if self._closing:
            return
        if name == 'status':
            self.refresh_services_button.setEnabled(True)
            if isinstance(result, dict) and 'error' not in result:
                self.render_statuses(self.service_labels, result)
                if 'control' not in self.background_tasks:
                    self.update_napcat_qr_source(result.get('napcat', {'state': 'unknown'}), result.get('qq_http', {}))
                backend = result.get('backend', {})
                state = backend.get('state', '')
                if backend.get('ok'):
                    self.header_status.setText('后台运行中')
                elif state in {'unknown', 'error'}:
                    self.header_status.setText('后台状态未知')
                elif state == 'starting':
                    self.header_status.setText('后台切换中')
                else:
                    self.header_status.setText('后台未运行')
                self.services_timestamp.setText('最近检测：' + datetime.now().strftime('%H:%M:%S') + ' · 进程运行状态与 QQ 接口分别检测')
            else:
                self.update_napcat_qr_source({'state': 'unknown'}, {})
                self.header_status.setText('后台状态未知')
                self.services_timestamp.setText('状态检测失败，请稍后刷新。')
        elif name == 'sites':
            self.sites_button.setEnabled(True)
            if isinstance(result, dict) and 'error' not in result:
                self.render_statuses(self.site_labels, result)
                routes = sorted({status.get('route_label', '') for status in result.values() if isinstance(status, dict) and status.get('route_label')})
                recommendations = list(dict.fromkeys(status.get('recommendation', '') for status in result.values() if isinstance(status, dict) and status.get('recommendation')))
                self.sites_timestamp.setText('最近检查：' + datetime.now().strftime('%H:%M:%S') + (' · ' + ' / '.join(routes) if routes else '') + ' · 耗时包含线路切换' + ('\n' + '\n'.join(recommendations) if recommendations else ''))
            else:
                for state in self.site_labels.values():
                    state.setText('检测失败，请检查代理配置后重试。')
        elif name == 'control':
            self._enable_service_controls(True)
            self.service_operation_result.setText('\n'.join(result) if isinstance(result, list) else result.get('error', '操作未完成'))
            self.refresh_services()
        elif name == 'qq-test':
            self.test_send_button.setEnabled(True)
            success = isinstance(result, dict) and result.get('ok', False)
            self.test_send_status.setText(result.get('message', result.get('error', '发送未完成')) if isinstance(result, dict) else '发送未完成')
            self.test_send_status.setStyleSheet('color: #277c55;' if success else 'color: #977349;')
        elif name == 'cache':
            self.cache_page.finish(result)
        elif name == 'napcat-qr':
            if isinstance(result, dict) and result.get('epoch') == self._napcat_qr_epoch:
                self.render_napcat_qr(result.get('result', {}))

    def stop_monitoring(self):
        self._closing = True
        self.log_timer.stop()
        self.subscription_timer.stop()
        self.service_timer.stop()
        self.napcat_qr_timer.stop()
        self.cache_page.timer.stop()
        for task in self.background_tasks.values():
            try:
                task.signals.done.disconnect(self.background_finished)
            except (RuntimeError, TypeError):
                pass

    def editor_value(self, path):
        editor, kind, _ = self.fields[path]
        if isinstance(editor, QCheckBox):
            return editor.isChecked()
        if isinstance(editor, (QSpinBox, QDoubleSpinBox)):
            return editor.value()
        if isinstance(editor, QTimeEdit):
            return editor.time().toString('HH:mm')
        if isinstance(editor, QComboBox):
            if kind == 'adapter':
                return editor.currentData() or editor.currentText()
            return editor.currentText().strip()
        if isinstance(editor, QPlainTextEdit):
            text = editor.toPlainText()
            return [item.strip() for item in re.split(r'[,，;；\n]+', text) if item.strip()]
        return editor.text().strip()

    def populate(self):
        self.loading = True
        for path, (editor, kind, default) in self.fields.items():
            if path.endswith(('.adapter', '.requires_api_key')):
                role_config = get_value(self.store.data, path.rsplit('.', 1)[0], {})
                try:
                    default = resolve_adapter(role_config) if kind == 'adapter' else provider_requires_api_key(role_config)
                except ValueError:
                    pass
            value = get_value(self.store.data, path, deepcopy(default))
            editor.blockSignals(True)
            if isinstance(editor, QCheckBox):
                editor.setChecked(bool(value))
            elif isinstance(editor, (QSpinBox, QDoubleSpinBox)):
                # Loading a valid pre-existing large value must not silently clamp it.
                if isinstance(editor, QDoubleSpinBox):
                    from decimal import Decimal
                    precision = max(1, -Decimal(str(value)).as_tuple().exponent)
                    editor.setDecimals(min(precision, 12))
                editor.setRange(min(editor.minimum(), value), max(editor.maximum(), value))
                editor.setValue(value)
            elif isinstance(editor, QTimeEdit):
                time = QTime.fromString(str(value), 'HH:mm')
                editor.setTime(time if time.isValid() else QTime(8, 0))
            elif isinstance(editor, QComboBox):
                if kind == 'adapter':
                    index = editor.findData(value)
                    if index < 0:
                        editor.addItem(str(value), value)
                        index = editor.count() - 1
                    editor.setCurrentIndex(index)
                else:
                    editor.setCurrentText(str(value))
            elif isinstance(editor, QPlainTextEdit):
                editor.setPlainText('\n'.join(str(x) for x in value) if isinstance(value, list) else str(value or ''))
            else:
                editor.setText(str(value or ''))
                if kind == 'secret':
                    editor.reveal_button.setChecked(False)
                    editor.setEchoMode(QLineEdit.EchoMode.Password)
            editor.blockSignals(False)
            self.baseline[path] = self.editor_value(path)
        for prefix in self.model_presets:
            provider = get_value(self.store.data, prefix + '.provider', 'agnes')
            self.model_presets[prefix].setCurrentIndex(max(0, self.model_presets[prefix].findData(provider)))
            self.update_model_suggestions(prefix, provider)
        enabled = get_value(self.store.data, 'push.enabled', [])
        for name, toggle in self.channel_toggles.items():
            toggle.setChecked(name in enabled)
            self.channel_forms[name].setVisible(name in enabled)
        for name, toggle in self.source_toggles.items():
            toggle.setChecked(bool(get_value(self.store.data, f'sources.{name}.enabled', True)))
            # Initialize visibility even when setChecked did not emit a signal.
            self.source_forms[name].setVisible(toggle.isChecked())
        self.rss_table.blockSignals(True)
        self.rss_table.setRowCount(0)
        for entry in get_value(self.store.data, 'sources.rss', []):
            self._append_rss(entry)
        self.rss_table.blockSignals(False)
        self.rss_baseline = self.rss_values()
        self.loading = False
        self.update_test_recipients()
        self.on_change()

    def update_test_recipients(self):
        current = self.test_qq.currentText()
        self.test_qq.blockSignals(True)
        self.test_qq.clear()
        self.test_qq.addItems([str(value) for value in self.editor_value('push.qq.private_qq') if str(value).isdigit()])
        if current:
            self.test_qq.setCurrentText(current)
        elif self.test_qq.count():
            self.test_qq.setCurrentIndex(0)
        self.test_qq.blockSignals(False)

    def refresh_subscribers(self, force=False):
        if self.loading or self._closing:
            return
        try:
            stat = self.store.path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            if not force and stamp == self._subscription_stamp:
                return
            latest = self.store.read()
            raw = get_value(latest, 'push.qq.private_qq', [])
            values = [str(value).strip() for value in raw] if isinstance(raw, list) else [part for part in re.split(r'[,，;；\s]+', raw) if part]
            if self.editor_value('push.qq.private_qq') != self.baseline.get('push.qq.private_qq'):
                self.subscription_status.setText('正在编辑接收列表；当前已保存的 QQ：' + ('、'.join(values) or '暂无') + '。完成编辑后保存，若冲突会提示重新载入。')
                # Keep the external change pending if the user later undoes the
                # manual edit; a consumed stamp would hide that subscription.
                return
            else:
                editor = self.fields['push.qq.private_qq'][0]
                editor.blockSignals(True)
                editor.setPlainText('\n'.join(values))
                editor.blockSignals(False)
                self.baseline['push.qq.private_qq'] = self.editor_value('push.qq.private_qq')
                set_value(self.store.data, 'push.qq.private_qq', raw)
                self.subscription_status.setText(f'已同步 {len(values)} 个接收 QQ · {datetime.now().strftime("%H:%M:%S")} · 订阅与退订自动更新')
                self.update_test_recipients()
                self.on_change()
            self._subscription_stamp = stamp
        except (OSError, ValueError, TypeError):
            self.subscription_status.setText('暂时无法读取最新订阅，已保留当前显示内容。')

    def send_qq_test(self):
        if 'qq-test' in self.background_tasks:
            return
        qq = self.test_qq.currentText().strip()
        if not qq.isdigit():
            self.test_send_status.setText('请先选择或输入一个数字 QQ 号。')
            return
        base = self.editor_value('push.qq.http_base')
        token = self.editor_value('push.qq.access_token')
        if self.run_background('qq-test', lambda: send_test_message(base, token, qq)):
            self.test_send_button.setEnabled(False)
            self.test_send_status.setText(f'正在向 {qq} 发送一条测试消息…')

    def restore_defaults(self):
        answer = QMessageBox.question(
            self, '恢复全新配置',
            '恢复为全新空配置？\n将清除当前密钥、邮箱账号、QQ/群号、接收列表和自定义地址，并关闭所有推送渠道。\n'
            '不会恢复历史个人信息；需要重新填写配置才能运行。未保存编辑也会丢弃。',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.restore()
            self.populate()
            self._subscription_stamp = None
            self.refresh_subscribers(force=True)
            self.save_status.setText('已恢复全新配置 · 所有推送已关闭 · 请重新填写账号与密钥')
        except (OSError, ValueError, TypeError):
            QMessageBox.warning(self, '无法恢复默认', self.initial_error or '恢复失败，请检查配置文件和目录权限；当前配置已保留。')

    def _append_rss(self, entry):
        row = self.rss_table.rowCount()
        self.rss_table.insertRow(row)
        name = QTableWidgetItem(str(entry.get('name', '')))
        name.setData(Qt.ItemDataRole.UserRole, deepcopy(entry))
        self.rss_table.setItem(row, 0, name)
        self.rss_table.setItem(row, 1, QTableWidgetItem(str(entry.get('url', ''))))

    def rss_values(self):
        values = []
        for row in range(self.rss_table.rowCount()):
            name, url = self.rss_table.item(row, 0), self.rss_table.item(row, 1)
            if not name or not url:
                continue
            entry = deepcopy(name.data(Qt.ItemDataRole.UserRole) or {})
            entry.update(name=name.text().strip(), url=url.text().strip())
            values.append(entry)
        return values

    def add_rss(self):
        self.rss_table.blockSignals(True)
        self._append_rss({'name': '', 'url': ''})
        self.rss_table.blockSignals(False)
        self.rss_table.setCurrentCell(self.rss_table.rowCount() - 1, 0)
        self.rss_table.editItem(self.rss_table.currentItem())
        self.on_change()

    def remove_rss(self):
        if self.rss_table.currentRow() >= 0:
            self.rss_table.removeRow(self.rss_table.currentRow())
            self.on_change()

    def enabled_channels(self):
        # Preserve unknown channels when only toggling a known channel.
        initial = get_value(self.store.data, 'push.enabled', [])
        result = [name for name in initial if name not in self.channel_toggles or self.channel_toggles[name].isChecked()]
        result.extend(name for name, toggle in self.channel_toggles.items() if toggle.isChecked() and name not in result)
        return result

    def changes(self):
        changed = {path: self.editor_value(path) for path in self.fields if self.editor_value(path) != self.baseline.get(path)}
        enabled = self.enabled_channels()
        if enabled != get_value(self.store.data, 'push.enabled', []):
            changed['push.enabled'] = enabled
        for name, toggle in self.source_toggles.items():
            path = f'sources.{name}.enabled'
            if toggle.isChecked() != bool(get_value(self.store.data, path, True)):
                changed[path] = toggle.isChecked()
        rss = self.rss_values()
        if rss != self.rss_baseline:
            changed['sources.rss'] = rss
        return changed

    def on_change(self, *args):
        if self.loading:
            return
        count = len(self.changes())
        self.save_button.setEnabled(bool(count))
        self.save_status.setText(f'有 {count} 项未保存更改' if count else '所有更改已保存')
        self.setWindowModified(bool(count))
        self.setWindowTitle('星闻 · News Studio — 配置中心' + ('  · 未保存' if count else ''))
        enabled = self.enabled_channels()
        self.metrics['channels'].setText(f'{len(enabled)} 个')
        sources = len(self.rss_values()) + sum(toggle.isChecked() for toggle in self.source_toggles.values())
        self.metrics['sources'].setText(f'{sources} 个')
        self.metrics['time'].setText(self.editor_value('schedule.daily_report_time'))
        channel_names = {'qq': 'QQ', 'email': '邮箱', 'telegram': 'Telegram'}
        self.channel_summary.setText('  /  '.join(channel_names.get(name, name) for name in enabled) if enabled else '尚未启用任何推送渠道')

    def save(self):
        changes = self.changes()
        if not changes:
            return True
        try:
            self.store.save(changes)
            self.populate()
            self.save_status.setText('已保存 · 运行中服务约 1 秒内感知 · 新请求使用新配置 · .bak 不含私密信息')
            return True
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, '请检查配置', str(exc))
            return False

    def allow_discard(self):
        if not self.changes():
            return True
        answer = QMessageBox.question(
            self, '还有未保存的更改', '是否保存当前配置？',
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def reload(self):
        if not self.allow_discard():
            return
        try:
            data = self.store.read()
            if not isinstance(get_value(data, 'sources.rss', []), list):
                raise ValueError('RSS 来源必须是列表')
            self.store.data = data
            self.populate()
            self.save_status.setText('已重新载入本地配置')
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, '无法重新载入', str(exc))

    def check_connection(self):
        if self.probe is not None:
            return
        base = self.editor_value('push.qq.http_base')
        from urllib.parse import urlparse
        try:
            parsed = urlparse(base)
            valid = (parsed.scheme in ('http', 'https') and bool(parsed.hostname)
                     and not parsed.username and not parsed.password
                     and (parsed.port is None or 1 <= parsed.port <= 65535))
        except ValueError:
            valid = False
        if not valid:
            self.probe_status.setText('请先填写完整的 HTTP 服务地址')
            return
        self.probe_button.setEnabled(False)
        self.probe_status.setText('正在检查连接…')
        self.probe = ConnectionProbe(base, self.editor_value('push.qq.access_token'))
        self.probe.signals.done.connect(self.probe_finished)
        QThreadPool.globalInstance().start(self.probe)

    def probe_finished(self, success, message):
        self.probe_status.setText(message)
        self.probe_status.setStyleSheet('color: #267b56;' if success else 'color: #a66c37;')
        self.probe_button.setEnabled(True)
        self.probe = None

    def log_directory(self):
        # The existing logger writes relative to the project, even for --config elsewhere.
        return Path(__file__).resolve().parents[2] / 'logs'

    def refresh_logs(self):
        if self.stack.currentIndex() != 5:
            return
        path = self.log_directory() / 'news_agent.log'
        try:
            with path.open('rb') as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - 48000))
                chunk = handle.read()
            lines = chunk.decode('utf-8', errors='replace').splitlines()[-160:]
            text = '\n'.join(lines) or '暂无日志。后台服务运行后会在这里显示记录。'
        except FileNotFoundError:
            text = '暂无日志。后台服务运行后会在这里显示记录。'
        except OSError:
            text = '暂时无法读取日志文件，请稍后刷新。'
        # Mask configured secrets even if an older backend version wrote them to logs.
        for path, (_, kind, _) in self.fields.items():
            if kind == 'secret':
                for secret in (self.editor_value(path), get_value(self.store.data, path, '')):
                    if secret:
                        text = text.replace(str(secret), '••••••')
        if text != self.log_view.toPlainText():
            self.log_view.setPlainText(text)
            self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def open_logs(self):
        directory = self.log_directory()
        directory.mkdir(exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def closeEvent(self, event):
        if not self.allow_discard():
            event.ignore()
            return
        self.stop_monitoring()
        if self.probe is not None:
            # Wait only for the bounded six-second local connection check.
            self.probe.signals.done.disconnect(self.probe_finished)
        event.accept()
