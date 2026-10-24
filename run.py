import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from news_agent.config import load_config
from news_agent.logger import setup_logger
from news_agent.orchestrator import run_daily, run_realtime
from news_agent.scheduler import run_loop

setup_logger()


def main():
    parser = argparse.ArgumentParser(description="科技新闻采集与推送")
    parser.add_argument("--mode", choices=["service", "realtime", "daily", "test", "qqbot"], default="service")
    parser.add_argument("--with-qqbot", action="store_true", help="service 模式同时启动 QQ 交互机器人")
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()
    if args.mode == "service":
        run_loop(args.config, with_qqbot=True)
    elif args.mode == "qqbot":
        from news_agent.qq_bot import run_qq_bot

        run_qq_bot(args.config)
    else:
        cfg = load_config(args.config)
        if args.mode == "realtime":
            run_realtime(cfg)
        elif args.mode == "daily":
            run_daily(cfg)
        elif args.mode == "test":
            from news_agent.push.dispatcher import Dispatcher

            ok, failed = Dispatcher(cfg).send("测试消息", "这是来自新闻助手的一条测试推送。", kind="test")
            print(f"OK: {ok}, FAILED: {failed}")


if __name__ == "__main__":
    main()
