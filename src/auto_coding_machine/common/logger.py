import logging
import sys

_CONFIGURED = False
_FILE_HANDLERS = {}


def setup_logging():
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    _CONFIGURED = True


def get_logger(name):
    setup_logging()
    return logging.getLogger(name)


def add_file_handler(path):
    """给 root logger 挂一个文件 handler，和 stdout 同格式、同 level。

    为什么不在 setup_logging 里挂：
      setup_logging 是全局的，API、benchmark、worker import 任何模块都会触发。
      文件 handler 只想让 worker 进程写，所以只在工作进程入口显式调用。

    幂等：同一个 path 重复调用不会挂多个 handler。
    """
    setup_logging()  # 先确保 root 的 stdout handler 和 level 就位
    root = logging.getLogger()
    if path in _FILE_HANDLERS:
        return
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root.addHandler(handler)
    _FILE_HANDLERS[path] = handler
