"""守卫管理器：防止 Agent 卡死。

【这文件是干什么的】
  循环可能无限跑，Guard 负责拦住：
    - 连续重复调同一工具（同一个参数）
    - 反复失败
    - 长时间没进展

【当前状态】
  Phase 2 先从最简单的开始：检测连续重复的工具调用。
  Phase 3 再加：
    - 连续失败检测（最近 5 轮全是 error）
    - 上下文无变化检测

【和现有 GUI Guard 的关系】
  src/phase2/guard_runtime.py 是 GUI 专用的，检查：
    - 搜索结果页重复 Enter
    - 滚动次数
    - AAAA / ABAB 动作循环

  这里是通用 Guard，用在不同 Profile：
    - 工具级重复（read_file 连续 3 次同一路径）
    - 总失败次数
    - 预算耗尽（预算耗尽由 MachineLoop 的 max_turns 处理）

【谁会用】
  src/engine/machine_loop.py
"""


class GuardManager:
    """通用守卫。

    【大白话】
      给 Agent 一个"刹车"。
      模型有时候会卡住——反复调同一个工具、反复读同一个文件。
      GuardManager 就是检测这种卡死状态，然后叫停。

    用法例子：
        guard = GuardManager()
        if guard.should_stop(messages, turn):
            # 停止循环
    """

    def __init__(self):
        """初始化。

        目前只设了一个阈值：连续几次相同工具调用就算卡死。
        Phase 3 可以加时间窗口、失败次数累积等。
        """
        # 连续多少次相同的工具+参数调用就算卡死
        # 3 次的依据：有些工具确实需要调 2 次（比如先 grep 再 read_file），
        # 但连续 3 次一模一样就是卡死了
        self.repeat_limit = 3

    def should_stop(self, messages: list, turn: int) -> bool:
        """判断是否应该停止。

        参数：
          messages — 当前完整对话历史（给 Guard 看里面有没有卡死特征）
          turn     — 当前轮次（暂时没用，以后可以结合时间窗口）

        返回：
          True  — 应该停止（卡死了）
          False — 可以继续（一切正常）

        检测规则（当前）：
          从 messages 里提取最近几轮的工具调用，
          如果连续 3 次都是同一个工具 + 同一套参数，说明卡死了。

        【为什么只看 tool 消息】
          tool 消息就是工具执行结果。
          每调一次工具，就会产生一条 role="tool" 的消息。
          所以数 tool 消息就能知道最近调了什么工具、传了什么参数。
          不需要解析 assistant 消息里的 tool_calls，因为 tool 消息
          里已经带了对应的 tool_call_id，我们可以用它来追踪。
        """
        # ---------- 第一步：从 messages 里提取最近几轮的工具调用记录 ----------
        # messages 的结构大致是这样：
        #   [
        #       {"role": "system", "content": "..."},
        #       {"role": "user", "content": "读一下 test.txt"},
        #       {"role": "assistant", "content": "...", "tool_calls": [...]},
        #       {"role": "tool", "tool_call_id": "call_1", "content": "文件内容..."},  ← 工具结果
        #       {"role": "assistant", "content": "...", "tool_calls": [...]},
        #       {"role": "tool", "tool_call_id": "call_2", "content": "文件内容..."},  ← 工具结果
        #   ]
        #
        # 我们要找的就是 role="tool" 的消息，每条对应一次工具调用。
        #

        # 收集所有 tool 消息（按时间顺序）
        tool_messages = []
        for msg in messages:
            if msg.get("role") == "tool":
                tool_messages.append(msg)

        # 如果工具调用次数少于 repeat_limit，肯定还没卡死
        if len(tool_messages) < self.repeat_limit:
            return False

        # ---------- 第二步：看最近 repeat_limit 条是否相同 ----------
        # 取最近 repeat_limit 条 tool 消息
        recent_tools = tool_messages[-self.repeat_limit:]

        # 拿到这 3 条 tool 消息的 tool_call_id
        # 然后靠 tool_call_id 去 assistant 消息里找到对应的工具名和参数
        # 不过有个更简单的办法：
        #   每个 tool 消息的 "content" 字段包含了工具执行结果，
        #   但我们没法从结果反推"调了哪个工具"。
        #
        # 所以正确做法：找 assistant 消息里的 tool_calls。
        # 每个 assistant 消息如果有 tool_calls，结构是：
        #   {
        #       "role": "assistant",
        #       "tool_calls": [
        #           {"id": "call_1", "function": {"name": "read_file", "arguments": '{"path": "test.txt"}'}}
        #       ]
        #   }
        #
        # 我们遍历 assistant 消息，收集每次工具调用的 (name, arguments) 对，
        # 按调用顺序排列，就能比对最近几次是不是重复了。

        # 按调用顺序收集所有 (工具名, 参数字符串)
        call_history = []  # 里面放 (name, args_str) 元组
        for msg in messages:
            if msg.get("role") == "assistant":
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    for tc in tool_calls:
                        name = tc["function"]["name"]
                        # arguments 是字符串，直接比字符串就行
                        args_str = tc["function"]["arguments"]
                        call_history.append((name, args_str))

        # 如果历史不够长，没法判断
        if len(call_history) < self.repeat_limit:
            return False

        # 取最近 repeat_limit 次
        recent_calls = call_history[-self.repeat_limit:]

        # 检查这 3 次的名字是否都一样
        names = [c[0] for c in recent_calls]
        if len(set(names)) > 1:
            # 工具名字不一样，说明在正常切换，不是卡死
            return False

        # 检查这 3 次的参数是否也一样
        args_list = [c[1] for c in recent_calls]
        if len(set(args_list)) == 1:
            # 连续 3 次同一个工具 + 同一套参数 → 卡死了
            return True

        # 名字相同但参数不同（比如读了不同的文件），不算卡死
        return False
