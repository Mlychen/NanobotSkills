# Timeline Memory 风险总览

本文档汇总当前已确认的实现与维护风险，并指向独立分项文档。

目标不是重新定义公开 contract，而是把已经识别出的风险沉淀为可追踪、可排期、可验证的工作项，方便后续推进。

评估口径：

- `风险是否真实`：当前代码与文档、测试、运行路径交叉核对后，是否已经确认存在。
- `紧急度`：不修复时，对当前功能正确性、外部合同稳定性、后续演进成本的综合优先级。
- `影响面`：受影响的调用路径、数据范围、维护范围。

## 风险列表

### 1. 输入类型强转会把非法输入静默写成合法数据

- 风险是否真实：`是（历史问题）`
- 当前状态：`公开输入层已缓解`
- 当前紧急度：`低`
- 影响面：`低到中`
- 详情见：[risk-01-input-type-coercion.md](./risk-01-input-type-coercion.md)

### 2. CLI 错误码分类依赖异常消息文本

- 风险是否真实：`是（历史问题）`
- 当前状态：`主路径已缓解，仍保留兼容兜底`
- 当前紧急度：`低到中`
- 影响面：`低到中`
- 详情见：[risk-02-error-classification-by-message.md](./risk-02-error-classification-by-message.md)

### 3. Legacy helper 写入顺序与 M2 设计协议不一致

- 风险是否真实：`是（历史问题）`
- 当前状态：`已缓解`
- 当前紧急度：`低`
- 影响面：`低`
- 详情见：[risk-03-legacy-write-order-mismatch.md](./risk-03-legacy-write-order-mismatch.md)

### 4. 输入合同边界测试覆盖不足

- 风险是否真实：`是（历史问题）`
- 当前状态：`关键边界已补齐`
- 当前紧急度：`低`
- 影响面：`低到中`
- 详情见：[risk-04-input-contract-test-gaps.md](./risk-04-input-contract-test-gaps.md)

### 5. 模块导入依赖 `sys.path` 补丁，复用边界脆弱

- 风险是否真实：`是`
- 紧急度：`中低`
- 影响面：`低到中` 对 CLI 用户，`中到高` 对开发者复用
- 详情见：[risk-05-import-path-fragility.md](./risk-05-import-path-fragility.md)

### 6. 文档组织分裂，规范源不清晰

- 风险是否真实：`是`
- 紧急度：`低到中`
- 影响面：`中`
- 详情见：[risk-06-documentation-fragmentation.md](./risk-06-documentation-fragmentation.md)

## 建议推进顺序

第一批建议优先处理：

1. `risk-05-import-path-fragility`
2. `risk-06-documentation-fragmentation`
3. 按需评估是否需要为内部存储读模型的宽松兼容单开新风险项

第二批建议跟进：

1. 继续收尾历史消息匹配 fallback，但不再作为当前最高优先级
2. 持续防止 thread 写序回退，但不再作为当前主修项
3. 后续新增字段时同步补合同边界测试

## 使用方式

后续如果要立项推进，建议每个风险至少补齐以下内容：

- 修复范围
- 明确不修的边界
- 验收测试
- 对文档与公开 contract 的影响
- 是否需要迁移或兼容历史数据
