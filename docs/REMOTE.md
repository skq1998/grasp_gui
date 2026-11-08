# SSH remote access / SSH 远程连接

## 使用

桌面版选“文件 → 连接服务器…”。服务器只运行 `grasp_gui remote`，不需要显示设备、Qt 导入或另装 Python
(使用独立可执行文件时)。结果文件留在服务器，分析结果和重新取样后的曲线通过同一次 SSH 登录的标准输入输出传回。
不使用 X11、TCP/agent 转发、反向隧道、监听端口或服务器向外连接；不自动检查更新或上传程序。

### 安装服务器端

在桌面电脑下载支持 remote 协议的旧系统版 (glibc ≥ 2.17)，手动上传：

```sh
scp grasp_gui-legacy-linux-x86_64 cluster:grasp_gui-legacy
ssh cluster 'mkdir -p ~/.local/bin; mv ~/grasp_gui-legacy ~/.local/bin/grasp_gui; chmod +x ~/.local/bin/grasp_gui'
ssh cluster '~/.local/bin/grasp_gui --version'
```

默认依次尝试 `~/.local/bin/grasp_gui`、PATH。非交互 SSH 的 PATH/module 环境可能与终端不同，可在连接对话框
指定完整路径；成功选择目录时按主机保存实际路径和目录 (`remote_hosts` 用户偏好)。找不到、不能启动或协议不兼容时，
对话框显示原始错误输出；按上述方法手动更新，再设置完整路径重试。服务器上的 GRASP bin、MPI 等运行设置仍由服务器端
原有配置决定；第一版不能在桌面修改。

### 登录

沿用系统 OpenSSH 的密钥、ssh-agent、已知主机和 ProxyJump 配置，例如：

```sshconfig
Host gateway
    HostName gateway.example.org
    User researcher
    IdentityFile ~/.ssh/id_ed25519

Host cluster
    HostName compute.internal
    User researcher
    ProxyJump gateway
    IdentityFile ~/.ssh/id_ed25519
```

列表读取 `~/.ssh/config` 中的具体 Host 别名和 Include，也能直接输入 `user@host`。OpenSSH ≥ 8.4 使用
`SSH_ASKPASS_REQUIRE=force`，密码、密钥口令、验证码由隐藏内容的独立输入框交给 ssh，不保存、不记录。
首次连接确认主机密钥时，普通输入框完整显示 OpenSSH 提示及指纹，默认不填回答。先通过管理员或其他可信渠道
核对指纹，一致后再输入 `yes` 或粘贴指纹；不确定时取消，不要仅因连接需要就接受。
OpenSSH 的 `confirm` 提示仍用默认“否”的确认框；`none` (如 FIDO 硬件密钥按键) 仅显示通知，不提供输入框，
不向 ssh 回答，完成后由 ssh 结束通知进程。

