# Timeline Memory

`timeline-memory` 是一个独立的时间轴记忆 skill。

对外稳定合同保持为脚本式 CLI：

```bash
uv run python scripts/timeline_cli.py <command> --store-root <path> ...
```

这份 `README.md` 面向项目维护与文档导航。

项目使用时仍以 `SKILL.md` 为入口，不要求先读这份 `README.md`。

如果你是第一次接手这个目录，建议先看这份 `README.md`，再按需要进入更具体的文档。

## 文档入口

- `SKILL.md`
  - 项目使用入口，也是运行说明。
  - 说明何时使用这个 skill、公开命令怎么调用、推荐测试入口怎么跑。

- `references/schema.md`
  - 当前稳定的公开 contract 规范源。
  - 需要确认字段、输入边界、输出形态时，以这份文档为准。

- `references/design-milestones-status-and-acceptance.md`
  - 里程碑状态、历史决策、验收边界与阶段性结论。
  - 偏设计与交接记录，不作为公开 contract 的唯一规范源。

- `references/risks/README.md`
  - 风险总览与各风险项入口。
  - 用于排期、治理和后续收口跟踪。

## 推荐阅读顺序

### 1. 想知道怎么用

先读 `SKILL.md`。

重点看：

- 何时使用
- 公开命令
- `project-turn` 输入约束
- 测试与验证

### 2. 想确认合同和字段边界

再读 `references/schema.md`。

重点看：

- 4 个公开 CLI 命令
- `project-turn` 输入字段
- 查询输出结构
- 错误输出合同

### 3. 想理解为什么这样设计

读 `references/design-milestones-status-and-acceptance.md`。

适合查看：

- 里程碑完成状态
- 历史设计取舍
- 已完成与非目标边界
- 后续交接说明

### 4. 想继续做治理或排风险

读 `references/risks/README.md`。

适合查看：

- 当前确认存在的风险
- 每项风险的紧急度和影响面
- 建议推进顺序

## 最小使用示例

在 `timeline-memory/` 根目录执行：

```bash
uv run python scripts/timeline_cli.py project-turn --store-root ./timeline-store --input turn.json
uv run python scripts/timeline_cli.py get-thread --store-root ./timeline-store --thread-id thr_demo
```

## 当前文档分工原则

- 运行方式、命令示例、测试入口：放在 `SKILL.md`
- 稳定 contract 与字段边界：放在 `references/schema.md`
- 过程性设计记录与完成状态：放在 `references/design-milestones-status-and-acceptance.md`
- 风险与治理事项：放在 `references/risks/`

如果同一信息在多处出现，应优先维护上述“主归属”中的那一份，其余位置尽量改为摘要或链接。
