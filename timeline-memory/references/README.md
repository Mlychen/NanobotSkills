# References

当前 `timeline-memory` 仓库中的设计与规范文档入口。

## 文档分工

- `schema.md`
  - 当前稳定的公开 CLI contract 与内部存储模型说明。
  - 面向实现与调用方，优先级高于提案类文档。

- `design-milestones-status-and-acceptance.md`
  - 既有里程碑、验收标准、实现状态与历史决策记录。
  - 偏过程文档，不作为新增设计的唯一规范源。

- `project-turn-proxy-design.md`
  - 面向主 agent 的 `project-turn proxy` 设计提案。
  - 说明主 agent、代理层与 `timeline-memory` 的职责边界、交互方式与推荐实现。

- `risks/README.md`
  - 当前已确认风险与分项风险文档入口。

## 阅读建议

如果要理解当前稳定能力：

1. 先读 `schema.md`
2. 再读 `design-milestones-status-and-acceptance.md`
3. 需要看后续演进方向时，再读 `project-turn-proxy-design.md`
4. 排期或缺陷治理时，再看 `risks/README.md`
