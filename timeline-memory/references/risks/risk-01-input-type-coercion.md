# 风险 01：输入类型强转会把非法输入静默写成合法数据（公开输入层已缓解）

## 摘要

这个风险在早期实现里真实存在过：`project-turn` 输入模型层对多个字段使用过宽松强转，例如 `str(...)` 和 `bool(...)`。这会让本应被拒绝的非法输入被静默接受，并以“看起来合法”的形式落盘。

当前仓库中的公开 `project-turn` 输入层已经不再按这种方式工作；这份文档保留为历史风险记录，同时说明已落地的缓解状态与剩余边界。

## 风险结论

- 风险是否真实：`是（历史问题）`
- 当前状态：`公开输入层已缓解`
- 当前紧急度：`低`
- 当前影响面：`低到中`

## 背景

这个 skill 的设计理念是“公开接口窄、合同明确、由高层输入驱动内部投影”。文档把 `project-turn` 描述成一个高层 contract，只允许有限字段，并强调调用方不应传内部存储字段，见 [../schema.md](../schema.md) 和 [../../SKILL.md](../../SKILL.md)。

在这种设计下，输入层本应承担两个责任：

1. 拒绝结构不符合 contract 的输入。
2. 拒绝类型不符合 contract 的输入。

当前实现已经同时覆盖这两个责任：

1. 拒绝结构不符合 contract 的输入。
2. 拒绝类型不符合 contract 的输入。

## 证据

### 历史问题位置

- 这类问题曾出现在 `ProjectTurnPlanTime.from_dict()`、`ProjectTurnContext.from_dict()`、`ProjectTurnThreadInput.from_dict()`、`ProjectTurnInput.from_dict()` 对公开输入字段的解析中。
- 现在这些入口已经统一改为显式 `require_string()`、`require_optional_string()`、`require_bool()` 校验，相关实现位于 [../../scripts/models.py](../../scripts/models.py)。

### 历史最小复现

- `{"source": null}` 会变成字符串 `"None"`，不是默认值，也不是报错。
- `{"all_day": "false"}` 会被解析为 `True`，因为 Python 中 `bool("false")` 为真。
- `{"thread_kind": null}` 会变成字符串 `"None"`，而不是报错。

### 当前修复状态

- 公开 `project-turn` 路径上的相关字段已经改为严格类型校验。
- 错类型输入当前会稳定返回 `TM_INVALID_ARGUMENT`，而不是被静默强转。
- 真实 CLI E2E 与 `selftest.py` 已补以下回归：
  - `context.source = null`
  - `thread.thread_kind = null`
  - `thread.plan_time.all_day = "false"`
  - `user_text = 123`
  - `assistant_text = false`

## 为什么这是风险

### 1. 历史风险判断是正确的

最危险的不是报错，而是成功。这个判断本身仍然成立，也是当时必须修的原因。

一旦非法输入被转换成合法字符串或布尔值：

- CLI 返回 `ok: true`
- 测试和调用方可能以为写入成功且语义正确
- 持久化层会记录错误语义

这种问题往往比硬失败更难排查。

### 2. 当前公开输入层已不再按该方式污染数据

对 `project-turn` 公开 contract 来说，当前已经不再接受这些错类型输入，因此这部分风险已从“会发生”降为“历史上发生过，当前已被拦截”。

剩余需要注意的是：仓库里仍有一些内部存储读模型保留宽松读取逻辑，用于兼容历史数据与容错读取。但这些路径不再是公开 `project-turn` 输入 contract 的直接风险源，后续如要继续收紧，应单独立项处理。

因此问题不是局部的“输入校验不严”，而是会向后传播。

### 3. 当前真正的风险已经变成“文档状态滞后”

在代码和测试已经修复后，如果风险文档仍把它描述成“当前故障”，会误导后续排期与优先级判断。这就是本次回填文档的原因。

## 影响面

### 当前直接影响

- 风险排期判断
- 风险总览的优先级排序
- 后续接手者对当前输入边界的认知

### 已验证覆盖的字段

至少已覆盖以下公开 contract 字段：

- `turn_id`
- `user_text`
- `assistant_text`
- `thread.thread_kind`
- `thread.title`
- `thread.status`
- `context.source`

### 剩余边界

- 内部存储读模型仍存在部分宽松转换，用于兼容现有 JSONL / snapshot 数据。
- `normalize_structured_list()` 仍会把非 dict 项包装成 `{"text": str(item)}`；这属于 `thread.content.followups/items` 的当前设计语义，不等同于本风险文档最初指向的公开字符串/布尔字段静默强转。

## 紧急度判断

当前建议按 `低` 处理。

判断依据：

- 公开 `project-turn` 写入路径已补严格校验
- CLI E2E、宿主测试与 `selftest.py` 都已有回归
- 当前主要问题是文档状态需要同步，而不是实现仍在放行脏数据

## 建议推进方向

### 已完成项

- 已在公开输入模型层收紧字符串与布尔字段校验
- 已把错类型输入稳定映射为 `TM_INVALID_ARGUMENT`
- 已补真实 CLI E2E 与 `selftest.py` 回归

### 后续建议

1. 保持现有 contract 类型回归，不要回退到宽松强转。
2. 若后续要继续收紧内部存储读模型，单开新风险或新里程碑，不要混在本条历史风险里。
3. 风险总览中将本项从当前优先修复列表移出。

## 验收建议

当前已具备的回归包括：

- `context.source = null` 应失败
- `thread.thread_kind = null` 应失败
- `plan_time.all_day = "false"` 应失败
- `user_text = 123` 应失败
- `assistant_text = false` 应失败
- 错误码稳定为 `TM_INVALID_ARGUMENT`
