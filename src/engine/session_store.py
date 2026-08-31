"""Session 持久化：把对话历史逐行写进 JSONL 文件。

【这文件是干什么的】
  Coding Agent 的对话历史（history）以前只存在内存里，关掉程序就丢。
  本模块把每条消息追加写入一个 JSONL 文件（一行一条 JSON），
  下次启动可以读回来接着聊（resume）。

【什么是 JSONL】
  就是纯文本文件，每行是一条独立的 JSON。像记流水账：
    {"role": "user", "content": "帮我看看 cli.py"}
    {"role": "assistant", "content": "好的，我看了..."}
  追加写天然抗崩溃：哪怕写到一半断电，前面的行都还在。

【设计约定】（见 docs/adr/0001、0002）
  - 一次 Session 一个文件：.autocoding/sessions/<session_id>.jsonl
  - 只存原始 history（user / assistant / tool 消息），
    不存 system prompt、不存 injections、不存 compact 摘要
  - 本模块是消息历史的唯一真相源
  - 不建索引文件、不自动删除旧 Session

【谁会用】
  - MachineLoop：产生消息时逐条落盘
  - cli.py：--resume / /sessions 命令
"""

import json
import time
import uuid
from pathlib import Path
from filelock import FileLock  # ★ 新增：文件级互斥锁

# 悬空 tool_calls 修复要造"被中断"的回执，见文件末尾 repair_dangling_tool_results
from src.engine.contracts import ToolResult


# session 文件统一放在这个子目录（相对某个工作区）
SESSIONS_DIR_NAME = ".autocoding/sessions"


def sessions_dir_for(workspace) -> Path:
    """返回某个工作区的 session 存放目录（不创建）。

    参数：
      workspace — 工作区根目录（str 或 Path）

    返回：
      Path，形如 <workspace>/.autocoding/sessions
    """
    return Path(workspace) / SESSIONS_DIR_NAME


def new_session_id() -> str:
    """生成一个新的 session id：时间戳 + 短随机串。

    形如 "20260727-153042-a1b2c3"。
    时间戳在前，按文件名排序就是按时间排序，人眼也能看懂。
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    short = uuid.uuid4().hex[:6]
    return stamp + "-" + short


class SessionStore:
    """一个 Session 的读写器（对应一个 JSONL 文件）。

    用法例子：
        store = SessionStore(sessions_dir_for(workspace), "20260727-153042-a1b2c3")
        store.append({"role": "user", "content": "你好"})
        history = store.load()
    """

    def __init__(self, sessions_dir, session_id: str):
        """初始化。

        参数：
          sessions_dir — session 文件所在目录（str 或 Path）
          session_id   — 本次会话的 id（也是文件名主体）

        注意：这里不创建文件也不创建目录（懒创建），
        第一次 append 时才真正写盘 —— 没说过话的会话不留空文件。
        """
        self.session_id = session_id
        self.path = Path(sessions_dir) / (session_id + ".jsonl")

    def append(self, message: dict):
        """追加一条消息到文件末尾（一行一条 JSON）。

        参数：
          message — 消息 dict，比如 {"role": "user", "content": "..."}

        每次调用都是 打开 → 追加一行 → 关闭，
        不持有文件句柄，程序崩溃最多丢正在写的这一条。
        """
        # 懒创建目录：第一次写的时候才建
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(message, ensure_ascii=False) + "\n"

        # ★ 文件级互斥锁（同一时刻只允许一个进程/线程写）
        # Windows/Linux/macOS跨平台兼容，timeout=10 防止死锁
        lock_path = str(self.path) + ".lock"
        lock = FileLock(lock_path, timeout=10)

        with lock:  # ★ 加锁后写入
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)

    def load(self) -> list:
        """读回全部历史消息。

        返回：
          消息列表（和 append 进来的顺序一致）。
          文件不存在时返回空列表。
          坏行（比如崩溃时写了半截的 JSON）直接跳过，不报错。
        """
        if not self.path.is_file():
            return []
        history = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    history.append(json.loads(line))
                except json.JSONDecodeError:
                    # 半截行：多半是上次崩溃留下的，跳过就好
                    continue
        return history

    def overwrite(self, messages: list):
        """用新消息列表整体覆盖 JSONL（compact 压缩后重写用）。

        参数：
          messages — 新的完整消息列表（只应含原始 history，不含 system/injections）

        先写临时文件再原子替换，防止写一半崩溃把原文件弄坏。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for m in messages:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
        tmp.replace(self.path)


