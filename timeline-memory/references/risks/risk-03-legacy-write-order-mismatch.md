# 风险 03：Legacy helper 写入顺序与 M2 设计协议不一致（已缓解）

## 摘要

这个风险在仓库里真实存在过：设计文档在 M2 中明确收敛到“先 snapshot、后 history”的写入协议，但仓库中曾保留若干 helper 继续使用旧顺序：先写 history，再写 snapshot。

当前这些遗留 helper 已经统一为 `snapshot -> history` 顺序。文档保留为历史风险记录，同时说明已完成的修复和验证范围。

## 风险结论

- 风险是否真实：`是（历史问题）`
- 当前状态：`已缓解`
- 当前紧急度：`低`
- 当前影响面：`低`

## 背景

M2 的核心目标不是“绝对跨文件原子提交”，而是让中间态：

- 可识别
- 可补齐
- 可收敛

在这个目标下，文档明确把 thread 更新顺序收敛为：

1. 先原子替换 snapshot
2. 再补 history

这样可以减少错误中间态暴露窗口。

## 证据

### 设计要求

见 [../design-milestones-status-and-acceptance.md](../design-milestones-status-and-acceptance.md) 中 M2 相关说明：

- “先原子替换 snapshot，再补 history”
- `snapshot_committed -> history_committed`

### 当前主路径

当前 `project-turn` txn 主路径已经按正确顺序执行：

- 先 `write_thread_snapshot()`
- 再 `append_thread_history()`

见 [../../scripts/timeline_cli.py](../../scripts/timeline_cli.py)。

### 历史遗留 helper

此前保留旧顺序的路径有：

- [../../scripts/store.py](../../scripts/store.py) 中 `ThreadStore.write_thread()`
- [../../scripts/timeline_cli.py](../../scripts/timeline_cli.py) 中 `apply_replay_thread_write_plan()`

当前这两处都已经改为：

- 先写 snapshot
- 再 append history

## 为什么这是风险

### 1. 风险判断曾经成立

从文档角度，M2 已经把该问题定义为已完成收敛。

但从代码角度，仍有活路径保留旧顺序。这意味着：

- 文档结论和实现状态不完全一致

### 2. 当前该中间态窗口已被压掉

legacy helper 当前不再先写 history，因此这里描述的旧窗口不再是现状。

### 3. 当前真正需要防的是回退

后续真正要避免的是：新代码再次绕过当前 helper 约束，把旧顺序重新带回来。

## 影响面

### 当前不受影响的范围

- 当前 txn 主路径
- 正常新写入流程

### 当前剩余影响

- 文档状态与实现一致性
- 后续重构时是否会无意回退旧顺序

### 当前测试状态

当前已经补了直接锁定写序的单测，覆盖：

- `ThreadStore.write_thread()` 使用 `snapshot -> history`
- `apply_replay_thread_write_plan()` 使用 `snapshot -> history`

## 紧急度判断

当前建议按 `低` 处理。

原因：

- 公开主路径和 legacy helper 现在都已统一
- 回归和自检都已通过
- 剩余工作主要是防回退，不是继续修主问题

## 建议推进方向

### 已完成项

- 已统一 `ThreadStore.write_thread()` 为 `snapshot -> history`
- 已统一 `apply_replay_thread_write_plan()` 为 `snapshot -> history`
- 已补直接单测锁定写序
- 既有 pytest / selftest / host tests 已通过

### 后续建议

1. 后续新增 thread 写入 helper 时，默认复用当前顺序，不要重新引入 history-first。
2. 如果未来再扩展 legacy repair 路径，继续把写序单测当成防回退护栏。

## 验收建议

当前已满足：

- legacy replay repair 路径也使用 snapshot-first
- helper 层不存在反向顺序的遗留入口
- 故障注入后，查询不再暴露旧的中间态窗口
