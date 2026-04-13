# 风险 02：CLI 错误码分类依赖异常消息文本（主路径已缓解）

## 摘要

这个风险在本仓库里真实存在过：CLI 对外承诺结构化错误码，但内部分类逻辑曾大量依赖 `str(exc)` 和若干 `_is_*_message()` 的消息文本匹配规则。

当前主路径已经改为优先依赖结构化异常类型与显式 `details`。文档保留为风险记录，同时说明已完成的缓解范围与剩余兜底边界。

## 风险结论

- 风险是否真实：`是（历史问题）`
- 当前状态：`主路径已缓解，仍保留兼容兜底`
- 当前紧急度：`低到中`
- 当前影响面：`低到中`

## 背景

这个 skill 明确把结构化错误输出作为公开 contract 的一部分。文档建议调用方优先依赖 `error.code` 做程序分支，而不是依赖英文消息。

在这种前提下，内部实现应尽量保证：

- 错误码由显式语义驱动
- 错误文案只服务于人工排障

当前实现已经不是这个状态：

- 主路径错误会优先抛显式结构化异常
- `classify_cli_error()` 会先按异常类型映射 `code/category/details`
- 消息文本匹配仅保留为兼容历史 `ValueError` 的兜底

## 证据

### 当前分类入口

错误分类集中在 [../../scripts/timeline_cli.py](../../scripts/timeline_cli.py) 的 `classify_cli_error()`。

当前顺序已经调整为：

1. 先处理 `TimelineStructuredError` 子类
2. 再处理 `StoreWriteBusyError`
3. 最后才对剩余遗留异常走消息匹配兜底

### 已完成缓解

- 已新增共享结构化异常类型，覆盖：
  - invalid argument
  - read failed
  - turn conflict
  - metadata conflict
  - partial write
- `store.py` 与 `timeline_cli.py` 的主要公开错误路径已经改为直接抛这些异常。
- 结构化 `details` 不再依赖从 `message` 里做 fragile 解析。
- 已新增直接单测，验证 `classify_cli_error()` 对这些异常是“看类型，不看文案”。

### 剩余边界

- 为了兼容尚未改造完的历史 `ValueError` 路径，`classify_cli_error()` 里仍保留了消息匹配 fallback。
- 这意味着“完全摆脱消息匹配”还没有做到，只是已经从“主路径依赖消息”降到了“兼容兜底依赖消息”。

## 为什么这是风险

### 1. 这个风险最初判断是对的

对外看起来是结构化合同。

对内其实是：

- 文案稳定，错误码才稳定

这会让“重构不改行为”变得很难，因为哪怕只是把一句报错改得更自然，也可能影响外部契约。

### 2. 当前主路径的测试保护已补强

除原有 E2E / 宿主测试外，当前已经补了直接针对分类器的测试，至少锁住：

- structured invalid argument
- structured read failure
- structured turn conflict
- structured metadata conflict
- structured partial write

### 3. 剩余成本主要在收尾，而不是主路径重构

后续如果新增命令或错误类型，应继续优先接入结构化异常，而不是向 fallback 再添加新的消息片段规则。

## 影响面

### 当前直接影响

- 后续错误模型收尾工作
- 遗留 fallback 的维护成本
- 文档与实现的一致性

### 已明显下降的影响

- 公开 contract 主路径的错误码漂移风险已明显下降
- `details` 漂移风险也已从“正则提取”降到“显式字段传递”

## 紧急度判断

当前建议按 `低到中` 处理。

当前不是高优先级，因为：

- 主路径已改为结构化异常
- 直接测试与 E2E 都已覆盖

但也不是完全可以忽略，因为：

- 兼容 fallback 仍在
- 后续新代码如果偷懒继续抛裸 `ValueError`，仍会把问题带回来

## 建议推进方向

### 已完成项

- 已定义共享结构化异常类型
- 已让 `classify_cli_error()` 优先消费异常类型
- 已让主路径 `details` 通过显式字段传递
- 已补分类器直接单测

### 后续建议

1. 继续把剩余历史 `ValueError` 路径按需迁到结构化异常。
2. 禁止为新错误分支新增消息片段分类规则，优先新增异常类型。
3. 保留 fallback，但把它视为兼容层，而不是主实现。

## 验收建议

当前已覆盖：

- conflict 类异常的 code 不依赖消息改写
- read failure 的 details 提取不依赖 fragile 前缀
- invalid argument 的 code 在 helper 重构后不漂移
- 未知异常才落到 `TM_INTERNAL`
