# Project Turn Proxy Design

## Status

- Draft
- 当前文档用于定义 `project-turn proxy` 的输入约定、职责边界与最小链路。
- 它描述的是未来代理层设计，不代表当前 `timeline-memory` 已经实现该能力。

## 1. 背景

`timeline-memory` 当前已经有稳定的高层写入口：`project-turn`。

它负责：

- 写入一整轮 turn
- 自动生成 raw event
- 自动维护 thread / history / revision
- 保障幂等、恢复与并发语义

问题不在于底座缺少写入能力，而在于调用方直接面对 `project-turn` contract 时，需要自己理解并填写大量 timeline 领域字段，例如：

- `thread.thread_kind`
- `thread.status`
- `thread.plan_time`
- `thread.fact_time`
- `thread.content`

这会带来两个持续问题：

1. 调用方很难把正确的值放到正确的地方，尤其是时间字段。
2. 调用方容易把自然语言里的事务语义，直接误投影为底层存储字段。

`project-turn proxy` 的目标就是在上游调用方与 `timeline-memory` 之间增加一层稳定的语义代理。

## 2. 设计目标

`project-turn proxy` 第一版的目标是：

1. 让上游只表达事务语义，而不直接填写 `timeline-memory` 底层字段。
2. 把事务动作、事务领域、时间语义与原始对话收口成稳定输入。
3. 由 proxy 内部生成 `turn_id`、`recorded_at` 和最终 `project-turn` payload。
4. 在对象目标不明确时返回结构化阻塞结果，而不是冒险写入。
5. 保持 `timeline-memory` 继续作为唯一事实写入口。

## 3. 非目标

当前阶段不追求：

- 替代 `timeline-memory`
- 改造底层存储结构
- 让上游直接管理 `thread_id`
- 把模型输出直接当作最终可提交事实
- 一次处理多个相互独立的对象

## 4. 核心问题定义

`project-turn proxy` 解决的核心问题，不是“再包一层 CLI”，而是：

- 把上游世界中的事务语义
- 稳定映射成 `timeline-memory` 世界中的 `project-turn` 写入

其中最关键的难点是时间。

同样是自然语言里的“时间”，在底层可能对应完全不同的语义槽位：

- 预约时间
- 计划开始时间
- 截止时间
- 已发生时间
- 完成时间
- 实际记录时间

因此第一版设计中必须明确区分：

- 用户意图时间：`intent_time`
- 系统记录时间：`recorded_at`

二者不能混用。

## 5. 角色边界

### 5.1 上游调用方

上游调用方负责：

- 判断本轮大致属于哪种事务动作
- 给出事务领域
- 提供事务摘要
- 提供原始对话
- 在需要确认时与用户交互

上游调用方不负责：

- 生成 `turn_id`
- 生成 `thread_id`
- 直接构造 `project-turn`
- 决定时间最终落到 `plan_time` 还是 `fact_time`

### 5.2 Project-Turn Proxy

proxy 负责：

- 校验上游输入
- 生成系统级上下文字段
- 解析和规范化事务语义
- 做对象候选解析与确认流
- 生成最终 `project-turn` payload
- 调用 `timeline-memory`

### 5.3 Timeline-Memory

`timeline-memory` 继续负责：

- `project-turn` 写入
- thread / history / raw event 的稳定存储
- 幂等、恢复、并发和查询语义

## 6. 顶层接口

第一版只定义两个高层入口：

- `memory_proxy.inspect`
- `memory_proxy.resume`

### 6.1 `memory_proxy.inspect`

用途：

- 接收一轮新的事务语义输入
- 判断是否可直接形成可提交动作
- 或返回阻塞结果，等待用户确认

### 6.2 `memory_proxy.resume`

用途：

- 接续一个先前处于阻塞状态的待确认草案
- 结合用户补充信息推进解析

## 7. 输入约定

### 7.1 `memory_proxy.inspect` 输入草案

```json
{
  "context": {
    "user_id": "wechat:u123",
    "session_id": "conv_456",
    "request_key": "msg_789",
    "source": "wechat"
  },
  "semantic": {
    "action": "appointment",
    "domain": "medical",
    "summary": "预约下周二上午皮肤科复诊",
    "intent_time": {
      "text": "下周二上午",
      "precision": "part_of_day"
    },
    "conversation": {
      "user_text": "还是之前那家医院，下周二上午帮我约一下皮肤科",
      "assistant_text": "好的，我记一下"
    }
  },
  "thread_hint": null
}
```

### 7.2 `memory_proxy.resume` 输入草案

```json
{
  "context": {
    "user_id": "wechat:u123",
    "session_id": "conv_456",
    "request_key": "msg_790",
    "source": "wechat"
  },
  "resume": {
    "pending_draft_id": "pd_123",
    "conversation": {
      "user_text": "是更新之前那个，不是新建",
      "assistant_text": "好的，我按原来的那条更新"
    },
    "thread_hint": {
      "type": "candidate_ref",
      "value": "cand_001"
    }
  }
}
```

## 8. 字段定义

### 8.1 `context`

- `user_id`
  - 必填
  - 用户身份标识
- `session_id`
  - 必填
  - 会话身份标识
- `request_key`
  - 必填
  - 本次请求的稳定唯一标识
  - 优先使用上游消息 ID、事件 ID 或宿主回合 ID
  - 不要求自然枚举；只要求稳定、唯一、可追溯
- `source`
  - 可选
  - 请求来源渠道或宿主标识

### 8.2 `semantic`

