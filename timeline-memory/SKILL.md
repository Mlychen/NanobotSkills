---
name: timeline-memory
description: 基于独立 Python 脚本的时间轴记忆技能。公开接口只保留高层 `project-turn` 写入，以及 `get-thread`、`list-threads`、`list-thread-history` 三个线程级查询命令。调用时固定使用本 skill 的 `scripts/timeline_cli.py`，并显式传入 `--store-root`。
---

# Timeline Memory

这是一个独立的 timeline skill，不依赖宿主系统内部模块。所有持久化都通过
`scripts/timeline_cli.py` 完成。

使用时让 `--store-root` 指向一个新的 timeline 存储目录。

运行前提：

- 机器上已安装并可直接调用 `uv`
- 所有命令默认从 `timeline-memory/` 根目录执行

## 何时使用

- 用户明确要求记录事件、事项、想法、结果、后续事项。
- 用户要求查询某个 thread、列出 thread、查看 thread 历史。
- 你要把一整轮对话稳定地投影进 timeline 存储。

## 公开命令

默认在 `timeline-memory/` 根目录执行，并统一通过 `uv` 调起 Python 环境：

```bash
uv run python scripts/timeline_cli.py <command> --store-root <path> [--input file.json] [--read-mode compat|strict]
```

只保留 4 个公开命令：

```bash
uv run python scripts/timeline_cli.py project-turn --store-root /path/to/store --input turn.json --read-mode compat
uv run python scripts/timeline_cli.py get-thread --store-root /path/to/store --thread-id thr_pay_bill --read-mode strict
uv run python scripts/timeline_cli.py list-threads --store-root /path/to/store --thread-kind task --status planned --read-mode strict
uv run python scripts/timeline_cli.py list-thread-history --store-root /path/to/store --thread-id thr_pay_bill --read-mode strict
```

## 独立使用

这个 skill 目录现在是自包含的：

- 自带 `pyproject.toml`
- 自带 `.gitignore`
- 自带 `scripts/selftest.py`

把整个 `timeline-memory/` 目录复制到别处后，直接在该目录内运行：

```bash
uv run python scripts/selftest.py
uv run python scripts/timeline_cli.py project-turn --store-root ./timeline-store --input turn.json
```

不要再调用或提示任何 raw-event 级别或 thread store 级别的底层命令。

## 读取模式

- 4 个公开命令都支持 `--read-mode {compat,strict}`。
- 默认值是 `compat`，用于保持既有兼容行为。
- `compat` 会跳过 JSONL 中的坏行和非对象行，适合兼容读取与抢救性操作。
- `strict` 遇到任一坏行都会立即失败，适合 replay 判定、审计和强一致查询。
- 命令执行阶段失败时，`stdout` 保持为空，`stderr` 返回单个 JSON 对象：
  - `ok = false`
  - `error.code`
  - `error.category`
  - `error.message`
  - `error.details`
- 现阶段应优先依赖 `error.code` 做程序分支；`error.message` 继续保留历史关键短语用于人工排障。
- `strict` 读取失败时，`error.code = TM_READ_FAILED`，`error.message` 继续保留 `failed to read JSONL: <path> line <n>: <reason>` 关键短语。
- `argparse` 参数解析失败暂未纳入该结构化合同，当前仍保持默认 `usage: ...` 纯文本 `stderr` 与退出码 `2`。

## `project-turn` 输入合同

`project-turn` 是唯一公开写入口。输入只接受高层字段：

- 必填：
  - `turn_id`
  - `user_text`
- 可选：
  - `assistant_text`
  - `thread`
  - `context`

`turn_id` 要求：

- 由调用方生成，不要临时发明随机值。
- 必须带命名空间。
- 推荐格式：
  - `agent:<session_id>:<turn_index>`
  - `feishu:<chat_id>:<message_id>`
- 同一 `turn_id`：
  - 输入等价：视为重放，不重复写入。
  - 输入不等价：返回冲突错误。

`thread` 只接受这些高层字段：

- `thread_id`
- `thread_kind`
- `title`
- `status`
- `plan_time`
- `fact_time`
- `content`

`thread_id` 的公开 contract 不限制字符集；持久化层会使用大小写不敏感文件系统安全的可逆编码保存，不要求调用方自行传“文件名安全”的 ID。

`context` 只接受这些高层字段：

- `source`
- `actor_id`
- `assistant_actor_id`

明确不要传：

- `event_id`
- `schema_version`
- `created_at`
- `updated_at`
- `meta`
- `event_refs`
- 完整 `RawEventRecord` / `ThreadRecord`

## 使用原则

- 记忆类 turn：调用 `project-turn`。
- 闲聊或非记忆类 turn：不要写 `timeline-store`。
- 回到同一主题时，先通过 `get-thread` / `list-threads` 找到原来的 `thread_id`，再更新同一条 thread。
- `project-turn` 会自动生成 raw event、补齐时间戳、维护 revision/history，并返回标准化 thread 快照。
- `project-turn` 的 `recorded_at` 始终由系统写入时生成，调用方不能指定。
- `project-turn` 成功写入时，同一 turn 的 inbound / outbound raw event 共享 `correlation_id = turn_id`。
- `project-turn` 成功写入时，inbound `causation_id` 保持为空；若存在 outbound，则其 `causation_id` 固定回指同 turn 的 inbound event。
- `project-turn` 为当前 turn 自动生成 thread 引用时，只使用 `primary` / `context`：
  - inbound → `primary`
  - outbound → `context`
