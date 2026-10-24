"""One explicit, non-retried NapCat private test message."""

from urllib.parse import urlparse

import requests


TEST_MESSAGE = '你好，我是星星，这是一条测试连通性的测试信息，你可以忽略此信息。'


def send_test_message(base, token, qq):
    qq = str(qq).strip()
    if not qq.isdigit():
        return {'ok': False, 'message': '请填写一个有效的测试接收 QQ 号。'}
    try:
        parsed = urlparse(str(base))
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username or parsed.password
                or parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError('invalid address')
    except ValueError:
        return {'ok': False, 'message': 'NapCat HTTP 地址无效，请先检查地址。'}
    try:
        with requests.Session() as session:
            session.trust_env = False
            headers = {'Authorization': f'Bearer {token}'} if token else {}
            with session.post(
                str(base).rstrip('/') + '/send_private_msg',
                json={'user_id': qq, 'message': TEST_MESSAGE}, headers=headers,
                timeout=(3, 8), allow_redirects=False,
            ) as response:
                if response.status_code in {401, 403}:
                    return {'ok': False, 'message': 'NapCat 鉴权失败，请核对 Token。'}
                if response.status_code != 200:
                    return {'ok': False, 'message': f'NapCat 返回 HTTP {response.status_code}，请检查服务地址。'}
                payload = response.json()
        if (isinstance(payload, dict) and payload.get('status') == 'ok'
                and payload.get('retcode', 0) == 0
                and isinstance(payload.get('data'), dict)
                and payload['data'].get('message_id') is not None):
            return {'ok': True, 'message': f'NapCat 已接受发往 {qq} 的测试消息，请在对方 QQ 确认收到。'}
        return {'ok': False, 'message': 'NapCat 未确认发送成功，请检查 QQ 登录、好友关系和 NapCat 日志。'}
    except requests.Timeout:
        # Sending is not retried: the server may have accepted the first request.
        return {'ok': False, 'message': '发送结果未知：请求超时，请先查看对方 QQ，避免重复发送。'}
    except Exception:
        return {'ok': False, 'message': '无法完成发送，请检查 NapCat 服务、地址和登录状态。'}
