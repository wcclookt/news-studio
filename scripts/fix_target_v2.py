# -*- coding: utf-8 -*-
"""
目标机修复脚本 v2
修复内容：
1. 日报bug：run_daily加异常保护 + 包含所有今日条目（含已推送的）+ 标注推送状态
2. 调度器：日报时间窗口放宽到±5分钟
3. 报告：日报条目显示"[已推送]"标记
"""
import os
import sys

BASE = r"D:\MYSTRICPROJECT"
sys.path.insert(0, BASE)

print("=" * 50)
print(" 新闻推送系统 - 目标机修复脚本 v2")
print("=" * 50)
print()

# 1. 检查关键文件是否存在且版本正确
print("[1/5] 检查代码版本...")
checks = []

def check_file(path, name, checks_list):
    if not os.path.exists(path):
        checks_list.append((name, False, "文件不存在"))
        return
    content = open(path, encoding="utf-8").read()
    for label, pattern in checks_list:
        if pattern in content:
            print(f"  [OK] {name}: {label}")
        else:
            print(f"  [FAIL] {name}: 缺少 {label}")

check_file(os.path.join(BASE, "news_agent/orchestrator.py"), "orchestrator.py", [
    ("today_all_entries", "today_all_entries"),
    ("fallback_daily_plain", "_fallback_daily_plain"),
    ("run_daily try-except", "try:"),
])
check_file(os.path.join(BASE, "news_agent/scheduler.py"), "scheduler.py", [
    ("tolerance_minutes", "tolerance_minutes"),
])
check_file(os.path.join(BASE, "news_agent/report.py"), "report.py", [
    ("pushed标记", "pushed"),
])
check_file(os.path.join(BASE, "news_agent/llm.py"), "llm.py", [
    ("AgnesProvider", "class AgnesProvider"),
    ("无DeepSeek", "class DeepSeekProvider"),
])

print()

# 2. 检查config.json配置
print("[2/5] 检查config.json...")
import json
cfg_path = os.path.join(BASE, "config.json")
try:
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    print(f"  proxy: {cfg.get('proxy', '未设置')}")
    print(f"  private_qq: {cfg.get('push', {}).get('qq', {}).get('private_qq', '未设置')}")
    print(f"  chat存在: {'chat' in cfg}")
    print(f"  llm.primary.api_key: {cfg.get('llm', {}).get('primary', {}).get('api_key', '')[:20]}...")
    print(f"  chat.primary.api_key: {cfg.get('chat', {}).get('primary', {}).get('api_key', '')[:20]}...")
except Exception as e:
    print(f"  [FAIL] config.json读取失败: {e}")

print()

# 3. 检查NapCat状态
print("[3/5] 检查NapCat...")
try:
    import requests
    r = requests.get("http://127.0.0.1:3000/get_login_info", timeout=5)
    data = r.json()
    if data.get("status") == "ok":
        user_id = data.get("data", {}).get("user_id", "unknown")
        nickname = data.get("data", {}).get("nickname", "unknown")
        print(f"  [OK] NapCat已登录: {nickname} ({user_id})")
    else:
        print(f"  [WARN] NapCat未登录: {data}")
except Exception as e:
    print(f"  [FAIL] NapCat连接失败: {e}")
    print("  请先启动NapCat并扫码登录")

print()

# 4. 测试LLM API
print("[4/5] 测试LLM API...")
try:
    from news_agent.config import load_config
    from news_agent.llm import LLMClient
    cfg = load_config(cfg_path)

    # 测试推送API
    push_client = LLMClient(cfg)
    r = push_client.complete("hi", "respond: ok")
    print(f"  [OK] 推送API: {push_client.last_provider} -> {r.strip()[:30]}")

    # 测试聊天API
    chat_client = LLMClient(cfg, use_chat=True)
    r = chat_client.complete("hi", "respond: ok")
    print(f"  [OK] 聊天API: {chat_client.last_provider} -> {r.strip()[:30]}")
except Exception as e:
    print(f"  [FAIL] LLM测试失败: {e}")

print()

# 5. 检查GitHub代理
print("[5/5] 检查GitHub代理...")
try:
    from news_agent.sources import github_trending
    items = github_trending.fetch_github_trending(proxy=cfg.proxy(), max_items=3, timeout=15)
    print(f"  GitHub Trending: {len(items)} 条")
    for i in items[:3]:
        print(f"    - {i.title[:40]}")
except Exception as e:
    print(f"  [WARN] GitHub抓取失败: {e}")
    print("  请确认Steam++已启动且代理端口正确")

print()
print("=" * 50)
print(" 修复检查完成")
print("=" * 50)
print()
print("如果所有检查通过，请重启服务:")
print("  sc stop news-agent")
print("  sc start news-agent")
print()
print("添加新QQ号:")
print("  python D:\\MYSTRICPROJECT\\scripts\\manage_qq.py")
print()
