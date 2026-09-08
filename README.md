# AutoCoding Machine

一个在终端里使用的 AI 助手。你用自然语言描述需求，它可以读代码、修改文件、运行测试，也可以切换成只读审查或陪伴聊天。

例如，你可以直接输入：

> 帮我看一下这个项目，先解释代码怎么运行，暂时不要修改。

## 它能做什么？

内置三种模式。项目里把一套模式配置叫作 **Profile**，可以理解成“角色设定 + 能用的工具”。

| 模式 | 适合做什么 |
| --- | --- |
| `coding`（默认） | 写功能、修 Bug。修改文件和执行命令时会请求确认；修改代码后会检查是否做过验证 |
| `review` | 只看代码、找问题、给建议，不修改文件、不执行命令 |
| `companion` | 陪伴聊天，可以记录你提供的偏好，不使用代码和命令工具 |

聊天记录会保存，之后可以接着聊。不同 Profile 分开保存会话和记忆，默认 Coding 的用户偏好仍沿用原来的全局记忆文件。

每个 Profile 的会话和输入历史都放在 `.autocoding/profiles/<profile-name>/` 下；详细运行记录默认关闭，评测时在 YAML 中设置 `trace_enabled: true` 才会开启。

## 怎么启动？

需要先安装 **Python 3.11 或更新版本**和 **uv**。以下命令在项目目录中运行。

**1. 安装项目依赖。**

```powershell
uv sync
```

**2. 配置模型。** 首次使用时复制配置模板；如果已经有 `.env`，跳过复制。

```powershell
Copy-Item .env.example .env
```

打开 `.env`，填写这三项：

- `LLM_BASE_URL`：模型服务的 API 地址。
- `LLM_MODEL`：要使用的模型名称。
- `LLM_API_KEY`：访问模型服务的密钥。

需要使用兼容 OpenAI 接口的模型服务。密钥留在本地，不要提交到仓库。

**3. 启动助手。**

```powershell
uv run python -m src
```

启动时所在的目录就是它处理文件的工作区。进入界面后直接打字即可：**Enter 发送，Alt+Enter 换行**。

宽终端会显示渐变色 Logo，窄终端会自动换成紧凑面板；工具调用显示参数卡片，助手回复使用 Markdown 面板。输入区底部显示当前 Profile、模型和输入预算的估算占比。

`/status` 可查看模型窗口及来源：手动配置、服务端报告或未知。token 在请求前只能估算（含工具定义）；上次请求的输入量以服务端 `usage` 为准。`/cost` 显示本次任务已报告的用量，报告缺失时会明确提示，详见[计数与窗口说明](docs/profiles.md#token-用量与模型窗口)。

## 常用命令

这些命令是在助手界面里输入的：

| 命令 | 作用 |
| --- | --- |
| `/profile` | 查看有哪些配置 |
| `/profile review` | 切换成只读审查；换成 `coding` 或 `companion` 就能切换其他模式 |
| `/sessions` | 查看当前模式下的聊天记录 |
| `/resume <会话ID>` | 继续某一次聊天，ID 可以从记录列表中找到 |
| `/memory` | 查看保存的长期记忆 |
| `/help` | 查看全部命令 |
| `/quit` | 退出 |

输入 `/` 会弹出命令提示，Tab 可以补全。**切换 Profile 会开始新对话，旧对话仍然保留。** 任务执行中想停下来，先按 Ctrl+C。

## 想改成自己的角色？

内置预设只有 `coding`、`review`、`companion`。教学示例放在 [examples/profiles](examples/profiles/)，不会自动出现在切换列表里。

想创建自己的角色，先复制示例，再修改 `name` 和 `prompt`：

```powershell
Copy-Item examples/profiles/companion.yaml profile_configs/my-companion.yaml
```

在助手界面中加载：

```text
/profile profile_configs/my-companion.yaml
```

`profile_configs/` 中的 YAML 会出现在列表里，自定义配置用文件路径切换。没有启用或发现 Skills 时，模型不会收到技能搜索和加载工具。更多字段和记忆位置见[配置指南](docs/profiles.md)。

## 想了解代码？

- [当前架构](docs/architecture.md)：现在有哪些模块，分别负责什么。
- [交互式架构图](docs/diagrams/README.md)：下载后用浏览器打开，查看整体结构和完成验证流程。
- [后续需求与计划](docs/plans/README.md)：通用 Profile、架构整理、Goal / Todo 和长上下文优化，均标明实施状态。

已完成的设计记录归档在 [docs/archive](docs/archive/)，需要了解历史取舍时再看。

修改代码后，可以运行检查：

```powershell
uv run pytest
```

测试包含模拟模型的流程验证，不代表真实模型的任务成功率。Qwen GGUF 专项优化还在后续计划中。

开源协议：[MIT](LICENSE)。
