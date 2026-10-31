# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

"""Audit project privacy without printing credentials; --clean resets local state.

No upload or network request is performed. Installed dependency binaries are not
rewritten. Reports include paths/categories/counts only, never matched values.
"""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from news_agent.factory_defaults import fresh_config
from news_agent.privacy import private_values, redact_text, is_private_key


TEXT_SUFFIXES = {'.json','.py','.md','.txt','.log','.bat','.ps1','.toml','.yaml','.yml','.ini','.config','.xml','.js','.mjs','.ts','.html','.csv'}
SKIP_PARTS = {'.venv','.runtime','node_modules','.git'}
KEY_VALUE = re.compile(r'''["']?(api[_-]?key|password|access[_-]?token|bot[_-]?token|smtp[_-]?password|authorization[_-]?code)["']?\s*[:=]\s*["']([^\r\n"']{5,})["']''', re.I)
EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')


def files(root):
    for base, directories, names in os.walk(root, followlinks=False):
        directories[:] = [name for name in directories if name not in SKIP_PARTS and not (Path(base)/name).is_symlink()]
        for name in names:
            path=Path(base)/name
            if not path.is_symlink():
                yield path


def text_file(path):
    return path.suffix.lower() in TEXT_SUFFIXES and path.stat().st_size < 8*1024*1024


def collect_private(root):
    values=set()
    for path in files(root):
        relative=path.relative_to(root)
        if relative.parts[0] == 'tests':
            continue
        if path.suffix.lower()=='.json' and ('config' in str(relative).lower() or 'AppData' in relative.parts):
            try:
                values.update(private_values(json.loads(path.read_text(encoding='utf-8-sig'))))
            except (OSError,ValueError,UnicodeError):
                pass
        if relative.parts[0] in {'news_agent','scripts'} and path.suffix.lower()=='.py' and path.name not in {'privacy.py','privacy_audit.py'}:
            for _, value in KEY_VALUE.findall(path.read_text(encoding='utf-8-sig',errors='replace')):
                if not any(token in value.lower() for token in ('fake','test','example','placeholder','your','{','get(','cfg','config')) and not value.startswith('你的'):
                    values.add(value)
        match=re.search(r'(?:napcat|onebot11|protocol)_(\d{5,12})',path.name)
        if match:
            values.add(match.group(1))
    return {value for value in values if len(value)>=5}


def safe_path(path, root):
    return re.sub(r'(?<!\d)\d{5,12}(?!\d)','<id>',str(path.relative_to(root)))


def audit(root, values):
    findings=[]
    for path in files(root):
        relative=path.relative_to(root)
        if '__pycache__' in relative.parts:
            continue
        if text_file(path):
            content=path.read_bytes()
            count=sum(content.count(value.encode('utf-8'))+content.count(value.encode('utf-16-le')) for value in values)
            if count:
                findings.append({'file':safe_path(path,root),'category':'known_private_value','matches':count})
        if relative.parts[0]=='steam++' and len(relative.parts)>1 and relative.parts[1] in {'AppData','Cache'}:
            findings.append({'file':safe_path(path,root),'category':'application_user_state','matches':1})
        if relative.parts[0]=='NapCat.Shell.Windows.Node' and ('config' in relative.parts or 'cache' in relative.parts or '.db' in path.name):
            findings.append({'file':safe_path(path,root),'category':'QQ_user_state','matches':1})
    return findings