- `action`
  - 必填
  - 事务动作
- `domain`
  - 必填
  - 事务领域
- `summary`
  - 必填
  - 本轮事务的简短摘要
  - 面向语义理解和匹配，不等于底层 `title`
- `intent_time`
  - 可选
  - 用户意图中的时间表达
- `conversation.user_text`
  - 必填
  - 用户原话
- `conversation.assistant_text`
  - 可选
  - 主代理回复原文

### 8.3 `thread_hint`

- 可选
- 仅允许传系统此前返回的候选引用
- 第一版推荐只支持：

```json
{
  "type": "candidate_ref",
  "value": "cand_001"
}
```

第一版不允许：

- 自由文本猜测
- 任意拼接的对象描述
- 上游随意指定底层 `thread_id`

## 9. 枚举建议

### 9.1 `action`

第一版建议使用以下枚举：

- `plan`
- `appointment`
- `complete`
- `cancel`
- `update`
- `note`

说明：

- `plan`
  - 未来计划性事务
- `appointment`
  - 预约、预定、约定某个时间点的事务
- `complete`
  - 事务完成
- `cancel`
  - 事务取消
- `update`
  - 对已有事务做补充或修改
- `note`
  - 记录型补充，不强制触发状态变化

### 9.2 `domain`

第一版建议使用以下枚举：

- `work`
- `learning`
- `medical`
- `finance`
- `legal`
- `personal`
- `other`

说明：

- `domain` 提供事务语境
- `domain` 不直接等于底层 `thread_kind`

### 9.3 `intent_time.precision`

第一版建议使用以下枚举：

- `exact`
- `date`
- `part_of_day`
- `range`
- `unknown`

## 10. 系统自动补字段

以下字段不由上游填写，而由 proxy 内部自动生成或决定：

- `turn_id`
- `recorded_at`
- 最终 `project-turn` payload
- 最终目标 `thread_id`
- 时间最终落位结果
- 底层 `status`
- 底层 `thread_kind`

### 10.1 `turn_id` 生成规则

第一版建议：

- `turn_id` 由 proxy 自动生成
- 生成依据为：
  - `user_id`
  - `session_id`
  - `request_key`

示例：

```text
ptp:wechat:u123:conv_456:msg_789
```

约束：

- 同一请求重试时，应保持同一个 `turn_id`
- 不要求自然枚举
- 重点是稳定、唯一、可追溯

### 10.2 `recorded_at`

- `recorded_at` 由 proxy 在接收请求时生成
- 它表示系统记录该事务的时间
- 它不等于用户意图时间

## 11. 映射原则

第一版先只定义高层映射原则，不在此文档中展开到底层字段实现细节。

### 11.1 时间映射

- `intent_time` 是用户意图时间
- `recorded_at` 是系统记录时间
- proxy 负责判断 `intent_time` 应落入：
  - 计划相关时间
  - 事实相关时间
  - 或截止类时间

### 11.2 动作映射

- `action = appointment`
  - 优先映射到未来安排语义
- `action = plan`
  - 优先映射到计划语义
- `action = complete`
  - 优先映射到完成语义
- `action = cancel`
  - 优先映射到取消语义
- `action = update`
  - 优先映射到已有事务补充或变更
- `action = note`
  - 优先映射到记录型补充

### 11.3 领域映射

- `domain` 影响解析、匹配和默认归类策略
- `domain` 不直接等于底层存储字段

### 11.4 摘要映射

- `summary` 是语义锚点
- 它可以作为：
  - 标题候选
  - 候选 thread 匹配输入
  - 默认内容摘要来源

## 12. 链路说明

推荐链路如下：

1. 上游调用方准备语义输入。
2. proxy 基于 `context` 生成 `turn_id` 和 `recorded_at`。
3. proxy 校验 `action`、`domain`、`summary`、`intent_time` 和原始对话。
4. proxy 尝试解析候选对象与最终动作。
5. 若目标明确，则生成最终 `project-turn` payload 并进入提交。
6. 若目标不明确，则返回阻塞结果和候选引用。
7. 上游调用方与用户交互后，通过 `memory_proxy.resume` 回传确认结果。
8. proxy 完成解析并提交到底层 `timeline-memory`。

## 13. 禁止项

第一版禁止上游直接传入以下字段：

- `turn_id`
- `thread_id`
- `thread_kind`
- `status`
- `plan_time`
- `fact_time`
- `content`
- `event_id`
- `recorded_at`
- 任意 `timeline-memory` 内部结构

## 14. 最小必填集合

`memory_proxy.inspect` 最小必填字段：

- `context.user_id`
- `context.session_id`
- `context.request_key`
- `semantic.action`
- `semantic.domain`
- `semantic.summary`
- `semantic.conversation.user_text`

## 15. 最小示例

```json
{
  "context": {
    "user_id": "wechat:u123",
    "session_id": "conv_456",
    "request_key": "msg_789"
  },
  "semantic": {
    "action": "complete",
    "domain": "work",
    "summary": "项目周报已完成",
    "conversation": {
      "user_text": "周报我已经写完了"
    }
  }
}
```

## 16. 后续待定项

以下事项在后续设计中继续细化：

1. `action` / `domain` 到底层 `status` / `thread_kind` 的确定性映射表。
2. `intent_time` 在不同动作下的时间槽位映射规则。
3. `thread_hint` 的完整生命周期和候选对象 contract。
4. `memory_proxy.inspect` / `resume` 的返回对象与错误码。
5. 与 pending draft、确认流和 supersede 规则的完整状态机。
