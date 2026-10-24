@echo off
chcp 936 >nul
rem ========================================
rem  目标机修复脚本 v2
rem 修复：日报bug + 代理配置 + QQ管理 + 代码完整性
rem ========================================

set PROJECT_DIR=%~dp0..
cd /d %PROJECT_DIR%

echo ========================================
echo  新闻推送系统 - 目标机修复脚本 v2
echo ========================================
echo.

rem 1. 修复 qq_bot.py 的 cfg -> self.cfg bug
echo [1/5] 修复 qq_bot.py...
python -X utf8 -c "
lines = open(r'D:\MYSTRICPROJECT\news_agent\qq_bot.py', encoding='utf-8').readlines()
changed = 0
for i, line in enumerate(lines):
    if 'cfg.chat_cfg()' in line:
        lines[i] = line.replace('cfg.', 'self.cfg.', 1)
        changed += 1
    elif 'LLMClient(cfg,' in line and 'self.cfg' not in line:
        lines[i] = line.replace('LLMClient(cfg,', 'LLMClient(self.cfg,', 1)
        changed += 1
if changed > 0:
    open(r'D:\MYSTRICPROJECT\news_agent\qq_bot.py', 'w', encoding='utf-8').write(''.join(lines))
    print('  修复 %d 处' % changed)
else:
    print('  无需修复')
"

rem 2. 修复 config.json 代理配置
echo [2/5] 设置 GitHub 代理...
python -X utf8 -c "
import json
cfg = json.load(open(r'D:\MYSTRICPROJECT\config.json', encoding='utf-8'))
old = cfg.get('proxy', '')
cfg['proxy'] = 'http://127.0.0.1:26561'
json.dump(cfg, open(r'D:\MYSTRICPROJECT\config.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print('  proxy: %s -> %s' % (old, cfg['proxy']))
"

rem 3. 检查代码完整性
echo [3/5] 检查代码完整性...
python -X utf8 -c "
import os
base = r'D:\MYSTRICPROJECT'
checks = [
    ('llm.py: AgnesProvider', 'class AgnesProvider' in open(os.path.join(base,'news_agent','llm.py'),encoding='utf-8').read()),
    ('llm.py: 无DeepSeek', 'class DeepSeekProvider' not in open(os.path.join(base,'news_agent','llm.py'),encoding='utf-8').read()),
    ('processor.py: RateLimiter', '_RateLimiter' in open(os.path.join(base,'news_agent','processor.py'),encoding='utf-8').read()),
    ('dedupe.py: 实体去重', 'dedup_same_event' in open(os.path.join(base,'news_agent','dedupe.py'),encoding='utf-8').read()),
    ('qq_bot.py: use_chat', 'use_chat' in open(os.path.join(base,'news_agent','qq_bot.py'),encoding='utf-8').read()),
    ('config.json: chat节', '\"chat\"' in open(os.path.join(base,'config.json'),encoding='utf-8').read()),
    ('config.json: 代理', '26561' in open(os.path.join(base,'config.json'),encoding='utf-8').read()),
]
ok = True
for name, r in checks:
    s = 'OK' if r else 'FAIL'
    if not r: ok = False
    print('  [%s] %s' % (s, name))
print()
if ok:
    print('  全部通过')
else:
    print('  有失败项，请检查')
"

rem 4. 检查 NapCat 状态
echo [4/5] 检查 NapCat...
python -X utf8 -c "
try:
    import requests
    r = requests.get('http://127.0.0.1:3000/get_login_info', timeout=5)
    data = r.json()
    if data.get('status') == 'ok':
        user_id = data.get('data', {}).get('user_id', 'unknown')
        nickname = data.get('data', {}).get('nickname', 'unknown')
        print('  [OK] NapCat已登录: %s (%s)' % (nickname, user_id))
    else:
        print('  [WARN] NapCat未登录: %s' % data)
except Exception as e:
    print('  [FAIL] NapCat连接失败: %s' % e)
"

rem 5. 测试 LLM API
echo [5/5] 测试 LLM API...
python -X utf8 -c "
try:
    from news_agent.config import load_config
    from news_agent.llm import LLMClient
    cfg = load_config(r'D:\MYSTRICPROJECT\config.json')
    push_client = LLMClient(load_config(r'D:\MYSTRICPROJECT\config.json'))
    r = push_client.complete('hi', 'respond: ok')
    print('  [OK] 推送API: %s -> %s' % (push_client.last_provider, r.strip()[:30]))
    chat_client = LLMClient(load_config(r'D:\MYSTRICPROJECT\config.json'), use_chat=True)
    r = chat_client.complete('hi', 'respond: ok')
    print('  [OK] 聊天API: %s -> %s' % (chat_client.last_provider, r.strip()[:30]))
except Exception as e:
    print('  [FAIL] LLM测试失败: %s' % e)
"

echo.
echo ========================================
echo 修复检查完成
echo ========================================
echo.
echo 请重启服务:
echo   sc stop news-agent
echo   sc start news-agent
echo.
echo 添加新QQ号:
echo   python D:\MYSTRICPROJECT\scripts\manage_qq.py
echo.
pause