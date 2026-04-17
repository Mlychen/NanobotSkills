# References

当前 `timeline-memory` 仓库中的设计与规范文档入口。

如果你是第一次接手这个 skill，建议先从上一级的 `README.md` 开始，再进入这里的细分文档。

## 文档分工

- `schema.md`
  - 当前稳定的公开 CLI contract 与内部存储模型说明。
  - 面向实现与调用方，优先级高于提案类文档。

- `design-milestones-status-and-acceptance.md`
  - 既有里程碑、验收标准、实现状态与历史决策记录。
  - 偏过程文档，不作为新增设计的唯一规范源。

- `project-turn-proxy-design.md`
  - `project-turn proxy` 的正式设计草案。
  - 当前用于定义代理层的输入约定、职责边界与最小链路。

- `risks/README.md`
  - 当前已确认风险与分项风险文档入口。

## 阅读建议

如果要理解当前稳定能力：

1. 先回到上一级读 `../README.md`
2. 需要确认稳定 contract 时读 `schema.md`
3. 需要看里程碑状态和历史决策时读 `design-milestones-status-and-acceptance.md`
4. 需要推进 `project-turn proxy` 设计时，读 `project-turn-proxy-design.md`
5. 排期或缺陷治理时，再看 `risks/README.md`
