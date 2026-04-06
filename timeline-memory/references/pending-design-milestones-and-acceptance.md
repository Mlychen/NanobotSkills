# Timeline Memory 未完成设计项里程碑与验收标准

## 目标

将当前已识别但未完全落地的设计项转化为可执行迭代，确保实现与以下原则一致：

- 一致性优先
- 幂等与可恢复
- 公开合同稳定
- 可观测、可运维

## 里程碑 M1：强一致读取策略（已完成）

完成情况：

- 已为 JSONL 读取引入 `strict` / `compat` 双模式
- 已明确默认读取模式为 `compat`
- 已统一 strict 模式下的读取失败错误语义
- 已补齐 CLI E2E、宿主测试与 `selftest.py` 回归场景

范围：

- 为 JSONL 读取引入 strict 模式（坏行即失败）
- 保留兼容模式（坏行跳过）并明确默认行为
- 统一读取失败错误语义

验收标准：

- strict 模式下，任一坏行都会导致命令失败并返回明确错误
- 兼容模式下，行为与当前实现保持一致
- 两种模式行为可通过自动化测试稳定复现

## 里程碑 M2：多文件写入原子性

范围：

- 明确 `project-turn` 的写入提交协议
- 降低 raw events 与 thread snapshot/history 之间的中间态暴露窗口
- 保留现有 replay 恢复能力并补充协议约束

设计结论：

- 继续保持公开 CLI 合同不变，只在内部为 `project-turn` 增加事务化提交协议
- 核心提交单元为 `turn_id`，事务目录固定为 `store-root/_txn/project_turn/`
- raw events 改为批量写入，尽量把当前 inbound-only 暴露窗口压缩到最小
- thread 更新顺序改为“先原子替换 snapshot，再补 history”
- 通过事务文件保证任意中断后都能识别阶段、继续补齐，并避免重复 history

非目标：

- M2 不追求“真正跨文件原子提交”
- M2 的目标是“任意中间态都可识别、可补齐、可收敛”
- 查询命令的外部合同与读取结果结构不在本阶段修改范围内

验收标准：

- 故障注入场景下不会产生不可修复状态
- 任意中断后重复提交同一 `turn_id` 都能收敛到唯一一致结果
- 覆盖 inbound-only、missing-snapshot、snapshot-restore 等关键恢复路径

当前实现观察：

