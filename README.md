<div align="center">

# 🔐 Server Vault

**本地优先的服务器密码管理 & 运维中心 —— 人可以用,AI Agent 也可以用**

加密保管服务器凭据 · 定期自动轮换密码 · 批量检测状态 · 硬件配置与机房位置 · 网站/端口/容器发现 · Web 终端 · **MCP / REST 服务给 Agent 调用**

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![MCP](https://img.shields.io/badge/MCP-Streamable%20HTTP%20%2B%20stdio-6E56CF)
![License](https://img.shields.io/badge/license-MIT-green)

如果这个项目对你有帮助,欢迎点个 ⭐ Star 支持一下!

</div>

---

## ✨ 功能

| 模块 | 能力 |
| --- | --- |
| **加密保险库** | 主密码经 scrypt 派生密钥,AES-256-GCM 加密全部密码/私钥;密钥只在内存,可一键锁定;支持修改主密码(全量重加密) |
| **一键导入本机 SSH** | 解析 `~/.ssh/config`(用 `ssh -G` 解析 Match / Include / ProxyCommand),私钥服务端直接加密入库,主机指纹取自 `known_hosts` |
| **代理 / 跳板** | 支持 OpenSSH `ProxyCommand`(如 VPN 的 SOCKS5 `nc -X 5`、`ssh -W` 跳板机),代理抖动自动重试 |
| **人工连接** | 浏览器内 Web SSH 终端(xterm.js,可多开)、一键复制密码 / SSH 命令 |
| **密码轮换** | 按服务器设置轮换周期,到期自动执行;生成强密码 → **预写入保险库** → `chpasswd`(root 或 sudo)→ 新密码验证登录 → 旧密码归档。中断不丢密码,失败自动 1 小时后重试 |
| **自动化** | 定时状态巡检、定时采集配置、自定义巡检脚本(按全部/分组/指定服务器,手动或周期执行),内置磁盘、证书、失败服务等模板 |
| **配置信息** | CPU 型号/核数/路数、内存、Swap、磁盘、**服务器厂商与型号**、序列号、主板、虚拟化类型、系统/内核、内网 IP |
| **位置信息** | 手填机房/服务商 + 自动识别公网 IP 归属地(国家/省/市/运营商/AS) |
| **批量状态检测** | 并发检测端口连通、SSH 登录、延迟、负载、内存与根分区使用率 |
| **网站发现** | 解析 `nginx -T`(域名、监听、根目录、反向代理、upstream、跳转、**SSL 证书到期**)、Apache vhost、监听端口与进程、Docker 容器 |
| **Agent 服务** | MCP(Streamable HTTP + stdio 桥接)与 REST API;Token 按权限(read / exec / secret / rotate)和分组隔离;危险命令正则拦截;全量审计 |
| **界面** | 卡片 / 列表双视图、按分组筛选、状态排序、浅色 / 深色 / 跟随系统主题 |
| **安全** | SSH 主机指纹 TOFU 校验(**认证前**校验,防中间人窃取密码)、登录失败限流、默认仅监听 127.0.0.1、审计日志 |

## 🚀 快速开始

```bash
git clone https://github.com/SikeFix/server-vault.git
cd server-vault
./run.sh
```

打开 <http://127.0.0.1:8765>,首次访问设置主密码,然后点击「从本机 SSH 导入」即可把 `~/.ssh/config` 里的服务器一键导入,并自动检测状态、采集配置。

> 需要 Python 3.10+。`run.sh` 会自动创建虚拟环境并安装依赖(有 [uv](https://github.com/astral-sh/uv) 时优先使用)。

**没有服务器也想体验?** 启动内置的模拟 SSH 服务器,然后在界面里添加 `127.0.0.1:2222`,用户 `root`,密码 `InitPass#123`:

```bash
.venv/bin/python tests/mock_sshd.py
```

### 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SV_HOST` | `127.0.0.1` | 监听地址。对外暴露请务必放在 HTTPS 反向代理之后 |
| `SV_PORT` | `8765` | 监听端口 |
| `SV_DATA_DIR` | `./data` | 数据目录(SQLite,文件权限 600) |
| `SV_MASTER_PASSWORD` | — | 启动时自动解锁,用于无人值守给 Agent 提供服务(会降低安全性,谨慎使用) |

## 🤖 给 AI Agent 使用

在 Web 界面 **Agent 接入** 页创建 Token(选择权限和可访问的分组),页面会生成可直接复制的配置。

**Claude Code**

```bash
claude mcp add --transport http server-vault http://127.0.0.1:8765/mcp \
  --header "Authorization: Bearer sv_xxx"
```

**Cursor / 支持 HTTP 的 MCP 客户端**

```json
{
  "mcpServers": {
    "server-vault": {
      "url": "http://127.0.0.1:8765/mcp",
      "headers": { "Authorization": "Bearer sv_xxx" }
    }
  }
}
```

**Claude Desktop / 仅支持 stdio 的客户端**(桥接脚本只依赖 Python 标准库)

```json
{
  "mcpServers": {
    "server-vault": {
      "command": "python3",
      "args": ["/path/to/server-vault/mcp_stdio.py"],
      "env": { "SV_URL": "http://127.0.0.1:8765", "SV_TOKEN": "sv_xxx" }
    }
  }
}
```

### MCP 工具

| 工具 | 权限 | 说明 |
| --- | --- | --- |
| `whoami` | — | 当前 Token 的权限与可见服务器数 |
| `list_servers` | read | 列出服务器(可按分组 / 状态 / 关键字过滤) |
| `get_server` | read | 单台服务器详情(不含密码) |
| `check_status` | read | 批量实时检测状态 |
| `get_server_facts` | read | 硬件与系统配置、IP 归属地 |
| `get_websites` | read | 站点、反代、证书到期、端口、容器 |
| `exec_command` | exec | 执行命令(支持 sudo、超时) |
| `batch_exec` | exec | 多台并发执行 |
| `get_credentials` | secret | 读取明文凭据 |
| `rotate_password` | rotate | 立即轮换密码 |

`server` 参数可以传 id、名称或主机地址。Token 没有的权限对应的工具不会出现在 `tools/list` 中。

### REST API

```bash
curl -s http://127.0.0.1:8765/api/agent/servers -H "Authorization: Bearer sv_xxx"

curl -s -X POST http://127.0.0.1:8765/api/agent/servers/web-01/exec \
  -H "Authorization: Bearer sv_xxx" -H "Content-Type: application/json" \
  -d '{"command": "df -h", "sudo": false}'
```

完整接口见 <http://127.0.0.1:8765/docs>。

## 🔁 密码轮换是怎么工作的

```
生成强密码(不含 : 和引号)
  → 新密码加密写入 pending 字段          ← 任何中断都不会丢失新密码
  → SSH 执行 chpasswd(非 root 用 sudo -S)
  → 成功:新密码生效,旧密码进入历史(保留 20 条),计算下次轮换时间
  → 用新密码重新登录验证(私钥账号用 sudo 验证)
  → 失败:记录原因,1 小时后自动重试
```

非 root 账号需要开启「使用 sudo」并保存当前密码。

## 🏗️ 项目结构

```
app/
  main.py         FastAPI 路由、Web 终端 WebSocket、会话
  vault.py        主密码保险库(scrypt + AES-256-GCM)
  ssh.py          SSH 连接 / 指纹校验 / 命令执行
  collectors.py   远程采集脚本与解析(配置、状态、nginx/apache/端口/docker)
  services.py     业务逻辑(Web、REST、MCP 共用)
  agent.py        Agent 权限层
  mcp.py          MCP 协议实现
  scheduler.py    后台调度(轮换、巡检、采集、定时任务)
static/           前端(原生 JS,无需构建)
mcp_stdio.py      MCP stdio 桥接
tests/            端到端测试 + 模拟 SSH 服务器
```

## 🧪 测试

端到端测试会启动模拟 SSH 服务器和后端,覆盖保险库、轮换、采集、网站解析、批量检测、Agent REST、MCP、stdio 桥接、Web 终端、指纹校验、调度器等 85 项断言:

```bash
.venv/bin/pip install httpx   # 或 uv pip install httpx
.venv/bin/python tests/e2e_test.py
```

## 🔒 安全建议

- 默认只监听本机;需要远程访问时用 Nginx/Caddy 加 HTTPS 和额外认证
- 给 Agent 的 Token 遵循最小权限:一般只给 `read` + `exec`,`secret` 谨慎授予,并按分组限制范围
- 定期查看审计日志;不用时可在左下角一键锁定保险库,Agent 访问会立即被拒绝
- 主密码无法找回,请妥善保管;`data/vault.db` 建议定期备份(内容已加密)

## 📄 License

[MIT](LICENSE)

---

<div align="center">觉得有用?给个 ⭐ Star 吧,这是对作者最大的鼓励!</div>
