import json
import os
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)


def main():
    print("=" * 50)
    print(" news-agent 部署自检")
    print("=" * 50)

    ok = True

    try:
        import requests, feedparser, bs4, lxml
        print("[OK] Python 依赖已安装")
    except ImportError as e:
        print(f"[FAIL] 缺少依赖: {e}")
        print("      运行: python -m pip install -r requirements.txt")
        ok = False

    sys.path.insert(0, PROJECT)
    from news_agent.config import load_config

    cfg_path = os.path.join(PROJECT, "config.json")
    if not os.path.exists(cfg_path):
        print("[FAIL] 缺少 config.json")
        return
    cfg = load_config(cfg_path)
    print("[OK] config.json 存在")

    proxy = cfg.proxy()
    if proxy:
        host, port = proxy.split("://")[1].split(":")
        print(f"[测试代理] {proxy}")
        try:
            import socket

            s = socket.create_connection((host, int(port)), timeout=5)
            s.close()
            print(f"[OK] 代理 {host}:{port} 可连接")
        except Exception as e:
            print(f"[WARN] 代理 {host}:{port} 连接失败: {e}")
            print("      请确认目标机 v2rayN 已运行且本地监听端口一致")

    key = cfg.get("llm.primary.api_key", "")
    print(f"[INFO] Agnes API Key: {'已配置' if key and 'Key' not in key else '未配置/无效'}")

    from news_agent.llm import LLMClient

    try:
        client = LLMClient(cfg)
        text = client.complete("你是助手", "请只回复:ok")
        print(f"[OK] LLM 链路正常 (provider={client.last_provider}) -> {text.strip()[:30]}")
    except Exception as e:
        print(f"[FAIL] LLM 调用失败: {e}")
        ok = False

    push = cfg.push()
    enabled = push.get("enabled", [])
    print(f"[INFO] 已启用推送渠道: {enabled}")
    if "email" in enabled:
        un = push["email"].get("username", "")
        print(f"[INFO] 邮件发件: {un}  收件: {push['email'].get('to')}")
    if "telegram" in enabled:
        print(f"[INFO] TG chat_id: {push['telegram'].get('chat_id')}")
    if "qq" in enabled:
        print(f"[INFO] QQ http_base: {push['qq'].get('http_base')}  private: {push['qq'].get('private_qq')}")
        import socket

        try:
            host = push["qq"]["http_base"].split("//")[1].split(":")[0]
            port = int(push["qq"]["http_base"].split(":")[-1])
            s = socket.create_connection((host, port), timeout=5)
            s.close()
            print("[OK] NapCat HTTP 服务可连接")
        except Exception as e:
            print("[WARN] NapCat HTTP 不可连接（未运行或端口不同）: ", e)
            print("      确认 NapCat 已启动、HTTP 服务已开启、端口与 config 一致")

        try:
            import websocket
            print("[OK] websocket-client 依赖已安装（QQ 交互可用）")
        except ImportError:
            print("[FAIL] 缺少 websocket-client 依赖，QQ 交互不可用！")
            print("      运行: python -m pip install websocket-client -i https://pypi.tuna.tsinghua.edu.cn/simple")
            ok = False

        ws_port = int(push["qq"].get("ws_port", 3001))
        try:
            s = socket.create_connection(("127.0.0.1", ws_port), timeout=5)
            s.close()
            print(f"[OK] NapCat WebSocket 服务可连接（端口 {ws_port}，QQ 交互可接收消息）")
        except Exception as e:
            print(f"[WARN] NapCat WebSocket({ws_port}) 不可连接: {e}")
            print("      这是 QQ 交互（发消息问AI）不生效的常见原因。")
            print("      请确认: 1) NapCat 已登录 2) onebot11_你的QQ号.json 配置存在且含 websocketServers")

    print("=" * 50)
    print("自检完成")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
