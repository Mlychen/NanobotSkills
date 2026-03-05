---
name: todo-txt
description: 基于 todo.txt 的本地任务管理技能。用于记录、查询、整理和归档任务，支持优先级 (A-Z)、项目标签 (+project)、上下文标签 (@context) 与分钟级时间标记 (t:HH:MM)。当用户提出“记任务/查今天安排/按项目或上下文筛选/调整优先级/归档已完成/定时提醒”等需求时使用。
---

# Todo.txt 任务管理

维护纯文本任务清单，不依赖外部数据库。固定使用：
- `~/.nanobot/workspace/todo.txt`
- `~/.nanobot/workspace/done.txt`

## 数据格式

按单行格式写入任务：

```text
[x ] [(A)] [完成日期] [创建日期] 任务描述 +项目 @上下文 t:HH:MM
```

规则：
- 未完成任务不要写 `x`、完成日期。
- 创建日期使用 `YYYY-MM-DD`。
- 优先级使用 `(A)` 到 `(Z)`，越靠前越紧急。
- 时间使用扩展字段 `t:HH:MM`（24 小时制）。
- 项目与上下文可有多个：`+ProjA +ProjB @home @deepwork`。

示例：

```text
(A) 2026-03-05 和老王对齐代码 +NanoBot @office t:15:00
x 2026-03-05 2026-03-04 提交日报 +Ops @laptop
```

## 执行流程

1. 解析用户意图
- 提取动作：新增、查询、修改、完成、归档。
- 提取字段：优先级、日期、时间、项目、上下文、关键词。

2. 规范化任务字段
- 将自然语言时间转为 `t:HH:MM`。
- 若用户未指定创建日期，写入当前日期。
- 若用户未指定上下文，不强制补齐。

3. 操作文件
- 新增：追加到 `~/.nanobot/workspace/todo.txt`。
- 查询：按条件筛选并返回匹配项。
- 完成：将任务行改为 `x 完成日期 创建日期 ...`。
- 归档：把已完成任务移动到 `~/.nanobot/workspace/done.txt` 并从 `~/.nanobot/workspace/todo.txt` 删除。

4. 回报结果
- 明确说明执行了什么（新增/修改/归档/无结果）。
- 返回关键结果或列表预览。
- 无匹配时给出可执行下一步建议（例如“是否新建该任务”）。

## 常用筛选命令

优先使用 `rg`（不可用时再用 `grep`）。

```bash
# 建议先定义路径变量
TODO_FILE=~/.nanobot/workspace/todo.txt

# 查某个小时的任务（如 14 点）
rg "t:14:" "$TODO_FILE"

# 查项目 +NanoBot
rg "\+NanoBot" "$TODO_FILE"

# 查上下文 @home
rg "@home" "$TODO_FILE"

# 组合筛选：项目 + 上下文
rg "\+ProjectX" "$TODO_FILE" | rg "@home"

# 查高优先级 A
rg "^\(A\)" "$TODO_FILE"
```

## 心跳提醒集成

当系统具备定时任务能力时，按小时扫描当前时段任务：

```bash
# Bash
TODO_FILE=~/.nanobot/workspace/todo.txt
rg "t:$(date +%H):" "$TODO_FILE"

# PowerShell
$todoFile = "$HOME/.nanobot/workspace/todo.txt"
$h = Get-Date -Format HH
rg "t:${h}:" $todoFile
```

找到匹配项后发送提醒消息（例如 IM/飞书）。技能本身只负责任务文本管理与可检索格式，不强依赖外部推送平台。

## 交互示例

用户：`帮我记下今天下午3点和老王对齐代码，优先级A，项目+NanoBot`

回复：

```text
已记录 1 条任务：
(A) 2026-03-05 和老王对齐代码 +NanoBot t:15:00
```

## 约束与安全

- 仅读写固定路径 `~/.nanobot/workspace/todo.txt` 与 `~/.nanobot/workspace/done.txt`。
- 不将任务数据上传到外部云服务。
- 修改前优先保持原始行可追踪（必要时先备份）。