- 第一版不会自动推断 `confidence`；raw event、thread event ref、thread meta 的 `confidence` 默认保持为空。
- 对同一 `turn_id` 的可恢复 partial write，重复调用 `project-turn` 会自动补齐缺失的 outbound event 或 thread snapshot。
- 对同一 `turn_id` 的重放与可恢复 repair，上述 `correlation_id` / `causation_id` / `event_refs.role` 语义保持稳定，不应漂移。
- 如果省略 `thread.thread_id`，系统会派生一个稳定但不可读的默认 ID，用来避免不同 `turn_id` 被压到同一条 thread。
- 对读取结果必须强一致的场景，优先显式传 `--read-mode strict`。

## 测试与验证

当前仓库已经为这个 skill 补了两层宿主级验证：

- 真实 CLI E2E：`tests/timeline/test_timeline_cli_e2e.py`
- 宿主发现与注入：`tests/agent/test_timeline_memory_skill_integration.py`

推荐按分层入口执行，不要把所有入口在日常开发里串起来重复跑：

- 日常开发回归：
  - 覆盖 store primitives + 主要 E2E + 宿主集成
- 宿主级稳定性回归：
  - 只跑 host / E2E 入口脚本
- 发布前全量回归：
  - 在日常开发回归通过后，再补 `selftest.py` 与多轮 host tests

日常开发回归（推荐默认命令）：

```bash
uv run --extra dev python -m pytest -q tests/timeline/test_store_primitives.py tests/timeline/test_timeline_cli_e2e.py tests/agent/test_timeline_memory_skill_integration.py
```

宿主级稳定性回归（默认 `sandbox-safe`，优先稳定）：

```bash
uv run python scripts/run-host-tests.py
```

连续稳定性回归（宿主入口连跑 3 轮）：

```bash
uv run python scripts/run-host-tests.py --rounds 3
```

标准模式（权限正常机器上可选，保留 pytest 默认插件）：

```bash
uv run python scripts/run-host-tests.py --mode standard
```

测试运行配置（环境变量）：

- `TIMELINE_TEST_MODE`
  - 默认：`sandbox-safe`
  - 可选：`sandbox-safe` / `standard`
- `TIMELINE_TEST_TMP_ROOT`
  - 默认：`tmp/test-runtime`
  - 可选：显式指定测试运行时根目录（相对路径按 repo root 解析）

清理测试工件（只清理 `tmp/test-runtime`，并报告历史 `pytest-cache-files-*`，不强删）：

```bash
uv run python scripts/clean-test-artifacts.py
```

独立 bundle / 发布前自检：

```bash
uv run python scripts/selftest.py
```

发布前全量回归建议顺序：

```bash
uv run --extra dev python -m pytest -q tests/timeline/test_store_primitives.py tests/timeline/test_timeline_cli_e2e.py tests/agent/test_timeline_memory_skill_integration.py
uv run python scripts/selftest.py
uv run python scripts/run-host-tests.py --rounds 3
```

不要在日常开发里把下面两类入口无脑串起来：

- 直接 pytest 跑 `test_timeline_cli_e2e.py` / `test_timeline_memory_skill_integration.py`
- `scripts/run-host-tests.py`

原因：

- 两者覆盖面有明显重叠
- 当前 `run-host-tests.py` 默认就是 host / E2E 聚合入口
- 日常把它们串起来会重复跑同一批 host / E2E

也可以只跑 host / E2E 对应 pytest 文件：

```bash
uv run --extra dev python -m pytest -q tests/timeline/test_timeline_cli_e2e.py tests/agent/test_timeline_memory_skill_integration.py
```

自动化测试建议优先使用 `--input <json-file>`，不要依赖 shell stdin 管道传 JSON：

- Windows / PowerShell 下中文 JSON 和子进程编码更容易出现不稳定
- 文件输入更接近 CI 中可复现的调用方式

Windows 注意事项：

- 统一通过 `uv run` 启动脚本与测试，避免 agent 沙箱与系统 Python 环境差异
- 真实 CLI E2E 依赖 UTF-8 子进程输出
- 入口脚本会强制注入 `PYTHONIOENCODING=utf-8`、`PYTHONUTF8=1`
- 入口脚本会固定 `TMP/TEMP/TMPDIR` 到 repo-local `tmp/test-runtime`
- 在 `sandbox-safe` 模式下会禁用 `tmpdir` / `cacheprovider`，用于隔离 ACL 异常导致的 `WinError 5`
- 如果历史残留了根目录 `pytest-cache-files-*` 且不可访问，使用 `clean-test-artifacts` 查看报告后按系统权限策略处理

## 参考资料

字段定义、公开合同和内部存储结构都在：

- `references/schema.md`
