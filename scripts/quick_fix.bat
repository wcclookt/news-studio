@echo off
chcp 936 >nul
rem ========================================
rem  目标机快速修复 (已有代码，仅修复配置)
rem ========================================

echo [1/3] 修复 qq_bot.py...
python -X utf8 -c "
lines = open(r'D:\MYSTRICPROJECT\news_agent\qq_bot.py', encoding='utf-8').readlines()
changed = 0
for i, line in enumerate(lines):
    if 'cfg.chat_cfg()' in line:
        lines[i] = line.replace('cfg.', 'self.cfg.', 1)
    elif 'LLMClient(cfg,' in line and 'self.cfg' not in line:
        lines[i] = line.replace('LLMClient(cfg,', 'LLMClient(self.cfg,', 1)
if changed > 0:
    open(r'D:\MYSTRICPROJECT\news_agent\qq_bot.py', 'w', encoding='utf-8').write(''.join(lines))
    print('  修复 %d 处' % changed)
else:
    print('  无需修复')
"

echo.
echo [2/3] 设置代理...
python -X utf8 -c "
import json
cfg = json.load(open(r'D:\MYSTRICPROJECT\config.json', encoding='utf-8'))
cfg['proxy'] = 'http://127.0.0.1:26561'
json.dump(cfg, open(r'D:\MYSTRICPROJECT\config.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print('  proxy 已设置为 http://127.0.0.1:26561')
"

echo.
echo [3/3] 重启服务...
sc stop news-agent
timeout /t 3 /nobreak >nul
sc start news-agent
echo 完成
pause