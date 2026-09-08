# 交互式架构图

[返回首页](../../README.md) · [当前架构说明](../architecture.md)

- [整体架构](architecture-overview.html)：终端、运行时、执行循环、工具和数据存储。
- [完成证据门](completion-gate-workflow.html)：何时要求验证、何时允许交付、何时标记未验证。
- [讲解稿](interview-script.md)：结合两张图介绍设计及其边界。

下载仓库后双击 HTML 在浏览器打开。GitHub 文件页面显示源码，不会直接运行交互图。这两张图描述当前实现，不包含规划中的通用 Profile、Goal 或 Todo。

修改同目录的 `.architecture.json` 或 `.workflow.json` 后，用 archify 重新生成对应 HTML；不要只改 HTML 导致源文件与产物不一致。`visual-check` 生成的截图和报告用于本地检查，已被 Git 忽略。

图是简化说明，具体约束以代码和测试为准：完成门检查代码净修改及验证时序，不证明需求全部正确；历史召回有条数和输出预算限制；token 请求前计数仍是估算。