判断依据为 [OpenSSH 10.0p2 sshconnect.c 的主机确认及重试提示](https://github.com/openssh/openssh-portable/blob/V_10_0_P2/sshconnect.c#L542-L566)
和[首次连接提示](https://github.com/openssh/openssh-portable/blob/V_10_0_P2/sshconnect.c#L1130-L1148)：
主机确认走 `RP_ECHO`，并非 `SSH_ASKPASS_PROMPT=confirm`。
`none` 通知及其退出由 [readpass.c 的 notify_start / notify_complete](https://github.com/openssh/openssh-portable/blob/V_10_0_P2/readpass.c#L264-L315)
定义。
更旧的 OpenSSH 使用 BatchMode，仅允许密钥/agent，并在界面说明；需要密码时先升级本机 OpenSSH。

每个远程窗口使用一个常驻 SSH stdio 会话；额外的查看器窗口共享它。ControlMaster/ControlPersist=60 仅用于复用认证，
控制套接字在当前用户的 0700 缓存目录；路径过长时改用 0700 临时目录。复用被拒绝时重连为普通 SSH。
`ServerAliveInterval=15`、`ServerAliveCountMax=3` 检测中断。自动重连最多连续失败三次 (等待 2、4 秒)；
耗时的读取请求至少等 180 秒，其他请求至少 30 秒，均再加 20 倍测得的请求往返时间。
停止自动重连后，可用远程窗口“窗口 → 重新连接”。

### 查看与计算

远程窗口顶部和独立查看器标题显示“服务器 host:/path”。计算页流程、参数、输入预览只读；可以运行全部、从选中步骤运行、
停止和查看日志。含自定义命令时必须在桌面确认；服务器在启动时再次核对目录记录的修订号。
能级图先显示能量，再补 jj/LSJ 主要组成和已算跃迁；需要更多组成时点“读取更多组成” (最多 500 项)。
拖动能级只选择已算跃迁，不在这里另建跃迁计算。图上标记仅保留在当前窗口。
可导出分析数据 CSV 和能级图；原始 GRASP 跃迁文件不传输，远程页会提示改用这两种导出。
能级图的“结果摘要…”在服务器调用 `grasp_summary.collect/render`，沿用同一个预览/勾选/复制/保存对话框。
每块能级数和包含部分改变后重新请求；摘要最多 200 KiB，按现有协议压缩传回，不下载原始结果文件。
运行证据只来自服务器保存的流程及状态，不能使用桌面本次会话的参数指纹。没有流程时禁用“附带完整配置”。
对话框标题标明服务器来源，保存文件在本机选择 (初始为本机家目录)，不把服务器目录当成本机路径。
关闭/替换请求后丢弃旧回答，不改剪贴板。

波函数先显示粗线，再补细线 (默认每轨道最多 1000 点，服务器按原网格索引作线性插值)；
表中的积分、节点数和原始点数仍由服务器在原始网格上计算。勾选缺少曲线的轨道时才请求曲线。

选中能级、显示已缓存组成、鼠标悬停、缩放和平移都不联网。每个视图为一个批量请求；较大目录有行数/曲线数限制，
结果截断时应少选文件。桌面缓存按主机、程序、请求参数和服务器文件的路径/大小/纳秒修改时间校验，
每个连接身份最多 128 MiB，优先清理最久未用的条目。再次打开先显示缓存，再用一次请求验证；文件变动后重新分析。
状态栏显示测得的请求往返时间 (包含服务器处理时间)。本地和远程目录互不写入；数据文件没有下载到桌面。

### 断线与后台计算

启动使用现有 `grasp_bg.launch`，新会话、无终端、输出写服务器日志，不与 SSH 持有管道。`remote` 在启动后的十秒内
接上后台状态；若连接在此之前退出，后台等待十秒后也会执行全部流程。重连重新读取状态和日志末尾，之后推送新增日志。
只读请求可以重发；启动/停止若失去回答，界面提示结果未确认，重连先查询状态，**不自动再启动或停止**。

服务器启动前调用 `logout_kills()` 检查 systemd-logind 的 KillUserProcesses。如果会随退出登录清理，默认取消；可选：

- 已开启 lingering 时，使用 `systemd-run --user --scope --quiet -- <后台程序> <目录>`，把计算放入用户管理器的 scope。
- 导出 Slurm 脚本到剪贴板，核对服务器运行设置后自行提交。
- 明确选择“照常启动”，接受断线会结束计算。

lingering 由 `loginctl show-user <用户名> -p Linger --value` 检测 (三秒超时)。scope 失败保留 systemd 原始诊断，
先核对状态，再由用户选 Slurm 或照常启动；不会静默退回普通进程。无 systemd 的服务器不显示 scope 选项。
管理员设置和检测限制见 [后台运行](INTERFACE.md#background-runs-and-logging-out--后台运行与退出登录-killuserprocesses)。

## Protocol (version 1)

The client starts `<executable> --version`, then `<executable> remote` on the same SSH command. It ignores stdout
before the fixed greeting `GRASP_GUI_REMOTE 1`, then sends `hello` with `protocol: 1` and its application version.
Compatibility is determined by the protocol number; update the server manually if incompatible.
Requests and replies are newline-delimited UTF-8 JSON; request IDs match replies and replies may arrive out of order.
Two server workers permit pipelined reads; run/stop are serialized. Server code uses only the Python standard library.

```json
{"id":1,"method":"hello","args":{"protocol":1,"version":"client version"}}
{"id":1,"result":{"protocol":1,"version":"server version","executable":"/path/to/grasp_gui","home":"/home/user"}}
{"id":2,"method":"view","args":{"directory":"/work/calc","kind":"levels"}}
```

| Method | Arguments / result |
|---|---|
| `hello` | Protocol/version; returns server protocol/version, executable and home |
| `list_dir` | Directory; names, sizes, mtimes, directories and content-detected types; up to 5000 entries |
| `levels` | Directory, optional files/limit/components; jj/LSJ level metadata and components |
| `transitions` | Directory, optional files/limit; existing λ, A, gf, S, computed-only branching ratios |
| `orbitals` | Directory, optional files/limit; properties computed on the original grid |
| `wavefunctions` | Directory, optional files/names; points 2–10000, at most 100 sampled curves |
| `summary` | Directory, optional per_block (1–200, default 5), selected sections; server-rendered text and configuration availability |
| `composition` | Directory, mixing file, block, index; up to 500 leading components |
| `view` | `kind: levels` or `wfn`; a whole view in one request, optional file/name selection and known signature |
| `pipeline` | Read-only saved jobs and revision |
| `run_plan` | Revision, custom commands, logout-kill policy and lingering availability |
| `run` | Revision, one-based start, explicit custom/logout/scope confirmations |
| `stop` | Stop through the existing background socket |
| `run_status` / `tail_log` | Existing state and bounded log tail |
| `watch` | Initial state/log tail; subscribes this session to local server state/log changes |
| `export_script` | Pipeline shell/Slurm script; does not submit it |

No arbitrary command RPC or file-download RPC is available. SSH already authenticates the account; the local MCP
enable switch is neither required nor changed. Results use the same `grasp_service` operations as MCP.
`run` uses the on-disk pipeline rather than accepting jobs supplied by the client.

Replies use `{"id":n,"result":...}` or `{"id":n,"error":"message"}`. Unsolicited messages use
`event: progress` (request ID and partial data), `event: log` (text/reset), or `event: state` (state).
The server checks its local log every 0.2 seconds; no desktop polling is used. A new run or replaced/truncated log
resets the displayed tail. Frames ≥16 KiB before compression use `{"zlib":"base64","size":uncompressed_bytes}`;
this envelope contains a zlib-compressed complete JSON message. Uncompressed messages are limited to 32 MiB,
including a bounded decompressor. File signatures exclude GUI run logs and analysis indexes, and include cached
transition results. A changed signature invalidates the cached view.

## English usage

Choose **File → Connect to server…**, select an SSH alias or enter `user@host`, optionally set the server executable's
full path, browse the server directories and choose **Use this directory**. Install/update the standalone legacy
executable manually with the scp commands above; the desktop never uploads it. Defaults try `~/.local/bin/grasp_gui`
then PATH. A successful connection remembers the executable's full path and working directory per host.
OpenSSH retains key/agent/ProxyJump behavior; ≥8.4 additionally supports password and verification-code dialogs.
On first contact, a normal input field shows the full host-key prompt and fingerprint. Verify that fingerprint
with your administrator or another trusted channel before typing `yes` or pasting it; cancel if unsure. Empty
input does not accept the host key. Passwords, key passphrases and verification codes remain hidden. OpenSSH
`confirm` requests use a default-No confirmation; `none` requests display only a notification, without input or
stdout, until ssh terminates the helper after the hardware-key operation. Source references are in the login
section above.
Older clients use keys only. Passwords are never saved or logged. No forwarding, server listening socket or outbound
server connection is required; all traffic uses the desktop-initiated stdio session.

The source is shown as **Server host:/path**. The pipeline is read-only, with parameter/input previews, run/stop and
pushed logs. Existing level and radial viewers render structured remote results; select **Load more components**
for additional data. CSV/figure export uses analysed data; original GRASP transition files stay on the server.
Curves are resampled on the server; numerical properties use the original grid.
**Results summary…** runs collect/render on the server using its saved pipeline and state. The same dialog supports
preview, section choices, levels per block, copy and local-file save; changing choices requests a new summary.
Text is bounded to 200 KiB and uses protocol compression. The dialog identifies the server, never reads remote
paths on the desktop, and starts saving in the desktop home directory. Desktop session history is not used;
configuration is disabled when no saved pipeline exists. Closed/replaced requests cannot update the preview.
Selection, hover, zoom and pan use local cached data. Reopening a view validates the bounded cache in one request.
The status bar reports request latency, including processing time. **Window → Reconnect** retries after automatic
reconnects have stopped. Running calculations normally continue through disconnection, including during startup;
read requests are retried, start/stop requests are never replayed automatically.

Before starting, servers that kill the login's processes at logout require an explicit choice: a user scope when
lingering is enabled, export a Slurm script, or start normally and accept the risk. Scope failures show systemd's
original diagnostic and require another user choice; no silent fallback occurs. See the Chinese section above for
the exact commands and [the interface reference](INTERFACE.md) for administrator remedies.

## Optional SSH test fixture

`tests/test_remote_ssh.py` skips unless `GRASP_SSH_TEST_HOST` is set. Use a dedicated disposable account: the tests
start/stop jobs in `GRASP_SSH_TEST_DIRECTORY`. Provide a computed `example1/` and three saved custom pipelines
`ssh-stop/`, `ssh-startup/`, `ssh-running/`, each containing three steps that print `step-0`, `step-1`, `step-2` and
sleep five seconds. Optional `GRASP_SSH_TEST_CONFIG` selects an isolated ssh_config; `GRASP_SSH_TEST_PROGRAM`
selects the installed server executable. Run `python3 -m pytest tests/test_remote_ssh.py`.
Protocol, cache/retry, bounded-memory radial reading, logout-choice and offscreen viewer tests need no SSH fixture
and run as part of the normal test suite. No real systemd manager is required for the simulated policy tests.

References: [OpenSSH ssh](https://man.openbsd.org/ssh.1),
[ssh_config](https://man.openbsd.org/ssh_config),
[systemd-run](https://www.freedesktop.org/software/systemd/man/latest/systemd-run.html),
[logind.conf](https://www.freedesktop.org/software/systemd/man/latest/logind.conf.html).
