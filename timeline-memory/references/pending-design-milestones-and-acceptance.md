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
- 第四段：进行中
  - 已覆盖 `prepared`、`raw_committed`、`snapshot_committed`、`history_committed` 四类事务中断恢复
  - 下一步继续把“重复恢复不重复 history”扩展成更系统的故障注入矩阵

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
- 第五步：补故障注入测试与回归测试（进行中）

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
  - 同一 `turn_id` 连续重放 2 次以上不产生重复 history（部分覆盖，需继续扩展）
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

## 里程碑 M4：结构化错误模型

范围：

- 定义错误码体系与错误类别
- CLI 输出稳定的结构化错误信息
- 保留可读错误文本用于人工排障

验收标准：

- 每类核心失败路径都有固定错误码
- 上层可仅依赖错误码完成重试与分支处理
- 历史关键错误文案兼容或提供清晰迁移说明

## 里程碑 M5：高级事件语义

范围：

- 在 `project-turn` 中定义 `correlation_id`、`causation_id`、`confidence` 的写入策略
- 明确 `event_refs` 角色在证据链中的约束
- 对多轮归因建立最小可用语义规则

验收标准：

- 相关字段在写入、重放、修复后语义一致
- `event_refs` 角色使用符合约束且可测试
- 回放不会破坏归因链和置信度字段

## 里程碑 M6：查询能力增强

范围：

- 为 `list-threads` 增加分页能力
- 增加时间窗口过滤能力
- 增加可选文本检索能力

验收标准：

- 新增参数全部有明确 contract 与边界行为
- 在数据量增长场景下查询结果稳定且顺序可预测
- 新查询能力不破坏既有调用方式

## 统一完成定义

每个里程碑都必须满足以下完成定义：

- 对应 E2E 测试与宿主集成测试均补齐
- `python scripts/run-host-tests.py` 全绿
- 关键冲突与恢复路径至少有一条回归测试
- 文档与命令合同同步更新
