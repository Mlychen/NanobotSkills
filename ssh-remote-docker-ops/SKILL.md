---
name: ssh-remote-docker-ops
description: 通过 SSH 管理远程 Docker 与 Docker Compose 主机的通用运维技能。用于远程查看容器、镜像、Compose 项目、日志与 inspect 信息，或对单个服务执行 stop、rm、up、restart 等操作。用户提出“通过 SSH 管理远程 Docker”“盘点远端容器”“查看远端容器日志”“判断容器是否属于 compose”“只操作某个 compose 服务”等需求时使用。
---

# SSH 远程 Docker 运维

通过 `ssh` 在远程主机上执行 `docker` 与 `docker compose` 命令，不依赖本机安装 Docker。

使用前提：

- 本机可以直接调用 `ssh`
- 远程主机允许 SSH 登录
- 远程登录用户可以执行 `docker`
- 如果要管理 compose 项目，远程主机还应可执行 `docker compose`

## 工作原则

1. 先只读，后变更
- 默认先做连接验证、容器盘点、日志和 `inspect` 查看；只有在需要判断或管理 compose 项目时，再补做 compose 项目盘点。
- 在没有明确变更指令前，不执行 `stop`、`rm`、`restart`、`up`、`down`、`prune`。

2. 优先操作单个服务
- 对 compose 项目中的单个应用，优先按 `docker compose stop <service>` -> `docker compose rm -f <service>` -> `docker compose up -d <service>` 的顺序重建。
- 如果希望把停止与删除合并成一步，可选用 `docker compose rm -sf <service>`；默认模板中的 `stop -> rm -f -> up -d` 顺序同样成立。
- 只有在用户明确要求整项目下线或重建时，才使用 `docker compose down`。

3. 先确认容器归属
- 在变更前先判断目标容器是独立容器，还是由 `docker compose` 创建。
- 如果属于 compose 项目，应在对应的 compose 项目目录中操作，而不是直接对容器做破坏性删除。

4. 退出容器先查因
- 对 `Exited`、重启循环、异常健康检查的容器，先看 `docker logs` 和 `docker inspect`。
- 没有确认原因前，不直接重建。

## 标准流程

1. 验证 SSH 与 Docker 可用性，按需验证 Compose

```bash
ssh <host> "whoami"
ssh <host> "docker version"
# 只有在需要判断或管理 compose 项目时再执行
ssh <host> "docker compose version"
```

2. 做只读盘点，按需补做 compose 项目盘点

```bash
ssh <host> "docker ps -a"
ssh <host> "docker image ls"
# 只有在需要判断或管理 compose 项目时再执行
ssh <host> "docker compose ls -a"
```

3. 查看目标对象细节

```bash
ssh <host> "docker logs --tail 200 <container>"
ssh <host> "docker inspect <container>"
```

提取 Compose 标签：参见「如何判断是否属于 Compose」一节中的标签提取命令。

4. 判断容器归属后再做变更
- 如果容器带有 `com.docker.compose.*` 标签，按 compose 服务管理。参见「如何判断是否属于 Compose」一节提取 project/service/config_files/working_dir 并恢复完整 Compose 上下文。
- 如果没有 compose 标签，再按独立容器处理。

## 常用命令模板

### 只读查询

```bash
ssh <host> "docker ps -a"
ssh <host> "docker image ls"
ssh <host> "docker logs --tail 200 <container>"
ssh <host> "docker inspect <container>"
# 提取 Compose 标签：参见「如何判断是否属于 Compose」一节
# 只有在需要判断或管理 compose 项目时再执行
ssh <host> "docker compose version"
ssh <host> "docker compose ls -a"
```

### 独立容器操作

```bash
ssh <host> "docker stop <container>"
ssh <host> "docker rm -f <container>"
ssh <host> "docker restart <container>"
```

### Compose 服务操作

先通过容器标签确认 `project`、`service`、`config_files`、`working_dir`。`<compose_args>` 的拼装规则参见「如何判断是否属于 Compose」一节。

Linux / macOS 远端：

```bash
ssh <host> "docker compose <compose_args> ps"
ssh <host> "docker compose <compose_args> stop <service>"
ssh <host> "docker compose <compose_args> rm -f <service>"
ssh <host> "docker compose <compose_args> up -d <service>"
ssh <host> "docker compose <compose_args> logs --tail 200 <service>"
# 如果希望删除步骤自动停止仍在运行的服务，可改用：
ssh <host> "docker compose <compose_args> rm -sf <service>"
```

本机 PowerShell -> Windows 远端：

