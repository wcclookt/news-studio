# -*- coding: utf-8 -*-
"""测试向指定QQ号发送消息，显示具体报错"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

def main():
    print("=" * 50)
    print("  QQ 推送测试工具")
    print("=" * 50)
    import json
    cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    qq_cfg = cfg.get("push", {}).get("qq", {})
    base = qq_cfg.get("http_base", "http://127.0.0.1:3000").rstrip("/")
    private_list = qq_cfg.get("private_qq", [])
    if isinstance(private_list, str):
        private_list = [x.strip() for x in private_list.split(",") if x.strip()]

    print("当前配置的推送 QQ 号:", private_list)
    print("NapCat 地址:", base)

    # 检查 NapCat 是否在线
    try:
        r = requests.post(f"{base}/get_login_info", json={}, timeout=5)
        info = r.json()
        if info.get("status") == "ok":
            print(f"[OK] NapCat 已登录: {info['data'].get('nickname')} ({info['data'].get('user_id')})")
        else:
            print("[错误] NapCat 未登录或异常:", info.get("message"))
            return
    except Exception as e:
        print("[错误] 无法连接 NapCat:", e)
        print("      请确认 NapCat 已启动并登录")
        return

    # 查询好友列表，检查配置的号是否都是好友
    try:
        r = requests.post(f"{base}/get_friend_list", json={}, timeout=10)
        friends = r.json().get("data", [])
        friend_ids = {str(f.get("user_id")): f.get("nickname", "") for f in friends}
        print("\n好友校验：")
        for qq in private_list:
            if qq in friend_ids:
                print(f"  [OK] {qq}（{friend_ids[qq]}）是好友")
            else:
                print(f"  [失败] {qq} 不是小号的好友！请先添加对方为好友")
    except Exception as e:
        print("查询好友失败:", e)

    # 逐个发送测试
    print("\n逐个发送测试：")
    for qq in private_list:
        try:
            r = requests.post(f"{base}/send_private_msg",
                              json={"user_id": str(qq), "message": "【测试】这是新闻助手的推送测试消息"},
                              timeout=10)
            data = r.json()
            if data.get("status") == "ok":
                print(f"  [OK] 已发送到 {qq}")
            else:
                print(f"  [失败] 发送到 {qq}: {data.get('message') or data.get('wording')}")
        except Exception as e:
            print(f"  [失败] 发送到 {qq}: {e}")

    print("\n提示：如果显示\"对方不是好友\"或\"发送失败\"，说明该号需满足：")
    print("  1. 对方也加了小号为好友（双向好友）")
    print("  2. 对方曾主动和小号聊过天（建立会话）")
    print("  3. 或者用小号主动给对方发一条消息（对方同意接收）")


if __name__ == "__main__":
    main()
