# 风险 04：输入合同边界测试覆盖不足（当前关键边界已补齐）

## 摘要

这个风险在仓库里真实存在过：测试体系对恢复、并发、分页、兼容读取等核心行为覆盖很强，但对“输入合同是否严格拒绝错类型数据”曾经覆盖不足。

当前文档中列出的关键合同边界已经补齐到真实 CLI E2E 和 `selftest.py` 中。这份文档保留为历史风险记录，同时说明当前覆盖状态与剩余边界。

## 风险结论

- 风险是否真实：`是（历史问题）`
- 当前状态：`关键边界已补齐`
- 当前紧急度：`低`
- 当前影响面：`低到中`

## 背景

这个 skill 的设计里，`project-turn` 是唯一公开写入口。

因此输入边界测试应承担两层职责：

1. 验证 happy path 正常写入。
2. 锁住 contract 边界，确保错类型输入被拒绝。

当前第二层覆盖已经不再是这个状态，至少对公开 `project-turn` 输入合同的关键错类型路径，已经有集中回归保护。

## 证据

### 已有强覆盖区域

现有测试对以下内容覆盖较好：

- replay / repair
- txn 阶段恢复
- 并发写入
- 分页与游标
- `compat` / `strict`
- 结构化错误输出

主要体现在：

- [../../tests/timeline/test_timeline_cli_e2e.py](../../tests/timeline/test_timeline_cli_e2e.py)
- [../../scripts/selftest.py](../../scripts/selftest.py)

### 当前已覆盖的输入校验

除已有的额外字段、非法 cursor、非法时间窗口外，当前还稳定覆盖了：

- `context.source = null`
- `context.actor_id = {}`
- `thread.thread_kind = null`
- `thread.title = 123`
- `thread.plan_time.all_day = "false"`
- `thread.plan_time.due_at = false`
- `thread.content.notes = false`
- `user_text = 123`
- `assistant_text = false`

### 当前状态与本文原始结论的差异

文档最初列出的“未见稳定覆盖”的关键案例，目前都已经能在以下入口中找到：

- [../../tests/timeline/test_timeline_cli_e2e.py](../../tests/timeline/test_timeline_cli_e2e.py)
- [../../scripts/selftest.py](../../scripts/selftest.py)

## 为什么这是风险

### 1. 风险判断当时是成立的

如果测试不锁住合同边界，那么：

- 输入层宽松强转问题很容易长期存在
- 后续重构可能继续复制这种模式

### 2. 当前关键输入合同的可信度已明显提高

当输入边界测试不足时，很难确信：

- 错输入一定失败
- 失败一定映射到正确 `error.code`

### 3. 当前剩余问题更多是“覆盖是否继续扩展”

后续需要讨论的已不再是“有没有关键边界测试”，而是：

- 是否要把矩阵继续扩展到更多字段和更多类型组合
- 是否要为 `classify_cli_error()` / fallback 行为继续补更细粒度 direct tests

## 影响面

### 当前直接影响

- 后续新增字段时是否同步补齐同类边界测试
- 是否继续把“关键边界”扩展成更系统的矩阵

### 已下降的间接影响

- 历史上确实会放大 [risk-01-input-type-coercion.md](./risk-01-input-type-coercion.md) 和 [risk-02-error-classification-by-message.md](./risk-02-error-classification-by-message.md)
- 现在这两项在公开主路径都已缓解，相关关键测试也已经补到位

## 紧急度判断

当前建议按 `低` 处理。

原因：

- 文档列出的关键边界场景已落到真实 CLI E2E 和 `selftest.py`
- `TM_INVALID_ARGUMENT` 映射也已被锁定
- 当前剩余工作是扩展覆盖，不是补救明显缺口

## 建议推进方向

### 已完成项

- 已新增集中拒绝错类型输入的合同测试
- 已在 E2E 与 `selftest.py` 同时覆盖关键案例
- 已锁定错类型输入返回 `TM_INVALID_ARGUMENT`

### 后续建议

1. 后续新增公开字段时，同步补一条错类型回归。
2. 如要继续扩矩阵，优先补当前仍未集中覆盖的数组/对象误传组合。
3. 不需要再把本项当成当前最高优先级风险。

## 验收建议

当前已覆盖并建议持续保留的最小矩阵包括：

- `null`
- 数字
- 布尔
- 对象
- 数组

并对关键字段分别断言：

- 是否应接受
- 接受时默认化行为是否符合文档
- 拒绝时错误码是否稳定
