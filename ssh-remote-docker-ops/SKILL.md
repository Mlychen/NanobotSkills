---
name: ssh-remote-docker-ops
description: 通过 SSH 管理远程 Docker 与 Docker Compose 主机的通用运维技能，覆盖容器、镜像、日志、inspect、Compose 服务与项目操作。用户提出“通过 SSH 管理远程 Docker”“盘点远端容器”“查看远端容器日志”“判断容器是否属于 compose”“只操作某个 compose 服务”等需求时使用。目标如果是 Windows + Docker Desktop 主机，还应使用本技能提供的远端 wrapper 绕过 `docker-credential-desktop.exe` / `wincred` 在 SSH 会话中的凭据错误。
---

# SSH 远程 Docker 运维

通过 `ssh` 在远程主机上执行 Docker / Compose 命令。优先保留统一心智模型，避免在技能里展开大量重复示例。

## 前提

- 本机可直接调用 `ssh`
- 远程主机允许 SSH 登录
- 远程登录用户可执行 Docker
- 如需管理 Compose，远端还需支持 `docker compose`

## 先决定执行入口

先定义 `<docker_cmd>`，后续所有命令模板都复用它。

- Linux / macOS 远端：`<docker_cmd> = docker`
- Windows + Docker Desktop 远端：`<docker_cmd> = cmd /c C:\Users\<User>\docker-ssh.cmd`
- Windows + Docker Desktop 远端如需传入带空格或带引号的参数（例如 `--format "..."`、`-f "C:\path with spaces\compose.yml"`），改用：`<docker_cmd_quoted> = cmd /s /c ""C:\Users\<User>\docker-ssh.cmd"`

Windows + Docker Desktop 远端默认统一走 wrapper，包括只读查询和变更命令。不要在 SSH 会话里混用原生 `docker`，否则 `pull` / `login` / `compose pull` 等命令可能触发 `docker-credential-desktop.exe` / `wincred`，报 `A specified logon session does not exist`。

首次接管 Windows 远端时，先安装并做默认的 wrapper / CLI 验证：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-remote-wrapper.ps1 -Host winnas -RemoteUser WinNas -RemoteHome C:\Users\WinNas
powershell -ExecutionPolicy Bypass -File .\scripts\verify-remote-wrapper.ps1 -Host winnas -RemoteHome C:\Users\WinNas
```

如需额外验证 registry 连通性或镜像拉取，再显式启用可选检查：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify-remote-wrapper.ps1 -Host winnas -RemoteHome C:\Users\WinNas -IncludePullChecks -Image mymc/nanobot:edge
```

相关资源：

- 远端 wrapper 模板：`assets/docker-ssh.cmd`
- 安装脚本：`scripts/install-remote-wrapper.ps1`
- 验证脚本：`scripts/verify-remote-wrapper.ps1`

## 标准流程

1. 验证连接与 Docker CLI 可用；如需管理 Compose，再验证 `docker compose`
2. 做只读盘点
3. 看 `logs` / `inspect`
4. 先判断容器归属，再决定按独立容器还是 Compose 服务操作
5. 变更时优先改单个服务，不默认操作整个项目

默认不要跳步，不要因为容器退出就直接重建。

## 统一命令模板

将下面模板中的 `<docker_cmd>` 替换成前面选好的执行入口。

如果是 Windows 远端，且命令本身要保留引号参数，用 `<docker_cmd_quoted>`，并在远端参数里继续使用 `cmd.exe` 需要的双引号形式。外层可以用本地 shell 的单引号包住整条 `ssh` 命令，但真正传给远端 `cmd.exe` 的 `--format` 模板、`-f` 路径、`--project-directory` 路径仍然必须是双引号。

基础验证：

```bash
ssh <host> "<docker_cmd> version"
# 仅在需要盘点或操作 Compose 时执行
ssh <host> "<docker_cmd> compose version"
```

只读盘点：

```bash
ssh <host> "<docker_cmd> ps -a"
ssh <host> "<docker_cmd> image ls"
# 仅在需要盘点或操作 Compose 时执行
ssh <host> "<docker_cmd> compose ls -a"
ssh <host> "<docker_cmd> logs --tail 200 <container>"
ssh <host> "<docker_cmd> inspect <container>"
```

