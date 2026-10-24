"""Launch the News Studio desktop configuration editor."""

import argparse
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description='星闻 · 新闻助手配置中心')
    parser.add_argument('--config', default=str(Path(__file__).with_name('config.json')))
    args = parser.parse_args()
    try:
        from PySide6.QtGui import QFont, QIcon
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError:
        print('缺少 PySide6，请运行：python -m pip install -r requirements-ui.txt')
        return 1
    from news_agent.desktop.window import ConfigWindow
    from news_agent.desktop.theme import STYLE, make_icon
    from news_agent import __author__
    app = QApplication(sys.argv)
    app.setApplicationName('星闻 · News Studio')
    app.setOrganizationName(__author__)
    font = QFont('Microsoft YaHei UI', 10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    app.setWindowIcon(QIcon(make_icon('spark', '#14846b', 48)))
    try:
        window = ConfigWindow(args.config)
    except (OSError, ValueError) as exc:
        QMessageBox.critical(None, '无法打开配置', str(exc))
        return 1
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
