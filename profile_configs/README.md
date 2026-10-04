# 我的 Profile 配置

把自己要使用的 `.yaml` 或 `.yml` 配置放在这里，TUI 的 `/profile` 会自动列出。

初始目录只有这份说明；内置的 coding、review、companion 无需复制配置即可使用。
可复制 [陪伴示例](../examples/profiles/companion.yaml) 或 [审查示例](../examples/profiles/reviewer.yaml)，再修改名称和提示词。

自定义配置目前用 `/profile profile_configs/文件名.yaml` 加载，不能直接用 YAML 中的 name 切换。
同一工作区中，相同 name 共用会话与记忆目录；希望隔离时请用不同名称。

完整字段见 [配置指南](../docs/profiles.md)。
