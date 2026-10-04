# 在代码里使用 Harness

想在自己的 Python 程序里运行这个 Agent，可以从 `open_harness_session` 开始。你提供模型调用函数和工作区；Harness 负责会话、执行循环与工具权限。

Harness 是 `auto_coding_machine` Python 包，可通过本地 editable 依赖加入另一个项目。核心组合从 `auto_coding_machine` 导入，外部记忆契约和 Tencent Adapter 从 `auto_coding_machine.memory` 导入。

## 运行只读 Review 示例

仓库提供了一个独立于交互式 CLI 的调用者。先按首页说明在 `.env` 配好模型，再从项目根目录运行：

```powershell
uv run python -m examples.harness_review . "检查这个项目的错误处理"
```

示例会使用内置 `review` Profile 和 `ModelAdapter`，通过 `open_harness_session` 执行任务。Profile 只提供读取能力；若任务意外请求权限，示例会停止，不会自动批准。会话仍保存到 `.autocoding/profiles/review/sessions/`。

下面先用固定回复演示调用方法。运行时不会调用真实模型，也不会让 Agent 改动项目代码。

## 跑通一个最小例子

先在 Harness 项目根目录运行 `uv sync`。把下面的代码保存为 `demo_harness.py`：

```python
from pathlib import Path

from auto_coding_machine import AgentResponse, load_profile, open_harness_session


def demo_model(messages):
    # 先返回固定文字，确认 Harness 的调用流程能跑通。
    return AgentResponse(content="我收到了你的问题。", done=True)


session = open_harness_session(
    Path.cwd(), demo_model, profile=load_profile("review")
)
run = session.begin_run("这个项目做什么？")
result = run.start()

print(result["status"])  # success
print(result["reply"])   # 我收到了你的问题。
print(session.store.session_id)  # 记下这个 ID，以后可恢复会话
```

在 PowerShell 中运行：

```powershell
$env:CODING_CONTEXT_LENGTH = "32768"
$env:PYTHONIOENCODING = "utf-8"
uv run python .\demo_harness.py
```

这里手动给出模型窗口大小，是为了让示例启动时不用查询模型服务；第二行让 Windows 终端正确显示中文。`review` 是只读 Profile；`demo_model` 只会返回上面写死的文字，所以这段代码演示的是 Harness 的接线，不是 AI 审查效果。原始会话保存在工作区的 `.autocoding/profiles/review/sessions/`。

在已有的另一个 uv 项目根目录运行下面的命令，把路径替换成 Harness 仓库的实际位置：

```powershell
uv add --editable "E:\path\to\Coding-Agent"
uv sync
uv run --env-file .env python .\app.py
```

调用程序从自己的工作目录运行；模型配置从调用方的 `.env` 或进程环境读取。如果还没有 `pyproject.toml`，先运行 `uv init --bare --no-package`。

## 接下来

本页先帮你跑通 Harness 和本地安装。接入自己的模型、注册工具、处理权限和恢复会话等完整做法，见 [Harness Cookbook](harness-cookbook.md)。