- 当前 `project-turn` 入口在 [timeline_cli.py](file:///d:/Code/NanobotSkills/timeline-memory/scripts/timeline_cli.py#L628-L708) 中已经调整为“先查事务，再走 legacy replay，最后进入新事务主路径”
- 当前事务执行在 [timeline_cli.py](file:///d:/Code/NanobotSkills/timeline-memory/scripts/timeline_cli.py#L517-L607) 中按 `prepared -> raw_committed -> snapshot_committed -> history_committed -> committed` 推进
- 当前 thread 写入底层能力在 [store.py](file:///d:/Code/NanobotSkills/timeline-memory/scripts/store.py#L458-L544) 中已经具备：
  - snapshot 单独写入
  - history 单独追加
  - normalize-for-write 归一化能力
- 当前仍保留的兼容窗口：
  - 事务文件不存在时，仍允许回退到既有 replay/repair 路径
  - 故障注入覆盖已补关键阶段，但尚未把所有矩阵场景都写成更细粒度用例

事务文件约束：

- 每个 `turn_id` 只允许一个事务文件，文件名使用可逆编码后的 `turn_id`
- 事务文件至少记录：
  - `turn_id`
  - `fingerprint`
  - `stage`
  - `recorded_at`
  - `thread_id`
  - `required_event_ids`
  - `has_thread`
  - `baseline_summary`
  - `target_snapshot` 或可重建它的最小数据
  - `history_entry`
- `stage` 固定为：`prepared`、`raw_committed`、`snapshot_committed`、`history_committed`、`committed`
- 若发现同一 `turn_id` 的事务文件已存在：
  - `fingerprint` 不同：返回冲突
  - `fingerprint` 相同：按事务阶段继续恢复，不新建第二份事务
- 事务文件是恢复主依据；当事务文件不存在时，才回退到现有 replay 推断逻辑

协议草案：

- 阶段 P0：准备事务
  - 为 `turn_id` 创建事务文件，先落盘提交意图，再开始任何公开数据写入
  - 此阶段完成后，恢复流程已经可以知道当前 turn 的目标与边界
- 阶段 P1：批量写 raw events
  - 将 inbound/outbound 一次性组装为单个批次，在一次文件打开中顺序写入 `raw_events.jsonl`
  - 不再分两次独立调用 append，尽量把 inbound-only 暴露窗口缩到最小
  - 完成后把事务阶段推进到 `raw_committed`
- 阶段 P2：生成目标 thread 状态
  - 基于 baseline thread 计算目标 snapshot
  - 对更新场景，同时把“旧 snapshot 作为 history 追加项”序列化进事务文件
  - 此阶段只做计算和落事务元数据，不直接暴露到公开读取路径
- 阶段 P3：原子替换 snapshot
  - 先把新 snapshot 写到同目录临时文件
  - 使用同卷原子替换将临时文件切换为正式 snapshot
  - 完成后把事务阶段推进到 `snapshot_committed`
- 阶段 P4：补 history
  - 仅在更新 thread 且确实需要保留旧版本时追加 history
  - history 追加前先根据事务文件检查该 revision 是否已入 history，避免恢复时重复写入
  - 此阶段不允许覆盖已提交 snapshot，也不允许重建新的 target snapshot
  - 完成后把事务阶段推进到 `history_committed`
- 阶段 P5：完成提交
  - 将事务文件标记为 `committed`
  - 清理临时文件和已完成事务文件

阶段不变量：

- `prepared`：公开存储中可以还没有任何本次 turn 的痕迹
- `raw_committed`：要求 raw events 已完整，不允许只补写其中一部分
- `snapshot_committed`：要求 snapshot 已是目标版本，恢复时只能向前补 history 和清理
- `history_committed`：要求 snapshot 与 history 都已收敛，剩余工作只能是事务收尾
- 任意阶段重复执行都必须保持幂等，不新增重复 raw event，不新增重复 history

恢复规则：

- `project-turn` 启动时先检查本 `turn_id` 是否存在未完成事务
- 若事务处于 `prepared`：重新执行 raw 批量提交
- 若事务处于 `raw_committed`：直接推进 snapshot 与 history 阶段，不重写 raw events
- 若事务处于 `snapshot_committed`：只补 history 与清理，不回退 snapshot
- 若事务处于 `history_committed`：只做事务完成标记与清理
- 若事务处于 `committed`：按幂等重放直接返回最终结果
- 若事务文件丢失但落盘状态符合旧实现的可恢复模式：继续保留现有 replay 修复逻辑
- 恢复完成后，对同一 `turn_id` 的重复提交必须返回唯一收敛结果：
  - 不重写已提交 raw events
  - 不回退已替换 snapshot
  - 不追加重复 history

实现拆分原则：

- 先补内部能力，再改 `project-turn` 主路径，再补恢复兼容，最后补故障注入测试
- 先只改写入主路径，不在同一阶段同时修改查询命令
- replay/repair 改造采用“事务优先，旧恢复逻辑兜底”，保证兼容历史数据

当前阶段状态：

- 已完成底层原语收口：
  - 事务文件已具备最小 schema 校验、同 `turn_id` 下 `fingerprint` 冲突拦截、阶段前进约束
  - raw batch 已具备“部分已提交可补齐、内容冲突即失败”的幂等语义
  - snapshot 已具备 temp + replace 与 thread 归属校验
  - history append 已补最小去重保护，避免重复追加同内容条目
- 已完成主路径切换：
  - `timeline_cli.py` 已引入事务 payload 构造、prepare、advance 与 txn 执行 helper
  - `project-turn` 新写入主路径已切为显式 P0-P5 阶段机
  - 恢复入口已改为“事务优先，旧路径兜底”
  - 更新 thread 时已经按“先 snapshot、后 history”顺序收敛
- 当前剩余工作：
  - 将故障注入测试矩阵继续细化，补齐更多“阶段内中断 + 重放”组合
  - 视需要清理 legacy replay 与 txn 路径间的重复判断逻辑

后续推进边界：

- 下一阶段只改 `project-turn` 写入与恢复主路径
- 不修改公开 CLI 输入输出合同
- 不修改查询命令与查询结果结构
- 不在本阶段引入新的并发语义

推荐落地顺序：

- 第一段：已完成
  - 已在 `timeline_cli.py` 中新增事务构造、prepare、阶段推进辅助函数
  - 已统一封装 `prepared`、`raw_committed`、`snapshot_committed`、`history_committed`、`committed` 的推进逻辑
- 第二段：已完成
  - `project-turn` 新写入流程已切为显式 P0-P5 阶段机
  - P0 写事务
  - P1 调用 `append_raw_events_batch()`
  - P2 计算并落盘 `target_snapshot` / `history_entry`
  - P3 执行 snapshot 原子替换
  - P4 追加 history
  - P5 标记 `committed` 并清理事务文件
- 第三段：已完成
  - `project-turn` 启动时已优先检查事务文件
  - 事务存在时按阶段继续恢复
  - 事务不存在时回退到现有 replay/repair 推断逻辑
- 第四段：已完成
  - 已覆盖 `prepared`、`raw_committed`、`snapshot_committed`、`history_committed` 四类事务中断恢复
  - 已覆盖 `prepared`、`raw_committed`、`snapshot_committed`、`history_committed` 后连续执行恢复 + 重放不重复追加 history 的关键场景
  - 已开始收敛 `timeline_cli.py` 中 txn 恢复与 legacy replay 的重复判断
  - 已将 replay 推断与恢复流程收敛为显式结构：`ReplayRawState`、`ReplayThreadState`、`ReplayRecoveryPlan`、`ReplayResult`
  - `execute_replay_recovery()` 已改为消费显式 recovery plan，不再依赖内部松散字典约定
  - 宿主测试入口已优先复用当前解释器执行 `pytest`，仅在当前环境缺少 `pytest` 时回退到 `uv run --extra dev`
  - 已继续把 replay recovery 拆为 raw/thread 两类独立 helper，并用 `thread_action` 收口线程恢复分支
  - 已开始复用 replay 与 txn 两条恢复路径中的 thread write plan 构造，统一 target snapshot / history entry 的生成逻辑
  - 已开始复用 replay raw 补齐与 txn raw 阶段推进中的共享 raw commit helper
  - 已补“阶段内中断 + 恢复 + 再重放”组合回归，验证最终 raw / thread / history 状态收敛一致

建议代码组织：

- 先在 `timeline_cli.py` 内引入小粒度 helper，避免把所有阶段判断堆进 `cmd_project_turn()`
- 建议拆出：
  - 事务 payload 构造函数
  - 事务 prepare / advance 函数
  - txn 驱动恢复函数
  - legacy replay 兜底函数
- 最终入口顺序建议为：
  - 先查事务
  - 再查 legacy replay
  - 最后进入新提交主路径

实现拆分：

- 第一步：在 `store.py` 增加事务文件读写、snapshot 临时写入与原子替换能力（已完成）
- 第二步：在 `TimelineStore` 增加 `append_raw_events_batch()`，统一批量写 raw events（已完成）
- 第三步：在 `timeline_cli.py` 中抽事务 helper，并把 `project-turn` 改写为显式阶段机（已完成）
- 第四步：把现有 replay/repair 改成“事务优先，旧路径兜底”（已完成）
- 第五步：补故障注入测试与回归测试（已完成）

测试矩阵：

- 创建 thread 场景故障注入：
  - 准备事务后中断（已覆盖）
  - raw 批量写入后中断（已覆盖）
  - snapshot 替换后中断（已覆盖）
- 更新 thread 场景故障注入：
  - raw 完成但 snapshot 未写（已覆盖）
  - snapshot 已替换但 history 未追加（已覆盖）
  - history 已追加但事务未清理（已覆盖）
- 现有恢复路径回归：
  - inbound-only（已覆盖）
  - no-thread（已覆盖）
  - missing-snapshot（已覆盖）
  - snapshot-restore（已覆盖）
  - existing-thread repair（已覆盖）
- 新增幂等恢复断言：
  - 同一 `turn_id` 连续重放 2 次以上不产生重复 history（`prepared` / `raw_committed` / `snapshot_committed` / `history_committed` 已覆盖）
  - 任一阶段中断后再次执行都收敛到同一最终 snapshot 与 raw event 集合（关键阶段已覆盖）
  - 更新 thread 时 history 条目数与 revision 增量严格一致（已覆盖）

## 里程碑 M3：并发写入语义

范围：

- 定义同一 `store-root` 的并发写策略
- 实现文件锁或等价并发控制机制
- 明确冲突、重试、失败返回规则

验收标准：

- 双进程并发写同一 thread 不出现静默覆盖
- 双进程并发写同一 `turn_id` 结果可预测且幂等
- 并发测试可稳定通过，无偶发数据错乱

设计结论：

- M3 先采用“`store-root` 级单写者串行化”策略，而不是一开始就做细粒度 thread 锁
- 公开 CLI 输入输出合同先保持不变，并发控制只作为内部实现细节引入
- 锁作用域覆盖整个 `project-turn` 写入主路径：`recover/replay -> prepare txn -> execute txn -> cleanup`
- 读取命令暂不加锁；M3 只保证“写不互相踩踏”，不额外承诺读到线性化瞬时视图
- 同一 `turn_id` 继续沿用 M2 的 txn + fingerprint 幂等语义；M3 负责把这套语义放到双进程竞争下依然变成确定结果

为什么先做 store-root 级串行写：

- 当前 `project-turn` 一次写入会同时触碰 `raw_events.jsonl`、`threads/*.json`、`thread_history/*.jsonl`、`_txn/project_turn/*.json`
- 仅给 thread 加锁仍无法解决 `raw_events.jsonl` 追加和事务文件推进的跨文件竞争
- 当前 `prepare_project_turn_txn()` 会先读 baseline thread，再生成后续写入计划；没有串行化时，这个 baseline 很容易变成陈旧快照
- 当前 `ThreadStore.normalize_for_write()` 基于“当前 revision + 1”计算新 revision；双写者并发时会出现两个进程都基于同一旧 revision 计算，从而导致静默覆盖风险
- 先把所有写入串成单写者，能以最小实现复杂度消除当前最危险的竞态；后续若吞吐成为问题，再评估拆成更细粒度锁

非目标：

- M3 不引入跨主机、跨网络文件系统的一致性保证
- M3 不修改查询命令返回结构
- M3 不在本阶段开放新的 CLI 并发参数
- M3 不追求“读写完全隔离”，只处理写写冲突和同 `turn_id` 幂等竞争

当前实现进展：

- `project-turn` 写入主路径已在 [cmd_project_turn](file:///d:/Code/NanobotSkills/timeline-memory/scripts/timeline_cli.py#L912-L951) 中包裹 `store.project_turn_write_lock(...)`，`recover_or_replay_project_turn()`、`prepare_project_turn_txn()`、`execute_project_turn_txn()` 已统一纳入同一临界区
- `store-root` 级写锁原语已在 [store.py](file:///d:/Code/NanobotSkills/timeline-memory/scripts/store.py#L164-L213) 中落地，并通过 [TimelineStore.project_turn_write_lock()](file:///d:/Code/NanobotSkills/timeline-memory/scripts/store.py#L676-L679) 暴露给 CLI 主路径
- 事务文件写入仍由 [ProjectTurnTxnStore.write()](file:///d:/Code/NanobotSkills/timeline-memory/scripts/store.py#L384-L404) 负责单 `turn_id` 幂等合并，但外层已由单写者锁消除不同写者同时推进阶段的竞争
- raw event 追加与 thread snapshot/history 写入逻辑本身未改公开合同，仍分别位于 [store.py](file:///d:/Code/NanobotSkills/timeline-memory/scripts/store.py#L310-L335) 与 [store.py](file:///d:/Code/NanobotSkills/timeline-memory/scripts/store.py#L592-L603)，但已由同一锁序列化保护
- 首轮真实并发回归已补齐：
  - 同 thread 不同 `turn_id` 串行收敛：[test_timeline_cli_e2e.py:L1420-L1487](file:///d:/Code/NanobotSkills/timeline-memory/tests/timeline/test_timeline_cli_e2e.py#L1420-L1487)
  - 同 `turn_id` 等价并发幂等：[test_timeline_cli_e2e.py:L1490-L1536](file:///d:/Code/NanobotSkills/timeline-memory/tests/timeline/test_timeline_cli_e2e.py#L1490-L1536)
  - 同 `turn_id` 非等价并发冲突：[test_timeline_cli_e2e.py:L1539-L1585](file:///d:/Code/NanobotSkills/timeline-memory/tests/timeline/test_timeline_cli_e2e.py#L1539-L1585)
  - 宿主层 busy timeout 冒烟：[test_timeline_memory_skill_integration.py:L182-L208](file:///d:/Code/NanobotSkills/timeline-memory/tests/agent/test_timeline_memory_skill_integration.py#L182-L208)

推荐落地顺序：

- 第一段：补底层锁原语（已完成）
  - 在 `store-root/_locks/` 下引入固定锁文件路径，例如 `project_turn.lock`
  - 优先使用操作系统级独占文件锁；进程异常退出时由 OS 自动释放，避免遗留“僵尸锁文件”
  - 锁文件正文只保留排障元数据：`pid`、`turn_id`、`thread_id`、`acquired_at`
  - 对外先只提供内部 helper / context manager，不改 CLI 参数

- 第二段：把 `project-turn` 主路径整体包进临界区（已完成）
  - 将 [cmd_project_turn](file:///d:/Code/NanobotSkills/timeline-memory/scripts/timeline_cli.py#L912-L951) 改为“先拿锁，再执行 replay/prepare/txn/cleanup”
  - 明确要求：加锁后重新读取 txn 与 baseline，禁止在锁外先做 replay 判断
  - 保持现有 M2 阶段机不变，只改变其执行前后的并发边界
  - 对无 thread 的 turn 也保持同一路径加锁，因为仍会写 `raw_events.jsonl` 与 txn 文件

- 第三段：定义等待、失败与重试语义（已完成首版）
  - 默认行为采用“短等待 + 周期重试 + 超时失败”
  - 超时后返回明确 busy 错误文本，例如“store is busy with another writer”
  - 进入 M4 时，需要把 busy / timeout 从当前稳定文本进一步映射为固定错误码，避免把文案本身演化为隐式合同
  - 相同 `turn_id` 且 payload 等价的竞争提交：一个进程完成真实提交，其他进程在获得锁后走 txn/replay 幂等返回
  - 相同 `turn_id` 但 payload 不等价：保持现有 fingerprint 冲突失败
  - 不同 `turn_id` 但同一 thread：后进入者等待前者完成，然后基于最新 snapshot 继续，revision 严格递增

- 第四段：补真实并发测试矩阵（已完成首轮）
  - 在 pytest 中新增真实 subprocess 并发 helper，避免仅靠进程内 runner 造成“假并发”
  - 用环境变量形式加入受控故障注入点，确保可以稳定复现“拿到锁后暂停”“snapshot 前暂停”等竞争窗口
  - 至少覆盖：
    - 同一 thread、不同 `turn_id` 同时写入，最终 snapshot / history / revision 一致
    - 同一 `turn_id`、相同 payload 同时写入，最终只写一份结果且返回可预测
    - 同一 `turn_id`、不同 payload 同时写入，一个成功、一个冲突失败
    - 前一个写者长时间持锁时，后一个写者超时失败且不产生脏数据
  - 回归入口保持与统一完成定义一致：pytest、`run-host-tests.py`、必要时 `selftest.py`

分步实现计划：

- P1：锁原语与 busy 错误（已完成）
  - 新增跨平台文件锁 helper
  - 约定锁路径与锁元数据 schema
  - 提供固定超时与退避策略
  - 验收：双进程争抢同一锁时，一个成功进入，另一个稳定等待或超时失败

- P2：`project-turn` 接入串行化（已完成）
  - 让 replay / prepare / execute / cleanup 全部在锁内完成
  - 确保恢复路径与正常写入路径共用同一锁协议
  - 验收：同一 thread 的并发双写不再出现 revision 丢失或 history 异常

- P3：并发冲突矩阵回归（已完成首轮）
  - 新增真实 subprocess 并发 E2E
  - 补宿主层至少一条并发冒烟
  - 验收：同 `turn_id` 幂等、不同 payload 冲突、持锁超时三类场景稳定复现

当前验证结果：

- `uv run --extra dev python -m pytest -q tests/timeline/test_store_primitives.py tests/timeline/test_timeline_cli_e2e.py tests/agent/test_timeline_memory_skill_integration.py` 已通过
- `uv run python scripts/run-host-tests.py` 已通过
- `uv run python scripts/selftest.py` 已通过
- 当前工作区相关文件 diagnostics 为 `0`

当前判断：

- M3 第一版核心目标已经落地：同一 `store-root` 的 `project-turn` 写入已具备单写者串行化语义
- 首轮并发测试已覆盖最关键的四类行为：同 thread 串行收敛、同 `turn_id` 幂等、同 `turn_id` 冲突、busy timeout
- M3 剩余工作不再是“是否需要并发控制”，而是是否继续扩展测试矩阵，以及在 M4 中把当前稳定错误文本收口为结构化错误码

当前建议：

- M3 第一版不要急于做 per-thread 锁、raw-events 锁、txn 锁三层组合
- 先用 `store-root` 级单写者把语义做对，再看是否值得为吞吐量拆锁
- 如果后续确实需要细粒度并发，下一轮应基于 M3 已验证的 busy / timeout / replay 语义继续演进，而不是直接推翻第一版

## 里程碑 M4：结构化错误模型

完成情况：

- 已在 [timeline_cli.py](file:///d:/Code/NanobotSkills/timeline-memory/scripts/timeline_cli.py) 落地统一错误对象、错误码映射与结构化 `stderr` 输出
- 已保持成功路径 `stdout` 合同不变，失败时统一返回 `ok=false` 的 JSON 错误对象
- 已将 busy / conflict / partial write / read failed / invalid argument / metadata conflict 纳入首批稳定错误码
- 已补齐 E2E、宿主测试、`selftest.py` 与公开合同文档同步

范围：

- 定义错误码体系与错误类别
- CLI 输出稳定的结构化错误信息
- 保留可读错误文本用于人工排障

验收标准：

- 每类核心失败路径都有固定错误码
- 上层可仅依赖错误码完成重试与分支处理
- 历史关键错误文案兼容或提供清晰迁移说明

设计结论：

- M4 采用“稳定错误码 + 结构化 stderr JSON + 兼容可读 message”三层模型
- 失败时 `stdout` 保持为空，结构化错误统一写到 `stderr`
- `project-turn`、`get-thread`、`list-threads`、`list-thread-history` 四个公开命令都复用同一错误出口
- `message` 继续保留当前人类可读文案；机器侧只依赖 `error.code`
- busy / timeout / conflict / strict read failure 等当前已被测试覆盖的失败面，优先进入第一批稳定错误码

为什么现在做 M4：

- 当前 CLI 总出口仍是 [timeline_cli.py:L1213-L1219](file:///d:/Code/NanobotSkills/timeline-memory/scripts/timeline_cli.py#L1213-L1219) 的统一失败出口；在 M4 之前，外部只能靠文本做分支
- 当前测试中已经有多处对错误字符串做子串断言，例如：
  - [test_timeline_cli_e2e.py:L1213-L1417](file:///d:/Code/NanobotSkills/timeline-memory/tests/timeline/test_timeline_cli_e2e.py#L1213-L1417)
  - [test_timeline_memory_skill_integration.py:L182-L208](file:///d:/Code/NanobotSkills/timeline-memory/tests/agent/test_timeline_memory_skill_integration.py#L182-L208)
- M3 已引入 busy / timeout 语义；如果不尽快结构化，这些语义会先以错误文案形式沉淀成隐式合同
- 当前 store 层、replay 层、CLI 入口层都直接抛 `ValueError` / `RuntimeError`，但没有统一的“错误类别 -> 错误码 -> 输出模型”映射层

非目标：

- M4 不修改成功返回结构
- M4 不在本阶段为每个错误分配不同进程退出码；除参数解析层外，命令失败仍保持退出码 `1`
- M4 不一次性重写所有内部异常类型；优先增加统一包装与映射层
- M4 不改变现有恢复策略、并发策略与查询语义

错误输出合同：

- 失败时 `stdout` 必须为空
- 失败时 `stderr` 输出单个 JSON 对象，建议形态：

```json
{
  "ok": false,
  "error": {
    "code": "TM_STORE_BUSY",
    "category": "busy",
    "message": "store is busy with another writer: D:\\store\\_locks\\project_turn.lock",
    "details": {
      "retryable": true,
      "turn_id": "agent:host:busy:0002"
    }
  }
}
```

- 字段约束：
  - `ok`
    - 固定为 `false`
  - `error.code`
    - 稳定错误码，供上层程序分支
  - `error.category`
    - 粗粒度错误类别，便于聚合统计与重试策略
  - `error.message`
    - 面向人工排障，继续保留当前关键文案短语
  - `error.details`
    - 非稳定扩展字段；允许附加 `turn_id`、`thread_id`、`path`、`line_no`、`retryable` 等

错误类别建议：

- `invalid_argument`
  - 输入 payload 非法、字段不允许、schema 校验失败
- `read_failed`
  - strict 读取失败、文件损坏、路径内容与 schema 不匹配
- `conflict`
  - 同 `turn_id` 非等价重放、txn 指纹冲突、元数据不一致
- `recovery_failed`
  - 部分写入检测、恢复前提不成立、快照与 raw state 不一致
- `busy`
  - 锁竞争超时、当前 store 存在活跃写者
- `internal`
  - 理论上不应暴露给调用方的未分类异常

第一批稳定错误码：

- `TM_INVALID_ARGUMENT`
  - 对应非法输入、未知字段、payload/schema/业务参数非法
- `TM_READ_FAILED`
  - 对应 strict 读失败、JSONL 坏行、snapshot/history/txn 加载失败
- `TM_TURN_CONFLICT`
  - 对应同 `turn_id` 非等价 payload、txn fingerprint 冲突
- `TM_PARTIAL_WRITE`
  - 对应 partial write detected、missing inbound、thread snapshot partially reflects current turn
- `TM_METADATA_CONFLICT`
  - 对应 inconsistent thread metadata、missing `_timeline_memory` metadata 等元数据问题
- `TM_STORE_BUSY`
  - 对应锁等待超时、store is busy with another writer
- `TM_INTERNAL`
  - 其他未归类异常的保底码

映射原则：

- 同一错误码可以覆盖多条历史 message，但同一 message 前缀不要在不同错误码间来回漂移
- `message` 允许继续包含当前断言依赖的关键短语，例如：
  - `different payload already recorded`
  - `partial write detected`
  - `failed to read JSONL`
  - `store is busy with another writer`
- 这样即使调用方短期内尚未切到 JSON 解析，基于子串的旧检测逻辑也更容易平滑过渡
- `details` 中的具体路径、行号、锁文件名允许继续变化，但其字段名一旦公开后应保持稳定

当前实现观察：

- 当前参数解析错误主要由 `argparse` 直接处理；该路径会保留现状，不纳入第一步改造范围
- 当前参数解析错误与业务输入错误要分开看：
  - `argparse` 层的 CLI 解析失败暂不纳入第一步结构化改造
  - 命令执行阶段的 payload/schema/业务参数错误进入 `TM_INVALID_ARGUMENT`
- 当前命令执行错误已统一落到 [main](file:///d:/Code/NanobotSkills/timeline-memory/scripts/timeline_cli.py#L1213-L1219) 的通用异常出口，并在这里完成“异常 -> 结构化错误对象”的集中映射
- 当前 store 层已有比较明确的错误前缀，可作为首版映射基础：
  - [store.py:L310-L335](file:///d:/Code/NanobotSkills/timeline-memory/scripts/store.py#L310-L335)
  - [store.py:L384-L404](file:///d:/Code/NanobotSkills/timeline-memory/scripts/store.py#L384-L404)
- 当前 replay / recovery 路径也已有较稳定的冲突文案，可先按 message 前缀分组映射，再逐步收敛为显式异常类型

推荐落地顺序：

- 第一段：定义错误对象与错误码表
  - 在 CLI 层新增统一错误模型，例如 `TimelineCliError`
  - 明确 `code`、`category`、`message`、`details` 四个核心字段
  - 整理首批稳定错误码表与 message 前缀映射关系

- 第二段：改造统一错误出口
  - 将 [main](file:///d:/Code/NanobotSkills/timeline-memory/scripts/timeline_cli.py#L1213-L1219) 从纯文本 stderr 改为结构化 JSON stderr
  - 保持命令失败退出码 `1`
  - 确保成功路径 `stdout` 合同完全不变

- 第三段：优先接入核心失败面
  - 锁竞争超时 -> `TM_STORE_BUSY`
  - `turn_id` 冲突 -> `TM_TURN_CONFLICT`
  - partial write / recovery mismatch -> `TM_PARTIAL_WRITE`
  - strict read failure / 文件损坏 -> `TM_READ_FAILED`
  - 非法字段 / schema 问题 -> `TM_INVALID_ARGUMENT`

- 第四段：补兼容回归与迁移说明
  - 将现有“字符串包含”断言升级为“错误码断言 + message 兼容断言”
  - 宿主测试补“仅依赖错误码分支”用例
  - 在 `SKILL.md` / `schema.md` 中同步错误输出合同

分步实现计划：

- P1：已完成
  - 定义首批错误码
  - 定义 category 枚举与详情字段约定
  - 验收：文档与代码内映射表一致

- P2：已完成
  - 将通用异常出口改为 JSON stderr
  - 保留 `message` 的历史可读文本
  - 验收：成功输出不变，失败输出具备稳定 `error.code`

- P3：已完成
  - busy / timeout
  - conflict / replay conflict
  - partial write / recovery mismatch
  - strict read / load failure
  - invalid argument
  - 验收：关键 E2E 与宿主测试不再仅依赖 message 才能分支

- P4：已完成
  - 更新 [SKILL.md](file:///d:/Code/NanobotSkills/timeline-memory/SKILL.md) 与 [schema.md](file:///d:/Code/NanobotSkills/timeline-memory/references/schema.md)
  - 提供迁移说明：旧调用方可继续读取 `message`，新调用方应切到 `error.code`
  - 验收：文档、CLI、测试三者一致

当前建议：

- M4 第一版优先做“稳定错误码 + 统一 stderr JSON”，不要先追求很复杂的层级化异常体系
- 先覆盖最有业务价值的失败面：busy、conflict、partial write、strict read、invalid argument
- 等错误码稳定后，再决定是否细分更多子码，避免一开始把码表切得过碎

## 里程碑 M5：高级事件语义

背景：

- 当前数据模型其实已经为高级事件语义预留了字段，但写入路径尚未定义稳定规则：
  - [RawEventRecord](file:///d:/Code/NanobotSkills/timeline-memory/scripts/models.py#L49-L97) 已包含 `correlation_id`、`causation_id`、`confidence`
  - [ThreadEventRef](file:///d:/Code/NanobotSkills/timeline-memory/scripts/models.py#L165-L195) 已包含 `role` 与 `confidence`
  - [ThreadMeta](file:///d:/Code/NanobotSkills/timeline-memory/scripts/models.py#L198-L224) 已包含聚合级 `confidence`
- 但当前 `project-turn` 写入主路径里，这些字段还没有稳定赋值策略：
  - [build_raw_event()](file:///d:/Code/NanobotSkills/timeline-memory/scripts/timeline_cli.py#L331-L376) 当前只稳定写 `event_id / event_type / actor / raw_text / payload`
  - [merge_event_refs()](file:///d:/Code/NanobotSkills/timeline-memory/scripts/timeline_cli.py#L185-L204) 当前只按“首条 `primary`、后续 `context`”生成最小引用关系
- 如果不先定义 M5，后续查询增强、跨轮归因与证据链展示都会建立在不稳定的隐式约定上

设计结论：

- M5 第一版只做“最小可用高级事件语义”，不追求完整知识图谱或复杂因果网络
- 语义单元继续以单次 `project-turn` 为核心；第一版重点保证“单轮内稳定、重放后不漂移、修复后可恢复”
- `correlation_id` 用于标识同一 turn 内的一组原始事件；同一 `turn_id` 生成的 inbound/outbound 必须共享同一 `correlation_id`
- `causation_id` 只表达直接因果；第一版只在 outbound 事件上显式回指当前 turn 的 inbound `event_id`
- `confidence` 第一版采用“保守传播”策略：未显式提供时不强行推断，不为了看起来完整而写入伪精确数值
- `event_refs.role` 第一版只稳定使用 `primary` 与 `context`；`evidence`、`derived` 在文档中保留语义位，但暂不由 `project-turn` 自动生成

为什么现在做 M5：

- M2 已把多文件写入收敛为可恢复阶段机，M3 已把写写竞争序列化，M4 已把失败输出结构化；现在最缺的是“写进去的事件之间到底是什么语义关系”
- 当前公开 schema 已暴露 `correlation_id`、`causation_id`、`confidence`、`event_refs.role` 等字段能力，但没有说明何时写、何时留空、重放后是否必须一致，见 [schema.md](file:///d:/Code/NanobotSkills/timeline-memory/references/schema.md#L154-L164) 与 [schema.md](file:///d:/Code/NanobotSkills/timeline-memory/references/schema.md#L208-L228)
- 如果先做 M6 查询增强，再回头定义事件语义，查询行为会被迫兼容一批历史不一致数据，成本更高

非目标：

- M5 不在第一版开放调用方直接传入任意 `correlation_id` / `causation_id`
- M5 不在第一版支持跨 thread、跨 turn 的复杂依赖图建模
- M5 不自动推导 `evidence` / `derived` 引用，也不引入新的公开 CLI 命令
- M5 不改变 M2/M3 已确立的事务提交协议与恢复阶段划分
- M5 不在第一版重写 thread 聚合逻辑，只先定义原始事件与最小 `event_refs` 的一致语义

字段语义约束：

- `correlation_id`
  - 作用：把同一轮 turn 内相关 raw event 归到同一相关组
  - 第一版规则：默认取 `turn_id`
  - 约束：同一 `turn_id` 的 inbound/outbound 必须一致；重放与 repair 后不得变化
- `causation_id`
  - 作用：表达“本事件直接由哪个上游事件触发”
  - 第一版规则：
    - inbound：留空
    - outbound：固定指向当前 turn 的 inbound `event_id`
  - 约束：若 outbound 存在，则其 `causation_id` 必须稳定为 `build_event_id(turn_id, "inbound")`
- `RawEventRecord.confidence`
  - 作用：表达单条原始事件的可信度
  - 第一版规则：默认留空；后续若引入显式来源，再按稳定映射写入
  - 约束：回放与 repair 不得凭空补值或改变已有值
- `ThreadEventRef.role`
  - 作用：表达 thread 与事件的关系类型
  - 第一版规则：
    - inbound 事件引用写为 `primary`
    - 同轮 outbound 事件引用写为 `context`
    - `evidence` / `derived` 暂不自动生成
  - 约束：重放与 repair 后角色不得漂移
- `ThreadEventRef.confidence`
  - 第一版规则：默认留空，不从 raw event 自动拷贝
- `ThreadMeta.confidence`
  - 第一版规则：默认留空，不在本阶段定义聚合算法

当前实现观察：

- 当前 raw event 序列化结构已经支持上述字段，但 `project-turn` 入口没有赋值逻辑，因此历史数据会大量留空，见 [models.py](file:///d:/Code/NanobotSkills/timeline-memory/scripts/models.py#L49-L97)
- 当前 thread 引用关系只表达“当前轮第一个事件是 `primary`，其余是 `context`”，这对单轮写入已经足够，但还没有把它上升为公开稳定规则，见 [merge_event_refs()](file:///d:/Code/NanobotSkills/timeline-memory/scripts/timeline_cli.py#L185-L204)
- 当前 replay 判定依赖 `_timeline_memory.turn_id / fingerprint / role / thread_id`，M5 需要确保新增语义字段不会破坏现有幂等与恢复判断，见 [build_timeline_meta()](file:///d:/Code/NanobotSkills/timeline-memory/scripts/timeline_cli.py#L165-L182) 与 [ensure_replay_metadata_matches()](file:///d:/Code/NanobotSkills/timeline-memory/scripts/timeline_cli.py#L307-L324)
- 当前 schema 文档对 `ThreadEventRef.role` 只列了枚举值，没有定义“谁来写、何时写、哪些命令会产生哪些 role”，这是 M5 首先要补齐的合同缺口，见 [schema.md](file:///d:/Code/NanobotSkills/timeline-memory/references/schema.md#L197-L220)

推荐落地顺序：

- 第一段：先把语义规则写清
  - 固定 `correlation_id`、`causation_id`、`confidence`、`event_refs.role` 的第一版规则
  - 明确默认值、允许留空的字段与“不自动生成”的边界
- 第二段：只改 `project-turn` 写入主路径
  - 在 raw event 构造阶段填充 `correlation_id` / `causation_id`
  - 在 thread 引用构造阶段固化 `primary/context` 规则
- 第三段：验证 replay / repair 一致性
  - 同一 `turn_id` 幂等重放后字段不漂移
  - M2 中断恢复后字段与一次成功提交结果一致
- 第四段：再决定是否扩语义面
  - 评估是否需要开放显式 `confidence`
  - 评估是否需要在后续里程碑中引入 `evidence` / `derived`

范围：

- 在 `project-turn` 中定义 `correlation_id`、`causation_id`、`confidence` 的写入策略
- 明确 `event_refs` 角色在证据链中的约束
- 对多轮归因建立最小可用语义规则

验收标准：

- 相关字段在写入、重放、修复后语义一致
- `event_refs` 角色使用符合约束且可测试
- 回放不会破坏归因链和置信度字段

分步实现计划：

- P1：语义合同定稿
  - 在 `references/schema.md` 与里程碑文档中明确字段定义、默认值与边界
  - 验收：对每个字段都能回答“何时写、写什么、何时留空、重放后是否必须相同”

- P2：最小写入实现
  - 在 `project-turn` raw event 构造中写入稳定 `correlation_id` / `causation_id`
  - 固化 `event_refs.role` 的 `primary/context` 规则
  - 验收：同一 turn 首次写入结果满足第一版语义合同

- P3：回放与恢复收敛
  - 补齐重放、partial write repair、txn recovery 后字段一致性的断言
  - 验收：一次成功提交、重复重放、故障恢复三条路径收敛到同一语义结果

- P4：公开合同同步
  - 更新 `schema.md` 与 `SKILL.md` 中相关字段描述
  - 明确第一版未开放的高级语义能力
  - 验收：文档、实现、测试三者一致

当前建议：

- M5 第一版要刻意收敛，不要一开始就引入“用户可自定义因果图”
- 先把“单 turn 内 inbound/outbound 的相关性与直接因果”做成稳定合同
- `confidence` 先保持保守：允许为空，不做未经定义的自动推断
- `evidence` / `derived` 先保留枚举位，等有明确上游语义来源后再开放自动生成

## 里程碑 M6：查询能力增强

范围：

- 为 `list-threads` 增加分页能力
- 增加时间窗口过滤能力
- 增加可选文本检索能力

验收标准：

- 新增参数全部有明确 contract 与边界行为
- 在数据量增长场景下查询结果稳定且顺序可预测
- 新查询能力不破坏既有调用方式

## 里程碑 M7：测试执行耗时优化

目标：

- 在不牺牲当前回归覆盖面的前提下，显著降低日常测试等待时间
- 优先压缩热点 E2E 中重复 CLI 子进程启动的成本
- 将“日常回归”和“发版前全量验证”拆成更清晰的执行层次

当前基线：

- `uv run --extra dev python -m pytest -q tests/timeline/test_store_primitives.py tests/timeline/test_timeline_cli_e2e.py tests/agent/test_timeline_memory_skill_integration.py`
  - 实测约 `56.957s`
- `uv run python scripts/selftest.py`
  - 实测约 `23.420s`
- `uv run python scripts/run-host-tests.py`
  - 实测约 `53.252s`
- 当前整条串行命令：
  - `pytest + selftest + run-host-tests`
  - 实测约 `130.978s`
- 热点画像：
  - Top 20 慢测合计约 `37.69s`
  - Top 20 慢测合计约触发 `148` 次 CLI 子进程
  - 折算平均约 `0.255s / CLI`

当前热点：

- [test_project_turn_stage_recovery_then_replay_matches_reference_state](file:///d:/Code/NanobotSkills/timeline-memory/tests/timeline/test_timeline_cli_e2e.py#L707-L774)
  - 单测约 `2.76s` 到 `2.92s`
  - 每个参数化 case 约触发 `12` 次 CLI 子进程
- [test_project_turn_repeated_recovery_from_snapshot_committed_txn_keeps_single_history_entry](file:///d:/Code/NanobotSkills/timeline-memory/tests/timeline/test_timeline_cli_e2e.py#L448-L515)
  - 单测约 `2.38s`
  - 约触发 `9` 次 CLI 子进程
- [test_project_turn_repeated_recovery_from_history_committed_txn_keeps_single_history_entry](file:///d:/Code/NanobotSkills/timeline-memory/tests/timeline/test_timeline_cli_e2e.py#L582-L652)
  - 单测约 `2.29s`
  - 约触发 `9` 次 CLI 子进程
- [test_source_normalization_and_partial_write_recovery](file:///d:/Code/NanobotSkills/timeline-memory/tests/timeline/test_timeline_cli_e2e.py#L845-L922)
  - 单测约 `2.22s`
  - 约触发 `8` 次 CLI 子进程
- [test_host_adapter_e2e_write_and_read_contract](file:///d:/Code/NanobotSkills/timeline-memory/tests/agent/test_timeline_memory_skill_integration.py#L120-L139)
  - 单测约 `1.02s`
  - 约触发 `4` 次 CLI 子进程

根因判断：

- 当前热点测试大多不是单次业务逻辑特别重，而是同一测试内反复调用 CLI
- 测试 helper 当前主要通过子进程调用 CLI，见：
  - [conftest.py](file:///d:/Code/NanobotSkills/timeline-memory/tests/conftest.py#L37-L79)
  - [test_timeline_memory_skill_integration.py](file:///d:/Code/NanobotSkills/timeline-memory/tests/agent/test_timeline_memory_skill_integration.py#L66-L93)
- `_assert_turn_state_matches_reference()` 每次会额外触发 `4` 次 CLI 读取，见 [test_timeline_cli_e2e.py](file:///d:/Code/NanobotSkills/timeline-memory/tests/timeline/test_timeline_cli_e2e.py#L78-L107)
- 当前日常命令还叠加了 `pytest`、`selftest.py`、`run-host-tests.py` 三层入口，存在明显重复覆盖

P1 当前进展：

- 已完成：
  - [conftest.py](file:///d:/Code/NanobotSkills/timeline-memory/tests/conftest.py) 中的 `CliRunner` 已优先使用当前解释器直启 CLI，仅在当前解释器不可用时回退到 `uv run python`
  - [test_timeline_memory_skill_integration.py](file:///d:/Code/NanobotSkills/timeline-memory/tests/agent/test_timeline_memory_skill_integration.py) 中的 `TimelineMemoryHostAdapter` 已切到相同策略
  - [selftest.py](file:///d:/Code/NanobotSkills/timeline-memory/scripts/selftest.py) 内部对子 CLI 的调用也已切到相同策略
- P1 实测结果：
  - 三文件 pytest：
    - 优化前约 `56.957s`
    - 优化后约 `46.714s`
    - 下降约 `17.98%`
  - `selftest.py`：
    - 优化前约 `23.420s`
    - 优化后约 `19.474s`
    - 下降约 `16.85%`
  - `run-host-tests.py`：
    - 优化前约 `53.252s`
    - 优化后约 `44.509s`
    - 下降约 `16.42%`
  - 当前判断：
    - `selftest.py` 与 `run-host-tests.py` 已获得稳定收益
    - 三文件 pytest 尚未达到“至少下降 `20%`”的验收线，需要继续推进 P2 / P3

P2 当前进展：

- 已完成：
  - [conftest.py](file:///d:/Code/NanobotSkills/timeline-memory/tests/conftest.py) 中的 `CliRunner` 已切为进程内执行 `timeline_cli.main(argv)`
  - E2E 测试已不再为每次 `cli_runner.run_json()` / `cli_runner.expect_failure()` 额外启动真实子进程
  - 宿主集成测试中的 [TimelineMemoryHostAdapter](file:///d:/Code/NanobotSkills/timeline-memory/tests/agent/test_timeline_memory_skill_integration.py#L24-L96) 仍保留真实 subprocess 路径，继续承担 CLI 合同冒烟角色
- P2 实测结果：
  - 三文件 pytest：
    - P1 后约 `46.714s`
    - P2 后约 `24.009s`
    - 相比 P1 再下降约 `48.61%`
    - 相比原始基线 `56.957s` 总降幅约 `57.85%`
  - `run-host-tests.py`：
    - P1 后约 `44.509s`
    - P2 后约 `21.500s`
    - 相比 P1 再下降约 `51.70%`
    - 相比原始基线 `53.252s` 总降幅约 `59.63%`
  - `selftest.py`：
    - 约 `19.743s`
    - 与 P1 基本持平，符合“本阶段主要优化 pytest / host tests”的预期
- P2 后热点观察：
  - 先前 `2.76s` 到 `2.92s` 的恢复类热点，已下降到约 `0.61s` 到 `0.71s`
  - 当前最慢项开始转向：
    - 个别复杂恢复场景本身的文件 IO / 状态构造
    - `scratch_root` 清理带来的 teardown 成本
    - 仍保留真实 subprocess 的宿主集成测试
- 当前判断：
  - P2 已达到“显著压缩热点 E2E 子进程成本”的目标
  - 三文件 pytest 与 `run-host-tests.py` 都已低于 M7 中设定的目标区间
  - 下一步可以进入 P3，继续减少高频对照读取与 teardown 成本

P3 当前进展：

- 已完成：
  - [test_timeline_cli_e2e.py](file:///d:/Code/NanobotSkills/timeline-memory/tests/timeline/test_timeline_cli_e2e.py) 中的 `_assert_turn_state_matches_reference()` 已改为直接读取 store 中的 snapshot / history 文件，不再走额外 CLI 查询
  - `prepared-reference` 与 `stage-recovery-reference` 两组对照测试的基线 snapshot / history 读取已改为直接读文件
  - `source_normalization_and_partial_write_recovery` 与 `existing_thread_inbound_only_replay_recovers_next_revision` 已不再通过单独 template store 生成 inbound raw line，而是直接复用共享 helper 构造最小 inbound record
- P3 实测结果：
  - 三文件 pytest：
    - P2 后约 `24.009s`
    - P3 后约 `23.257s`
    - 相比 P2 再下降约 `3.13%`
    - 相比原始基线 `56.957s` 总降幅约 `59.17%`
  - `run-host-tests.py`：
    - P2 后约 `21.500s`
    - P3 后约 `20.668s`
    - 相比 P2 再下降约 `3.87%`
    - 相比原始基线 `53.252s` 总降幅约 `61.19%`
- P3 后热点观察：
  - `test_source_normalization_and_partial_write_recovery`
    - call 阶段已从约 `0.66s` 下降到约 `0.40s`
    - 但 teardown 仍约 `0.55s`，说明剩余热点更多来自临时目录清理而非查询次数
  - `_assert_turn_state_matches_reference()` 相关测试已不再额外消耗 CLI 读取路径，剩余耗时主要回到文件 IO 与状态构造
  - 当前最慢项开始集中在：
    - `raw_committed` 恢复类用例本身
    - 宿主集成真实 subprocess 冒烟
    - Windows 下目录清理 teardown
- 当前判断：
  - P3 已完成“减少高频对照读取并收缩部分复合场景成本”的目标
  - 继续优化的边际收益开始下降，下一阶段更适合转向命令分层与回归入口整理

P4 当前进展：

- 已完成：
  - [SKILL.md](file:///d:/Code/NanobotSkills/timeline-memory/SKILL.md) 已按“日常开发回归 / 宿主级稳定性回归 / 发布前全量回归”三层入口重写测试说明
  - 已明确把 `store_primitives + 主要 E2E + 宿主集成` 作为默认日常回归命令
  - 已明确 `selftest.py` 的用途是独立 bundle / 发布前自检
  - 已明确 `run-host-tests.py` 的用途是 host / E2E 聚合入口与稳定性回归入口
  - 已明确标注：不要在日常开发里把直接 pytest 的 host/E2E 与 `run-host-tests.py` 串起来重复执行
- P4 当前产出：
  - 默认日常回归命令：
    - `uv run --extra dev python -m pytest -q tests/timeline/test_store_primitives.py tests/timeline/test_timeline_cli_e2e.py tests/agent/test_timeline_memory_skill_integration.py`
  - 宿主级稳定性回归：
    - `uv run python scripts/run-host-tests.py`
  - 发布前全量回归：
    - `uv run --extra dev python -m pytest -q tests/timeline/test_store_primitives.py tests/timeline/test_timeline_cli_e2e.py tests/agent/test_timeline_memory_skill_integration.py`
    - `uv run python scripts/selftest.py`
    - `uv run python scripts/run-host-tests.py --rounds 3`
- 当前判断：
  - P4 已完成“分层整理测试入口并明确边界”的目标
  - 下一步主要剩余的是 P5：在文档中沉淀最终 profile 对比结论，收口本轮优化结果

P5 当前进展：

- 已完成：
  - 已在同一机器、同一命令口径下重新测量三文件 pytest、`selftest.py`、`run-host-tests.py` 与整条串行命令
  - 已将 P1-P4 的分阶段收益与最终结果收口为统一结论
- 最终 profile 对比：
  - 三文件 pytest：
    - 初始基线约 `56.957s`
    - P5 当前约 `23.257s`
    - 总降幅约 `59.17%`
  - `selftest.py`：
    - 初始基线约 `23.420s`
    - 当前约 `19.743s`
    - 总降幅约 `15.70%`
  - `run-host-tests.py`：
    - 初始基线约 `53.252s`
    - 当前约 `20.668s`
    - 总降幅约 `61.19%`
  - 原始整条串行命令：
    - 初始基线约 `130.978s`
    - 当前约 `63.088s`
    - 总降幅约 `51.83%`
- 最终热点结论：
  - 主要收益来自 P2：
    - E2E 从真实子进程切到进程内 runner
  - P1 提供稳定但中等幅度收益：
    - 去掉测试内部重复的 `uv run python` 启动层
  - P3 进一步压掉了 reference-state 对照读取与部分 template store 构造成本
  - P4 的收益主要体现在流程层：
    - 降低日常误用重复入口的概率
    - 让优化后的命令分层能够稳定落地
- 目标达成情况：
  - 三文件 pytest：
    - 已低于目标 `35s`
  - `run-host-tests.py`：
    - 已低于目标 `30s` 到 `35s`
  - 整条串行命令：
    - 已低于目标 `75s` 到 `90s`
- 当前判断：
  - M7 的核心目标已完成
  - 后续若继续优化，优先级应低于并发语义、错误模型等功能性里程碑
  - 剩余可优化项主要集中在：
    - Windows 下目录清理 teardown
    - 少量仍保留真实 subprocess 的宿主冒烟
    - 个别 `raw_committed` 恢复场景本身的文件 IO 成本

分步方案：

- 第一步：收口测试子进程启动方式
  - 将测试 helper 中的 `uv run python ...` 优先替换为当前解释器直启
  - 在已运行于 `uv run` 环境的测试进程内，优先使用 `sys.executable` 调用 [timeline_cli.py](file:///d:/Code/NanobotSkills/timeline-memory/scripts/timeline_cli.py#L984-L995)
  - 保留“当前环境无 `pytest` 或无必要依赖时才回退到 `uv run`”的兜底逻辑
  - 当前状态：
    - 已完成当前解释器优先直启
    - 已保留解释器不可用时的 `uv run python` 回退
  - 预期收益：
    - 先压掉每次 CLI 调用外层 `uv` 解析与环境引导成本
    - 不改业务逻辑与公开合同，风险最低
  - 验收标准：
    - [test_timeline_cli_e2e.py](file:///d:/Code/NanobotSkills/timeline-memory/tests/timeline/test_timeline_cli_e2e.py) 与 [test_timeline_memory_skill_integration.py](file:///d:/Code/NanobotSkills/timeline-memory/tests/agent/test_timeline_memory_skill_integration.py) 全绿
    - `run-host-tests.py` 保持兼容当前用法
    - 三文件 pytest 总耗时相比当前基线至少下降 `20%`

- 第二步：引入进程内 CLI runner，缩小真实子进程覆盖面
  - 在测试层新增“进程内执行 `timeline_cli.main(argv)`”的 runner
  - 将大部分恢复类、回放类、严格读取类用例切到进程内 runner
  - 保留少量真实 subprocess 用例作为 CLI 合同冒烟
  - 当前状态：
    - 已完成 E2E 测试层的进程内 runner 切换
    - 宿主集成测试仍保留真实 subprocess 冒烟
  - 推荐保留真实子进程的范围：
    - 输入文件解析
    - stderr / exit code 合同
    - UTF-8 编码与 PowerShell 路径行为
  - 预期收益：
    - 针对 `8` 到 `12` 次 CLI 调用的热点测试，直接减少解释器与进程边界开销
  - 验收标准：
    - Top 10 热点测试总耗时相比当前基线至少下降 `35%`
    - 至少保留一组真实 CLI 冒烟测试覆盖 `project-turn`、`get-thread`、`list-threads`、`list-thread-history`

- 第三步：瘦身高频对照读取与复合场景
  - 重新审视 [_assert_turn_state_matches_reference()](file:///d:/Code/NanobotSkills/timeline-memory/tests/timeline/test_timeline_cli_e2e.py#L78-L107) 的使用范围
  - 对只验证最终存储收敛的场景，优先直接对 store 文件结构断言
  - 将“source normalization + partial write recovery”这类复合测试拆成更单一的断言路径
  - 将公共前置构造沉淀为 helper，避免每个测试重复做多轮读写
  - 当前状态：
    - 已完成 reference-state 对照路径的直接文件断言
    - 已将部分“只为生成 inbound raw line” 的 template store 替换为共享 helper
    - teardown 仍是剩余热点之一，继续压缩需要谨慎评估测试工件清理策略
  - 预期收益：
    - 减少热点测试中不必要的 `4` 次对照读取
    - 让 profile 更容易稳定反映真实热点
  - 验收标准：
    - Top 20 热点测试中的平均 CLI 子进程数从约 `7.4` 次降到 `5` 次以内
    - 不降低关键恢复矩阵覆盖

- 第四步：分层整理测试入口
  - 将日常回归命令与全量回归命令分开维护
  - 推荐分层：
    - 日常开发：
      - `store_primitives + 主要 E2E + 宿主集成`
    - 发布前回归：
      - 日常开发命令
      - `selftest.py`
      - `run-host-tests.py --rounds 3` 或等价稳定性入口
  - 明确 `run-host-tests.py` 与直接 pytest 的边界，避免重复跑同一批 host/E2E
  - 当前状态：
    - 已在 [SKILL.md](file:///d:/Code/NanobotSkills/timeline-memory/SKILL.md) 中完成入口分层与命令示例更新
    - 已明确 daily / prerelease / host-stability 三种入口的职责边界
  - 预期收益：
    - 降低开发阶段总等待时间
    - 保留发版前的独立 bundle / 宿主级验证
  - 验收标准：
    - 日常回归默认命令不再重复执行同一批 host/E2E
    - 文档中明确标注每个入口的用途与适用时机

建议落地顺序：

- P1：先改测试 helper 的 CLI 启动方式
- P2：补进程内 runner，并迁移恢复类热点测试
- P3：压缩热点测试里的重复读取与复合场景
- P4：整理并发布新的测试命令分层
- P5：重新 profile，并把前后对比结果回写文档

建议目标：

- 三文件 pytest：
  - 从约 `56.957s` 压到 `35s` 左右或更低
- `run-host-tests.py`：
  - 从约 `53.252s` 压到 `30s` 到 `35s`
- 当前整条串行命令：
  - 从约 `130.978s` 压到 `75s` 到 `90s`

风险与边界：

- 不能为了降耗时而完全取消真实子进程测试，否则会丢失编码、路径、退出码和宿主注入层的真实覆盖
- 进程内 runner 需要谨慎处理 `stdout` / `stderr` 捕获与异常到退出码的映射，保证与真实 CLI 合同一致
- 对 store 文件做断言时，仍需保留最小 CLI 读取断言，避免只验证内部实现细节而漏掉公开查询语义

完成定义：

- profile 数据更新并写回本文档
- 所有优化前后对比都基于同一机器、同一命令口径
- 回归命令与用途在 [SKILL.md](file:///d:/Code/NanobotSkills/timeline-memory/SKILL.md) 中同步更新
- 相关改动完成后，`uv run python scripts/run-host-tests.py` 与日常 pytest 入口均保持全绿

## 统一完成定义

每个里程碑都必须满足以下完成定义：

- 对应 E2E 测试与宿主集成测试均补齐
- `uv run python scripts/run-host-tests.py` 全绿
- 关键冲突与恢复路径至少有一条回归测试
- 文档与命令合同同步更新
