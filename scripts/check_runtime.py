"""Read-only checks used before the Windows desktop launcher provisions anything.

Run with ``--probe`` for interpreter compatibility or ``--dependencies`` for
installed requirement versions, imports, and a separate native Qt smoke check.
No pip installation or application configuration is performed here.
"""

import argparse
import hashlib
import importlib
import importlib.util
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import sysconfig


MIN_PYTHON = (3, 11)
MAX_PYTHON = (3, 16)
QT_DISTRIBUTION = 'PySide6-Essentials'
REQUIRED_IMPORTS = (
    ('requests', 'requests'), ('feedparser', 'feedparser'),
    ('beautifulsoup4', 'bs4'), ('lxml', 'lxml.etree'),
    ('PySocks', 'socks'), ('websocket-client', 'websocket'),
    (QT_DISTRIBUTION, 'PySide6.QtCore'), (QT_DISTRIBUTION, 'PySide6.QtGui'),
    (QT_DISTRIBUTION, 'PySide6.QtWidgets'), (QT_DISTRIBUTION, 'PySide6.QtSvg'),
)


def interpreter_report():
    errors = []
    is_venv = sys.prefix != sys.base_prefix
    if os.name != 'nt':
        errors.append('此启动器适用于 Windows 10/11 x64。')
    if sys.implementation.name != 'cpython':
        errors.append('需要官方 CPython，当前解释器不是 CPython。')
    if not MIN_PYTHON <= sys.version_info[:2] < MAX_PYTHON:
        errors.append('需要 Python 3.11～3.15；启动器可自动安装固定版本 3.13.16。')
    if struct.calcsize('P') != 8 or platform.machine().lower() not in ('amd64', 'x86_64'):
        errors.append('需要 Windows x64 Python，32 位和 ARM64 解释器不适用此启动流程。')
    if sysconfig.get_config_var('Py_GIL_DISABLED'):
        errors.append('需要标准 CPython，暂不支持 free-threaded / t 版本。')
    if not is_venv and (importlib.util.find_spec('venv') is None or importlib.util.find_spec('ensurepip') is None):
        errors.append('需要包含 venv 和 ensurepip 的完整 Python，当前精简版解释器无法建立项目环境。')
    if os.name == 'nt' and hasattr(sys, 'getwindowsversion') and sys.getwindowsversion().major < 10:
        errors.append('当前 Qt 桌面环境需要 Windows 10 或 Windows 11。')
    return {
        'ok': not errors, 'errors': errors,
        'executable': sys.executable, 'version': platform.python_version(),
        'bits': struct.calcsize('P') * 8,
        'venv': is_venv,
    }


def read_requirements(path):
    """Return requirement lines and a hash including every local -r include."""
    digest = hashlib.sha256()
    collected, visited = [], set()

    def visit(source):
        source = Path(source).resolve()
        if source in visited:
            return
        visited.add(source)
        content = source.read_bytes()
        digest.update(source.name.encode('utf-8'))
        digest.update(b'\0')
        digest.update(content)
        for original in content.decode('utf-8-sig').splitlines():
            line = original.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('-r ') or line.startswith('--requirement '):
                include = line.split(None, 1)[1].strip().strip('"\'')
                visit(source.parent / include)
            elif line.startswith('-'):
                raise ValueError('requirements 包含启动器不支持的 pip 选项，请人工检查。')
            else:
                collected.append(line.split(' #', 1)[0].strip())

    visit(path)
    return collected, digest.hexdigest()


def dependency_report(requirements, run_qt=True):
    report = interpreter_report()
    report.update(missing=[], broken=[], requirements_valid=True, requirements_hash='',
                  qt_ok=False, missing_vc_runtime=[])
    if not report['ok']:
        return report
    try:
        lines, fingerprint = read_requirements(requirements)
        report['requirements_hash'] = fingerprint
        # pip ships packaging; no additional module is needed to inspect the
        # complete requirement specifiers before installing project packages.
        from pip._vendor.packaging.requirements import Requirement
        for line in lines:
            requirement = Requirement(line)
            if requirement.marker and not requirement.marker.evaluate():
                continue
            try:
                installed = metadata.version(requirement.name)
            except metadata.PackageNotFoundError:
                report['missing'].append(requirement.name)
                continue
            if requirement.specifier and not requirement.specifier.contains(installed, prereleases=True):
                report['missing'].append(requirement.name)
    except ImportError:
        report['missing'].append('pip')
    except (OSError, UnicodeError, ValueError):
        report['requirements_valid'] = False
        report['errors'].append('无法解析 requirements-ui.txt 或其引用文件，请检查项目文件是否完整。')
        report['ok'] = False
        return report

    for distribution, module in REQUIRED_IMPORTS:
        try:
            importlib.import_module(module)
        except Exception:
            if distribution not in report['broken']:
                report['broken'].append(distribution)
    if run_qt and QT_DISTRIBUTION not in report['broken']:
        try:
            child = subprocess.run(
                [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--qt-smoke'],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=25,
            )
            report['qt_ok'] = child.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            report['qt_ok'] = False
        if not report['qt_ok']:
            report['broken'].append(QT_DISTRIBUTION)
            report['errors'].append('Qt DLL 或 Windows 平台插件加载失败。请检查杀毒隔离、显卡驱动和系统运行库。')
    elif not run_qt:
        report['qt_ok'] = True
    if os.name == 'nt' and not report['qt_ok']:
        import ctypes
        for library in ('vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll', 'msvcp140_2.dll'):
            try:
                ctypes.WinDLL(library)
            except OSError:
                report['missing_vc_runtime'].append(library)
    report['ok'] = not report['missing'] and not report['broken'] and report['qt_ok']
    return report


def qt_smoke():
    """Create a hidden native Windows widget in its own bounded subprocess."""
    from PySide6.QtCore import QLibraryInfo
    from PySide6.QtWidgets import QApplication, QWidget
    plugin = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)) / 'platforms' / 'qwindows.dll'
    if os.name == 'nt' and not plugin.is_file():
        return 1
    app = QApplication(['News Studio runtime check', '-platform', 'windows'])
    widget = QWidget()
    widget.setWindowTitle('News Studio runtime check')
    app.processEvents()
    widget.close()
    return 0 if app.platformName() == 'windows' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--probe', action='store_true')
    mode.add_argument('--dependencies', action='store_true')
    mode.add_argument('--qt-smoke', action='store_true')
    parser.add_argument('--requirements', type=Path, default=Path(__file__).resolve().parents[1] / 'requirements-ui.txt')
    args = parser.parse_args(argv)
    if args.qt_smoke:
        try:
            return qt_smoke()
        except Exception:
            return 1
    report = dependency_report(args.requirements) if args.dependencies else interpreter_report()
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
