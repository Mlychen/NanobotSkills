# Timeline Memory Schema

## Public CLI Contract

公开 CLI 只保留 4 个命令：

- `project-turn`
- `get-thread`
- `list-threads`
- `list-thread-history`

使用时让 `--store-root` 指向一个新的 timeline 存储目录。

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
- 成功写入时，同一 turn 的 inbound / outbound raw event 共享 `correlation_id = turn_id`
- 成功写入时，inbound `causation_id = null`；若存在 outbound，则其 `causation_id = <turn_id>:in`
- `project-turn` 第一版不会自动推断 `confidence`；raw event、thread event ref、thread meta 的 `confidence` 默认保持 `null`
- `project-turn` 为当前 turn 生成 thread 引用时，只自动写 `primary` / `context`：
  - inbound → `primary`
  - outbound → `context`
- `recorded_at` 始终由系统在写入时生成，调用方不能指定
- 重放请求不得新增 raw event、不得增加 revision
- 但如果检测到同一 `turn_id` 的可恢复 partial write，允许在重试时补齐缺失的 outbound event 或 thread snapshot
- 重放与可恢复 repair 后，上述 `correlation_id` / `causation_id` / `event_refs.role` 语义必须保持不漂移
- 如果省略 `thread.thread_id`，系统会基于 `turn_id` 派生稳定且无碰撞的默认 thread ID

### `get-thread`

输入：

- `--thread-id`

输出：

- 单个 thread JSON 或 `null`

### `list-threads`

输入：

- `--thread-kind`
- `--status`
- `--last-event-at-or-after`
- `--last-event-at-or-before`
- `--limit`
- `--cursor`

输出：

- 未显式进入分页模式时：
  - thread 数组
- 显式进入分页模式时：
  - object
    - `items`: thread 数组
    - `next_cursor`: string 或 `null`
    - `has_more`: boolean

行为：

- 默认排序按 `last_event_at`、再按 `updated_at`、最后按 `thread_id` 倒序稳定给出
- `--last-event-at-or-after` / `--last-event-at-or-before` 基于 `last_event_at` 做闭区间过滤
- 只要显式使用任一时间窗口参数，缺失 `last_event_at` 的 thread 就不会命中过滤结果
- 未显式进入分页模式时，成功输出继续保持 thread 数组
- 显式传入 `--limit` 或 `--cursor` 时进入分页模式
- 分页模式默认页大小为 `100`，最大页大小为 `200`
- `--cursor` 是不透明游标，必须与生成它时使用的过滤条件保持一致
- 分页建立在当前稳定排序与过滤结果之上；继续翻页时顺序不得漂移

边界：

- `--limit` 必须是正整数，且不能大于 `200`
- 非法 `--cursor` 返回 `TM_INVALID_ARGUMENT`
- 非法时间戳或反向时间窗口返回 `TM_INVALID_ARGUMENT`

### `list-thread-history`

输入：

- `--thread-id`

输出：

- 历史 thread 快照数组

## Internal Storage Model

内部存储布局保持不变，但不再是 agent-facing contract：

- `raw_events.jsonl`
- `threads/tid_<thread_id_utf8_hex>.json`
- `thread_history/tid_<thread_id_utf8_hex>.jsonl`

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

约束：

- `payload` 必须是 object
- `payload` 不能包含标准化 `plan_time` / `fact_time`
- `project-turn` 写入时，`correlation_id` 固定等于所属 turn 的 `turn_id`
- `project-turn` 写入时，inbound `causation_id = null`
- `project-turn` 写入时，若存在 outbound，则其 `causation_id` 固定回指同 turn 的 inbound `event_id`
- 第一版 `confidence` 默认保持 `null`；重放与 repair 不会自动补值

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

当前 `project-turn` 自动生成规则：

- inbound 事件引用写为 `primary`
- 同 turn outbound 事件引用写为 `context`
- `evidence` / `derived` 在第一版中保留枚举位，但不会由 `project-turn` 自动生成
- `confidence` 第一版默认保持 `null`

### ThreadMeta

- `created_by`
- `updated_by`
- `revision`
- `confidence`

## CLI I/O Contract

- 写操作：`--input <json-file>` 或 stdin JSON
- 读操作：stdout 输出 JSON
- 所有公开命令都支持可选 `--read-mode {compat,strict}`
- 默认 `read-mode` 为 `compat`
- `compat`：JSONL 坏行与非对象行会被跳过，保持兼容读取
- `strict`：JSONL 任一坏行都会导致命令失败
- 命令执行阶段失败：`stdout` 为空，`stderr` 输出单个 JSON 对象，退出码非 `0`
- 失败对象固定包含：
  - `ok = false`
  - `error.code`
  - `error.category`
  - `error.message`
  - `error.details`
- 首批稳定错误码：
  - `TM_INVALID_ARGUMENT`
  - `TM_READ_FAILED`
  - `TM_TURN_CONFLICT`
  - `TM_PARTIAL_WRITE`
  - `TM_METADATA_CONFLICT`
  - `TM_STORE_BUSY`
  - `TM_INTERNAL`
- `strict` 读取失败时，`error.code = TM_READ_FAILED`，且 `error.message` 继续保留 `failed to read JSONL: <path> line <n>: <reason>` 关键短语
- `argparse` 参数解析失败暂不在该合同内，当前仍输出默认 `usage: ...` 纯文本 `stderr`，退出码为 `2`
