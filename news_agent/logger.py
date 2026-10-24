import logging
import os
from logging.handlers import RotatingFileHandler
from .privacy import PrivacyLogFilter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(BASE_DIR, "logs")


def setup_logger(name="news_agent"):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    os.makedirs(LOG_DIR, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = RotatingFileHandler(
        os.path.join(LOG_DIR, "news_agent.log"),
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    fh.addFilter(PrivacyLogFilter())
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.addFilter(PrivacyLogFilter())
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def get_logger():
    return setup_logger()