def _remove_target(path, root):
    root=root.resolve()
    resolved=path.resolve()
    if not resolved.is_relative_to(root) or resolved==root or path.is_symlink():
        raise ValueError('清理目标超出项目范围')
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def clean(root, values):
    changed=[]
    # Run only after project workers/UI are stopped. Do not retain another copy
    # of the private originals in a .bak, zip or exported report.
    config_bytes=(json.dumps(fresh_config(),ensure_ascii=False,indent=2)+'\n').encode()
    for name in ('config.json','config.example.json'):
        (root/name).write_bytes(config_bytes)
        changed.append(name)
    for path in list(root.glob('config.json.*')):
        if path.name!='config.json.lock':
            _remove_target(path,root)
            changed.append(safe_path(path,root))
    for relative in ('logs','steam++/AppData','steam++/Cache','NapCat.Shell.Windows.Node/napcat/config','NapCat.Shell.Windows.Node/napcat/cache'):
        path=root/relative
        if path.exists():
            _remove_target(path,root)
            changed.append(relative)
    (root/'logs').mkdir(exist_ok=True)
    for path in list(files(root)):
        relative=path.relative_to(root)
        if path.name.startswith('news-before-') and path.suffix=='.db':
            _remove_target(path,root)
            changed.append(safe_path(path,root))
            continue
        if relative.parts[0]=='NapCat.Shell.Windows.Node' and ('.db' in path.name or path.suffix.lower()=='.log'):
            _remove_target(path,root)
            changed.append(safe_path(path,root))
            continue
        if '__pycache__' in relative.parts:
            _remove_target(path,root)
            continue
        if not text_file(path) or relative.parts[0]=='tests' or path.name=='privacy_audit.py':
            continue
        content=path.read_text(encoding='utf-8-sig',errors='replace')
        original=content
        for value in sorted(values,key=len,reverse=True):
            content=content.replace(value,'0' if value.isdigit() else '')
        if content!=original:
            # Preserve the BOM needed by Windows PowerShell 5.1.
            encoding='utf-8-sig' if path.suffix.lower()=='.ps1' else 'utf-8'
            path.write_text(content,encoding=encoding)
            changed.append(safe_path(path,root))
    # News itself is public, so preserve the cache while removing any private
    # text accidentally included in it. VACUUM rebuilds SQLite free pages.
    for path in (root/'data').glob('*.db'):
        with sqlite3.connect(path) as db:
            for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
                quoted='"'+table.replace('"','""')+'"'
                info=db.execute(f'PRAGMA table_info({quoted})').fetchall()
                columns=[row[1] for row in info if str(row[2]).upper() in {'TEXT','VARCHAR'}
                         and row[1] not in {'url_hash','url_key','title_key','title_prefix','content_key','identity'}]
                for column in columns:
                    col='"'+column.replace('"','""')+'"'
                    for rowid,value in db.execute(f'SELECT rowid,{col} FROM {quoted} WHERE {col} IS NOT NULL').fetchall():
                        if isinstance(value,str):
                            safe=value
                            for private in values:
                                safe=safe.replace(private,'[私密信息已清理]')
                            if safe!=value:
                                db.execute(f'UPDATE {quoted} SET {col}=? WHERE rowid=?',(safe,rowid))
            db.commit()
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.execute('VACUUM')
        changed.append(safe_path(path,root)+' (text scrub + vacuum)')
    return changed


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clean',action='store_true',help='Reset private local configuration and application profiles')
    parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args(argv)
    root=args.root.resolve()
    if args.clean:
        try:
            from news_agent.desktop.services import ServiceManager,_arguments,_normal
            manager=ServiceManager(root,root/'config.json')
            inventory=manager._inventory()
            active=[p for p in inventory['processes'] if manager._is_backend(p)]
            active.extend(manager._napcat_processes(inventory['processes']))
            active.extend(manager._steam_processes(inventory['processes'],{}))
            for process in inventory['processes']:
                if str(process.get('name','')).lower() in {'python.exe','pythonw.exe'}:
                    args_list=_arguments(process.get('command',''))
                    if any(_normal(value) in {_normal(root/'config_ui.py'),_normal('config_ui.py')} for value in args_list[1:]):
                        active.append(process)
            if active:
                print('清理未执行：请先关闭项目界面、后台、NapCat 和 Steam++，避免旧内存配置重新写回。')
                return 2
        except Exception:
            print('清理未执行：无法确认运行状态，请关闭相关应用后重试。')
            return 2
    values=collect_private(root)
    before=audit(root,values)
    print(json.dumps({'phase':'audit','files_with_findings':len({x['file'] for x in before}),
                      'categories':dict(Counter(x['category'] for x in before)),
                      'findings':before[:80]},ensure_ascii=False,indent=2))
    if args.clean:
        changed=clean(root,values)
        after=audit(root,values)
        print(json.dumps({'phase':'clean','changed':changed,'remaining_findings':after,
                          'note':'No credentials were copied to a backup or printed.'},ensure_ascii=False,indent=2))
        return 1 if after else 0
    return 0


if __name__=='__main__':
    raise SystemExit(main())
