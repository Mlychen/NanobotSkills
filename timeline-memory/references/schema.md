# Timeline Memory Schema

## Public CLI Contract

公开 CLI 只保留 4 个命令：

- `project-turn`
- `get-thread`
- `list-threads`
- `list-thread-history`

### `project-turn`

用途：

- 高层写入一整轮 turn
- 自动生成 inbound/outbound raw event
- 自动创建或更新 thread
- 自动维护 revision / history / timestamps
- 自动处理同一 `turn_id` 的重放幂等

输入：

- 必填：
  - `turn_id`
  - `user_text`
- 可选：
  - `assistant_text`
  - `thread`
  - `context`

`turn_id` 规则：

- 由调用方生成，必须带命名空间
- 推荐格式：
  - `agent:<session_id>:<turn_index>`
  - `feishu:<chat_id>:<message_id>`
- 同一 `turn_id`
  - 输入等价：视为重放，不重复写入
  - 输入不等价：返回冲突错误

`thread` 允许字段：

- `thread_id`
- `thread_kind`
- `title`
- `status`
- `plan_time`
- `fact_time`
- `content`

说明：

- `thread_id` 公开不限制字符集。
- 内部持久化会使用可逆、大小写不敏感文件系统安全的编码保存 thread 路径。

`plan_time` 允许字段：

- `planned_start`
- `planned_end`
- `due_at`
- `all_day`
- `rrule`

`fact_time` 允许字段：

- `occurred_at`
- `completed_at`

`content` 允许字段：

- `notes`
- `outcome`
- `followups`
- `items`

`context` 允许字段：

- `source`
- `recorded_at`
- `actor_id`
- `assistant_actor_id`

明确不允许调用方传：

- `event_id`
- `schema_version`
- `created_at`
- `updated_at`
- `meta`
- `event_refs`
- 完整 `RawEventRecord`
- 完整 `ThreadRecord`

输出：

- `ok`
- `idempotent_replay`
- `recorded_event_ids`
- `thread`

行为：

- 有 `assistant_text` 时写两条 raw event；没有则只写 inbound
- 有 `thread` 时执行 upsert；没有则不改 thread 快照
- 内部 event id 由 `turn_id` 派生，例如 `<turn_id>:in`、`<turn_id>:out`
- 重放请求不得新增 raw event、不得增加 revision
- 但如果检测到同一 `turn_id` 的可恢复 partial write，允许在重试时补齐缺失的 outbound event 或 thread snapshot
- 如果省略 `thread.thread_id`，系统会基于 `turn_id` 派生稳定且无碰撞的默认 thread ID

### `get-thread`

输入：

- `--thread-id`

输出：

- 单个 thread JSON 或 `null`

额外约束：

- 如果命中 legacy 文件名碰撞，必须显式报错，不能返回错误 thread

### `list-threads`

输入：

- `--thread-kind`
- `--status`

输出：

- thread 数组

### `list-thread-history`

输入：

- `--thread-id`

输出：

- 历史 thread 快照数组

额外约束：

- 如果命中 legacy 文件名碰撞，必须显式报错，不能混入其他 thread 的历史

## Internal Storage Model

内部存储布局保持不变，但不再是 agent-facing contract：

- `raw_events.jsonl`
- `threads/<thread_id>.json`
- `thread_history/<thread_id>.jsonl`

### RawEventRecord

- `event_id`
- `event_type`
- `recorded_at`
- `source`
- `actor_kind`
- `actor_id`
- `correlation_id`
- `causation_id`
- `raw_text`
- `payload`
- `confidence`
- `schema_version`
- `created_at`

约束：

- `payload` 必须是 object
- `payload` 不能包含标准化 `plan_time` / `fact_time`

### ThreadRecord

- `thread_id`
- `thread_kind`
- `title`
- `status`
- `plan_time`
- `fact_time`
- `content`
- `event_refs`
- `meta`
- `first_event_at`
- `last_event_at`
- `created_at`
- `updated_at`

### ThreadPlanTime

- `planned_start`
- `planned_end`
- `due_at`
- `all_day`
- `rrule`

### ThreadFactTime

- `occurred_at`
- `completed_at`

### ThreadContent

- `notes`
- `outcome`
- `followups`
- `items`

### ThreadEventRef

- `event_id`
- `role`
- `added_at`
- `added_by`
- `confidence`

支持的 `role`：

- `primary`
- `context`
- `evidence`
- `derived`

### ThreadMeta

- `created_by`
- `updated_by`
- `revision`
- `confidence`

## CLI I/O Contract

- 写操作：`--input <json-file>` 或 stdin JSON
- 读操作：stdout 输出 JSON
- 失败：stderr 输出错误，退出码非 0
