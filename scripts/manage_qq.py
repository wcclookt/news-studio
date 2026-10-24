# -*- coding: utf-8 -*-
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from news_agent.config_io import atomic_update_json

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(d, original_qq_list):
    """Apply only the intended recipient changes to the latest configuration."""
    desired = get_qq_list(d)
    original = {str(qq) for qq in original_qq_list}
    additions = [qq for qq in desired if qq not in original]
    removals = original - set(desired)

    def update(latest):
        users = [qq for qq in get_qq_list(latest) if qq not in removals]
        users.extend(qq for qq in additions if qq not in users)
        set_qq_list(latest, users)
        return latest

    return atomic_update_json(CONFIG_PATH, update)


def get_qq_list(d):
    val = d.get("push", {}).get("qq", {}).get("private_qq", [])
    if isinstance(val, list):
        return list(dict.fromkeys(str(x) for x in val if str(x).isdigit()))
    if isinstance(val, str) and val:
        return list(dict.fromkeys(x for x in re.split(r'[,，;；\s]+', val) if x.isdigit()))
    return []


def set_qq_list(d, qq_list):
    d.setdefault("push", {}).setdefault("qq", {})["private_qq"] = qq_list


def napcat_http(d):
    qq = d.get("push", {}).get("qq", {})
    return qq.get("http_base", "http://127.0.0.1:3000").rstrip("/")


def get_friends(d):
    try:
        base = napcat_http(d)
        token = d.get('push', {}).get('qq', {}).get('access_token', '')
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        with requests.Session() as client:
            # Local bridge credentials should go directly to the configured
            # endpoint, never through an environment proxy or a redirect.
            client.trust_env = False
            with client.post(f'{base}/get_friend_list', json={}, headers=headers,
                             timeout=(3, 10), allow_redirects=False) as response:
                response.raise_for_status()
                if 300 <= response.status_code < 400:
                    raise ValueError('unexpected redirect')
                data = response.json()
        if isinstance(data, dict) and data.get('status') == 'ok':
            friends = data.get('data', [])
            if isinstance(friends, list) and all(isinstance(friend, dict) for friend in friends):
                return friends
        print('  [错误] NapCat 返回异常，请检查登录状态和接口鉴权。')
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        # HTTP errors and response bodies can contain the URL or token.
        print('  [错误] 无法读取 NapCat 好友列表，请检查地址、登录状态和鉴权 Token。')
    return None


def check_is_friend(d, qq_number):
    friends = get_friends(d)
    if friends is None:
        return None  # 无法确认
    qq = str(qq_number)
    for f in friends:
        if str(f.get("user_id")) == qq:
            nickname = f.get("nickname", "") or f.get("remark", "") or ""
            return True, nickname
    return False, ""


def show_list(qq_list):
    if not qq_list:
        print("  (当前没有推送 QQ 号)")
        return
    for i, qq in enumerate(qq_list, 1):
        mark = " ★" if i == 1 else ""
        print(f"  {i}. {qq}{mark}")


def main():
    d = load_config()
    qq_list = get_qq_list(d)
    while True:
        print("=" * 46)
        print("  推送 QQ 号管理")
        print("=" * 46)
        print("  当前推送的 QQ 号：")
        show_list(qq_list)
        print("-" * 46)
        print("  1. 添加 QQ 号")
        print("  2. 删除 QQ 号")
        print("  3. 退出")
        print("-" * 46)
        choice = input("  请输入选项(1/2/3): ").strip()
        if choice == "1":
            qq = input("  请输入要添加的 QQ 号: ").strip()
            if not qq.isdigit():
                print("  [提示] QQ 号格式不对，请输入纯数字。")
                continue
            if qq in qq_list:
                print(f"  [提示] {qq} 已在列表中。")
                continue
            print("  正在检查是否是小号的好友...")
            result = check_is_friend(d, qq)
            if result is None:
                print("  [错误] 无法连接 NapCat，请确认 NapCat 已启动并登录。")
                continue
            ok, nickname = result
            if ok:
                original_qq_list = list(qq_list)
                qq_list.append(qq)
                set_qq_list(d, qq_list)
                try:
                    d = save_config(d, original_qq_list)
                    qq_list = get_qq_list(d)
                except (OSError, ValueError):
                    qq_list = original_qq_list
                    set_qq_list(d, qq_list)
                    print("  [错误] 无法保存 QQ 号，请检查配置文件后重试。")
                    continue
                name = nickname or qq
                print(f"  [成功] {qq}（{name}）已是好友，已添加！")
            else:
                print(f"  [失败] {qq} 不是小号的好友。")
                print("         请先在 QQ 里添加对方为好友，再重新操作。")
        elif choice == "2":
            if not qq_list:
                print("  [提示] 当前没有可删除的 QQ 号。")
                continue
            print("  当前推送的 QQ 号：")
            show_list(qq_list)
            sel = input("  请输入序号 或 直接输入QQ号: ").strip()
            original_qq_list = list(qq_list)
            removed = None
            if sel in qq_list:
                # 直接命中 QQ 号
                removed = sel
                qq_list.remove(sel)
            elif sel.isdigit():
                # 尝试按序号
                idx = int(sel) - 1
                if 0 <= idx < len(qq_list):
                    removed = qq_list.pop(idx)
            if removed is not None:
                set_qq_list(d, qq_list)
                try:
                    d = save_config(d, original_qq_list)
                    qq_list = get_qq_list(d)
                except (OSError, ValueError):
                    qq_list = original_qq_list
                    set_qq_list(d, qq_list)
                    print("  [错误] 无法保存 QQ 号，请检查配置文件后重试。")
                    continue
                print(f"  [成功] 已删除 {removed}")
            else:
                print("  [提示] 未找到匹配的序号或 QQ 号。")
        elif choice == "3":
            print("  再见！")
            break
        else:
            print("  [提示] 无效选项，请输入 1/2/3。")
        print()


if __name__ == "__main__":
    main()