def list_sessions(sessions_dir) -> list:
    """扫描目录，列出所有 Session（新的在前）。

    参数：
      sessions_dir — session 文件所在目录（str 或 Path）

    返回：
      [{"id": str, "title": str, "mtime": float}, ...]
      按文件修改时间从新到旧排序。
      title 取首条 user 消息的前 50 字（没有就显示 "(空会话)"）。

    说明：故意不做索引文件 —— 每次现扫目录，少一个会写坏的东西。
    """
    directory = Path(sessions_dir)
    if not directory.is_dir():
        return []

    sessions = []
    for path in directory.glob("*.jsonl"):
        session_id = path.stem
        store = SessionStore(directory, session_id)
        title = "(空会话)"
        for msg in store.load():
            if msg.get("role") == "user" and msg.get("content"):
                title = str(msg["content"])[:50]
                break
        sessions.append({
            "id": session_id,
            "title": title,
            "mtime": path.stat().st_mtime,
        })

    # 按修改时间从新到旧
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions


def latest_session_id(sessions_dir):
    """返回最近一次 Session 的 id（--resume 不带 id 时用）。

    返回：
      最新 session 的 id 字符串；目录为空或不存在时返回 None。
    """
    sessions = list_sessions(sessions_dir)
    if not sessions:
        return None
    return sessions[0]["id"]


def delete_session(sessions_dir, session_id: str) -> bool:
    """删除某个 Session 的 JSONL 文件。

    参数：
      sessions_dir - session 文件所在目录
      session_id   - 要删除的会话 id

    返回：
      True  - 删除成功
      False - 文件不存在（没东西可删）

    说明：只删文件，不删目录。和 append 一样是惰性的--
    没说过话的会话本来就没文件，删它等于 no-op。
    """
    path = Path(sessions_dir) / (session_id + ".jsonl")
    if not path.is_file():
        return False
    path.unlink()
    return True


def open_session(sessions_dir, resume):
    """根据 --resume 参数决定：新开会话还是恢复旧会话。

    参数：
      sessions_dir — session 文件所在目录
      resume       — 三种取值：
                     None → 新开会话（不加 --resume 的默认行为）
                     ""   → 恢复最近一次会话（--resume 不带值）
                     其它 → 恢复指定 id 的会话（--resume <id>）

    返回：
      (store, history, error)
      - 成功：store 是 SessionStore，history 是读回的消息列表，error 为 None
      - 失败：store 和 history 为 None，error 是给用户看的报错文案

    为什么放在 session_store.py：
      只依赖本模块的 SessionStore / new_session_id / latest_session_id，
      不碰终端、不碰全局状态，测试可以直接喂临时目录验证。
    """
    if resume is None:
        # 新开会话：生成新 id，history 从空开始
        store = SessionStore(sessions_dir, new_session_id())
        return store, [], None

    if resume == "":
        # 恢复最近一次
        session_id = latest_session_id(sessions_dir)
        if session_id is None:
            return None, None, "没有可恢复的会话（还没聊过天），直接启动即可新开会话"
    else:
        # 恢复指定 id
        session_id = resume

    store = SessionStore(sessions_dir, session_id)
    if not store.path.is_file():
        return None, None, f"没找到会话 {session_id}（文件不存在：{store.path}）"
    return store, store.load(), None


def repair_dangling_tool_results(store: SessionStore) -> int:
    """修复悬空的 tool_calls：给"有调用、没结果"的工具调用补一条中断回执。

    什么时候会悬空：
      MachineLoop 先把 assistant(tool_calls) 消息落盘，再逐个执行工具。
      如果工具执行到一半被 Ctrl+C 打断（或进程被杀），
      JSONL 里就留下"声明了调用、没写结果"的断头消息。
      下次 resume 时对话发出去会报错（tool_calls 后面必须紧跟 tool 消息）。

    修复方式：
      扫全部消息，收集"已有回执"的 tool_call_id；
      assistant 消息里声明了、却没见到回执的调用，各补一条
      error=True 的 tool 消息（内容说明是被中断的，模型能看懂）。

    参数：
      store - SessionStore 实例（补的消息直接追加写盘）

    返回：
      补了几条消息（0 = 历史本来就完整，没动）。

    谁调用：
      cli.py 捕获 Ctrl+C 后的平滑取消路径。
    """
    messages = store.load()

    # 第一步：收集已有回执的 tool_call_id
    answered = set()
    for m in messages:
        if m.get("role") == "tool" and m.get("tool_call_id"):
            answered.add(m["tool_call_id"])

    # 第二步：找出声明了调用、却没见到回执的 tool_call
    missing = []
    for m in messages:
        if m.get("role") != "assistant" or not m.get("tool_calls"):
            continue
        for tc in m["tool_calls"]:
            if tc.get("id") not in answered:
                missing.append(tc)

    # 第三步：各补一条"被中断"的回执（模型看到 error 可以重新发起）
    for tc in missing:
        result = ToolResult(
            tool_call_id=tc.get("id", ""),
            content="（这次工具执行被用户中断，没有结果。如需继续请重新发起。）",
            error=True,
            error_type="cancelled",
            retryable=True,
        )
        store.append(result.to_message())

    return len(missing)