独立容器操作：

```bash
ssh <host> "<docker_cmd> stop <container>"
ssh <host> "<docker_cmd> rm -f <container>"
ssh <host> "<docker_cmd> restart <container>"
```

Compose 服务操作：

```bash
ssh <host> "<docker_cmd> compose <compose_args> ps"
ssh <host> "<docker_cmd> compose <compose_args> stop <service>"
ssh <host> "<docker_cmd> compose <compose_args> rm -f <service>"
ssh <host> "<docker_cmd> compose <compose_args> up -d <service>"
ssh <host> "<docker_cmd> compose <compose_args> logs --tail 200 <service>"
```

如果 Windows 远端恢复出的 `<compose_args>` 含带空格路径，命令改写成：

```bash
ssh <host> '<docker_cmd_quoted> compose -f "C:\path with spaces\compose.yaml" --project-directory "C:\path with spaces" up -d <service>"'
```

整项目操作只在用户明确要求时使用：

```bash
ssh <host> "<docker_cmd> compose <compose_args> up -d"
ssh <host> "<docker_cmd> compose <compose_args> down"
```

## 判断是否属于 Compose

先同时看全量 `inspect` 和标签提取结果：

```bash
ssh <host> "<docker_cmd> inspect <container>"
ssh <host> '<docker_cmd_quoted> inspect --format "{{- with .Config.Labels -}}{{- with index . \"com.docker.compose.project\" }}{{ . }}{{ end -}}|{{- with index . \"com.docker.compose.service\" }}{{ . }}{{ end -}}|{{- with index . \"com.docker.compose.project.working_dir\" }}{{ . }}{{ end -}}|{{- with index . \"com.docker.compose.project.config_files\" }}{{ . }}{{ end -}}{{- else -}}|||{{- end -}}" <container>"'
```

重点字段：

- `com.docker.compose.project`
- `com.docker.compose.service`
- `com.docker.compose.project.config_files`
- `com.docker.compose.project.working_dir`

判断规则：

- 任意字段非空，或 `inspect` 中能看到这些标签：按 Compose 处理
- 提取结果稳定为空，且 `inspect` 中也没有这些标签：按独立容器处理

Compose 处理顺序：

1. 先确定 `project` 和 `service`
2. 优先用 `config_files` 恢复原始 `-f ...` 集合
3. 如有 `working_dir`，按需补 `--project-directory`
4. 确认上下文后，再执行 `compose` 变更命令

## Windows 远端固定提示

- Windows + Docker Desktop 远端默认统一走 wrapper
- 默认 wrapper 路径：`C:\Users\<User>\docker-ssh.cmd`
- 默认隔离目录：`%USERPROFILE%\docker-ssh`
- 默认 context：`desktop-linux`
- 不修改默认 `%USERPROFILE%\.docker\config.json`
- 不尝试修复 Docker Desktop helper；wrapper 只是 SSH 运维绕过方案

## 认证策略

- 公开镜像默认匿名拉取
- 私有仓库或限流场景，使用 wrapper 的隔离配置登录
- 命令模板：

```bash
Get-Content $env:USERPROFILE\.secrets\registry-pat.txt | ssh <host> 'cmd /s /c ""C:\Users\<User>\docker-ssh.cmd" login -u <user> --password-stdin"'
```

- 用本机 secret source 提供 PAT，例如 `Get-Content ...`、`Get-Secret ...`，不要把令牌直接写进 SSH 命令文本
- PAT 只写入 `%USERPROFILE%\docker-ssh\config.json`
- 不依赖 Credential Manager
- 不写回默认 Docker Desktop 配置

## 风险边界

- 不默认执行 `prune`
- 不默认删除卷、网络、镜像
- 不在未确认 Compose 归属时直接 `docker rm -f`
- 不在未确认 `compose` 可用、上下文可恢复之前执行 Compose 变更
- 不把 `docker compose ls` 当成唯一事实来源，排查已停止项目时使用 `ls -a`

## 汇报顺序

1. SSH 是否连通
2. `<docker_cmd>` 是否可用
3. 容器 / Compose 项目盘点结果
4. 目标是否属于 Compose
5. 已执行或拟执行的命令
6. 风险与下一步