```bash
ssh <host> 'powershell -NoProfile -Command "docker compose <compose_args> ps"'
ssh <host> 'powershell -NoProfile -Command "docker compose <compose_args> stop <service>"'
ssh <host> 'powershell -NoProfile -Command "docker compose <compose_args> rm -f <service>"'
ssh <host> 'powershell -NoProfile -Command "docker compose <compose_args> up -d <service>"'
ssh <host> 'powershell -NoProfile -Command "docker compose <compose_args> logs --tail 200 <service>"'
# 如果希望删除步骤自动停止仍在运行的服务，可改用：
ssh <host> 'powershell -NoProfile -Command "docker compose <compose_args> rm -sf <service>"'
```

### 整项目操作

仅在用户明确要求整项目变更时使用，并且同样优先恢复完整 Compose 上下文：

Linux / macOS 远端：

```bash
ssh <host> "docker compose <compose_args> up -d"
ssh <host> "docker compose <compose_args> down"
```

本机 PowerShell -> Windows 远端：

```bash
ssh <host> 'powershell -NoProfile -Command "docker compose <compose_args> up -d"'
ssh <host> 'powershell -NoProfile -Command "docker compose <compose_args> down"'
```

## 如何判断是否属于 Compose

优先同时查看全量 `inspect` 与标签提取结果：

```bash
ssh <host> "docker inspect <container>"
ssh <host> "docker inspect --format '{{- with .Config.Labels -}}{{- with index . \"com.docker.compose.project\" }}{{ . }}{{ end -}}|{{- with index . \"com.docker.compose.service\" }}{{ . }}{{ end -}}|{{- with index . \"com.docker.compose.project.working_dir\" }}{{ . }}{{ end -}}|{{- with index . \"com.docker.compose.project.config_files\" }}{{ . }}{{ end -}}{{- else -}}|||{{- end -}}' <container>"
```

重点查看 `Config.Labels` 中是否存在：

- `com.docker.compose.project`
- `com.docker.compose.service`
- `com.docker.compose.project.config_files`
- `com.docker.compose.project.working_dir`

判断规则：

- 标签提取命令返回任意非空字段，或在 `inspect` 的 `Config.Labels` 中看到上述标签：说明容器由 `docker compose` 创建
- 标签提取命令稳定返回空字段（例如 `|||`），且 `inspect` 中也没有上述标签：按独立容器处理

从这些标签可反推出：

- compose 项目名
- 服务名
- compose 配置文件路径集合
- 可用于恢复 `--project-directory` 或作为兜底执行目录的 `working_dir`

推荐处理顺序：

1. 先读取 `com.docker.compose.project` 与 `com.docker.compose.service`
2. 再读取 `com.docker.compose.project.config_files`，优先恢复原始 `-f ...` 集合
3. 如果存在 `com.docker.compose.project.working_dir`，按需补上 `--project-directory "<working_dir>"`
4. 只有在 `config_files` 无法恢复时，才退回根据 `working_dir` 选择执行目录
5. 确认上下文后，再执行 `docker compose -p ... -f ... [--project-directory ...] ...`

## Windows 远端注意事项

- 远端如果是 Windows 主机，优先继续通过 `ssh` 直接执行远端 `docker` 命令，不依赖本机 Docker context
- 如果本机也是 PowerShell，优先使用外层单引号包裹整个 SSH 远端命令，再在远端 `-Command` 中使用双引号
- 优先显式调用 `powershell -NoProfile -Command`
- `compose_dir` 可能包含空格；如果必须切换目录，优先使用 `Set-Location -LiteralPath '<compose_dir>'`
- 如果远端默认 shell 不是 PowerShell，先显式进入 PowerShell，再执行目录切换和 `docker compose`

## 风险与边界

- 不默认执行 `docker system prune`、`docker image prune`、`docker volume prune`
- 不默认删除卷、网络、镜像
- 不把裸 `docker compose ls` 当成全量盘点；需要排查已停止项目时使用 `docker compose ls -a`
- 不在未确认 compose 归属时直接 `docker rm -f` 一个疑似 compose 容器
- 不因容器退出就直接重建，优先看 `logs` 和 `inspect`
- 不在未确认 `docker compose` 可用、`compose_dir` 可达之前执行 compose 变更命令

## 交付与汇报

执行远程 Docker 运维任务时，优先用下面的顺序汇报：

1. 连接是否成功
2. Docker 是否可用
3. 当前容器 / compose 项目盘点结果
4. 目标容器是否属于 compose
5. 已执行或计划执行的具体命令
6. 风险提示与下一步建议
