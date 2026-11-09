# SSH remote access / SSH 远程连接

## 使用

桌面版选“文件 → 连接服务器…”。服务器只运行 `grasp_gui remote`，不需要显示设备、Qt 导入或另装 Python
(使用独立可执行文件时)。结果文件留在服务器，分析结果和重新取样后的曲线通过同一次 SSH 登录的标准输入输出传回。
不使用 X11、TCP/agent 转发、反向隧道或监听端口；不自动检查更新或上传程序。
Slurm 类型通过服务器已有的 Slurm 命令访问集群调度/记账服务，不访问互联网。

### Server type / 服务器类型

Choose **Workstation (工作站)** or **Slurm cluster (Slurm 集群)** in the connection dialog. The choice is saved
per SSH host, alongside the existing executable/directory preferences. New and previously unclassified profiles
ask for a type once before using the directory.
The choice is explicit: the presence of Slurm client binaries alone does not classify a computer.
The dialog explains why: cluster login nodes should not run calculations directly.

| Type / 类型 | Submit / 提交 | State / 状态 | Stop / 停止 |
|---|---|---|---|
| Workstation / 工作站 | Existing background runner / 原后台进程 | Server process and saved state / 服务器进程及记录 | Background stop / 后台停止 |
| Slurm cluster / Slurm 集群 | Confirmed `sbatch --parsable` | `squeue`, then `sacct` after leaving the queue | `scancel` for the recorded job |

选择 Slurm 后，“提交 Slurm 作业”打开资源设置：分区、节点、每节点进程数、时限、内存、GRASP 路径、模块命令等。
这些值按主机保存在本机；脚本使用服务器已有的流程和输入文件，最终确认默认否。不会在提交失败时退回登录节点直跑。
工作站沿用原来的后台运行和退出登录处理。旧客户端设置无需迁移；使用 Slurm 前需更新到支持此功能的服务器端。

For Slurm, review the resource dialog and confirm submission (default No). Parameters are stored per host on
the desktop; paths and module commands refer to the server/compute nodes. The server uses its saved pipeline,
checks the revision and required inputs, and saves the generated script and submission intent before calling
sbatch. `sbatch`, `squeue` and `scancel` must be available in the server session. Completed-job state also needs
`sacct` and working cluster accounting. There is no fallback to running on the login node.

The server directory stores `.grasp_gui_slurm.json` (job ID, returned cluster, submission time and identity),
plus `.grasp_gui_slurm_<token>.sh` and a separate `.log`. Use the same account, Slurm cluster and shared
directory from another computer or login node to recover the job state and log; no original desktop process
or login-node PID is needed. SSH profiles, classification and resource preferences are local to each desktop.
Only the recorded job is tracked; this is not a list of all jobs in the user's queue.

排队、运行、收尾和暂停期间不能重复提交同一目录，也不能更新或清理该目录。状态栏显示 Slurm 作业 ID 和原始状态，
包括 PENDING、RUNNING、COMPLETING、CANCELLED、FAILED、TIMEOUT、OUT_OF_MEMORY 等。
日志按独立文件恢复并持续显示。Slurm 状态在服务器端缓存：活动作业至少间隔 15 秒查询；错误和结束状态至少间隔 60 秒，
不随每 0.2 秒的本地日志检查反复调用调度器。取消会先核对队列中的作业标识，再发送 scancel，随后等待调度器确认结果。

Disappearance from squeue is **not** success. Accounting delay, missing sacct, unavailable accounting, query
failure or mismatched job identity shows Unknown and blocks a new submission. Explicit sbatch rejection can
be corrected and retried; a timeout or unreadable submission reply is never retried automatically. Instead,
the server searches the current user's queue for the exact `grasp-gui:<token>` comment and saves the recovered
job ID. sbatch has a 60-second timeout; queue/accounting commands have 30 seconds, and the desktop allows time
for these operations.

New submissions install an EXIT trap before any batch commands. It atomically writes
`.grasp_gui_slurm_<token>.done` containing the token, job ID and shell exit status. After the job leaves the
queue, validated accounting takes precedence; unavailable/delayed accounting can fall back to this marker.
The state then reports `BATCH_EXITED`, identifying script exit evidence rather than a scheduler completion
record. A killed node or SIGKILL may prevent the marker; absence is still Unknown. Existing version-1 records
without markers remain readable. Job arrays are not supported by this submission dialog.
After a successful scancel request, an optional `cancel_requested` flag is persisted in the version-1 record.
A later exit marker with status 130/143 then displays Cancelled even without accounting; the same exit codes
without this flag remain Failed. The request alone never establishes a terminal state, and accounting remains authoritative.

未知状态提供“我已核对，清除此作业记录…”：人工核对后默认否确认，服务器在目录锁内核对记录修订号，
重新查询当前用户队列，确认没有同 token 的作业后把原记录改名为 `.grasp_gui_slurm.json.cleared.<id>`。
原字节、脚本、日志和结束标记保留；这既不取消作业，也不把计算标为成功。损坏的记录会显示错误，并提供同一入口；
如果无法识别 token，必须确认该用户队列为空才能归档。查询失败时拒绝清除，需先恢复 Slurm 客户端/调度连接。

Slurm 的 COMPLETED/退出码 0 仅表示作业正常退出，不证明物理结果正确；逐步运行摘要不会把调度状态当作每步成功的证据。
本机日志和现有后台记录的格式不变；Slurm 使用独立记录。关闭远程窗口只断开查看，不会 scancel 作业。

Screenshots / 截图：[English](images/en_server_type.png) / [中文](images/zh_server_type.png)。
Resources / 资源设置：[English](images/en_slurm_submit.png) / [中文](images/zh_slurm_submit.png)。
Pending / 排队状态：[English](images/en_slurm_pending.png) / [中文](images/zh_slurm_pending.png)。
Unknown / 未知状态：[English](images/en_slurm_unknown.png) / [中文](images/zh_slurm_unknown.png)。
Clear confirmation / 归档确认：[English](images/en_slurm_clear.png) / [中文](images/zh_slurm_clear.png)。

复制或重命名目录不会迁移调度器中的作业。格式正确但路径不匹配的记录视为不属于当前目录，不查询、取消原作业，
也不阻止副本查看结果、更新或清理。原目录的状态不受影响。格式损坏仍显示错误，需要显式归档解除，不能静默忽略。

For Unknown, **I have checked; clear this job record…** asks for default-No confirmation. The server checks
the record revision under the directory lock and queries the queue again. It archives the original bytes only
when no matching token remains; a malformed record without a readable token requires an empty user queue.
Queue errors prevent clearing. Clearing is neither cancellation nor evidence of calculation success.
A valid record for another directory is ignored in a copied/moved directory, leaving result viewing and updates usable.

Command references: [sbatch](https://slurm.schedmd.com/sbatch.html),
[squeue](https://slurm.schedmd.com/squeue.html), [sacct](https://slurm.schedmd.com/sacct.html),
[scancel](https://slurm.schedmd.com/scancel.html).

### Optional real Slurm acceptance / 可选真实 Slurm 验收

`tools/slurm/` contains a single-node Docker fixture: slurmctld and slurmd in one container; the second mode
also starts MariaDB/slurmdbd. Runtime uses a Docker `--internal` network, publishes no ports and mounts
neither the Docker socket nor the host cgroup. Build installs test-only OS packages, without product/CI changes.
The fixture uses Debian trixie / Slurm 24.11, with the cgroup plugin disabled; an existing named network must
also be internal, or the runner stops.

With Docker available, provide a GRASP source checkout (standard `compas/grasp` commit `9006157` is the reference)
and a report directory outside this repository:

```sh
bash tools/slurm/run.sh /path/to/grasp-source /path/to/slurm-reports
```

GRASP source and this checkout are mounted read-only; compilation is inside the disposable container, including
MPI. The fixture does not modify Fortran source; source SHA and local changes are printed in the logs. Supply
any locally required allocation patch explicitly and retain it with the acceptance evidence. Both modes build
from the same supplied source; allow several minutes and sufficient compiler/GRASP memory.

The opt-in `tests/test_slurm_live.py` covers held/PENDING → RUNNING → example1 completion with reference energies,
running-job cancellation, no-accounting completion via EXIT marker, actual sacct evidence with accounting,
and `mpirun -np 1` producing `000/mcp000.30`. Accounting mode removes the completion marker before requiring
sacct evidence. Cancellation without a marker exercises explicit archival after the queue is empty.
Without `GRASP_SLURM_TEST=1` these tests skip. The maintainer ran the corrected fixture with Slurm 24.11.5:
**3 passed without accounting and 3 passed with slurmdbd**, including reference energies, cancellation and
the single-rank MPI file location. See the [acceptance review](https://github.com/skq1998/grasp_gui/pull/46#pullrequestreview-5820086033).
These fixture corrections are now included here. The development environment still lacks Docker; the added
no-accounting Cancelled display has unit coverage and should be rechecked with the updated fixture assertion.

负责人已在 Slurm 24.11.5 下完成真实验收：无记账和带 slurmdbd 各 3 passed，MPI 单进程文件位置已核对。
本次纳入其已验证的夹具修正，并补齐无记账时用户取消的状态显示；后者仍请用更新后的断言复验。

### Create directly on the server / 直接在服务器新建

After browsing a server directory, choose **Create calculation here…** in the connection dialog, or in a
remote Calculation tab with no pipeline. The configuration selector accepts built-in examples, a local JSON
file, or clipboard text (including fenced JSON). The default destination is a child of the browsed directory,
using a sanitized configuration title; edit it to choose another absent/empty server directory.
**Update current working directory** is hidden. No local calculation directory or local-copy association is created.

选好主机、服务器类型并浏览目录后，点“在此新建计算…”，从示例、配置文件或剪贴板选择配置。默认目标为当前服务器目录下
经清理的配置标题，可自行修改；已有非空目录会被拒绝。程序是否可用按服务器保存的 GRASP 路径及 PATH 检查，不受本机安装影响。

The existing upload dialog previews the pipeline, missing server programs/inputs, attached files and custom
commands. **Also upload local input files…** optionally selects an existing local input directory and uses the
ordinary input table/rsync flow: small files checked, large/generated files unchecked, server names retained.
Missing external inputs may be supplied later through the remote File menu. Custom commands need a separate
confirmation; the final host/path confirmation defaults to No. The record is created before file transfer.
A failed transfer retains the record and offers a checked retry. Successful creation opens the new remote directory;
from an empty remote window it replaces that view using the same SSH connection.

预览显示服务器缺少的程序/输入、流程、附带文件和自定义命令。“同时从本机上传输入文件…”可选现有输入目录，沿用文件表和 rsync；
也可先创建流程，稍后从远程窗口“文件 → 从本机上传…”补齐输入。自定义命令单独确认，最终确认默认否，先写记录再传文件。
勾选“新建并运行”后仍走普通工作站或 Slurm 的运行预检和确认，不会直接跳过调度器或运行确认。

**Create and run** opens the ordinary run-plan/confirmation flow for the chosen Workstation or Slurm type.
If a create reply is lost, read the server pipeline and compare its fingerprint; never resend automatically.
An absent/empty target must be proved before a new user-confirmed retry. Pending verification lives only in this
creation dialog; after closing it, browse the server directory to inspect the outcome. Servers lacking
`workdir_write_v1`, or the preview's additive `missing_programs` response field, show an update instruction before writes.
Existing local import/upload remains compatible with older preview responses; no RPC or configuration format changes.

新建回复丢失时先读取流程核对指纹，不自动重发；只有再次证明目标仍为空，才允许用户重新确认。待核对信息仅在当前新建窗口中，
关闭后应重新浏览服务器目录确认结果。旧服务器缺少能力或程序检查字段时提示更新，拒绝直接新建；原有本机导入和上传流程不变。

Screenshots / 截图：configuration / 配置 [English](images/en_remote_import_config.png) / [中文](images/zh_remote_import_config.png)；
preview / 预览 [English](images/en_remote_import_preview.png) / [中文](images/zh_remote_import_preview.png)。

Optional real SSH acceptance / 可选真实 SSH 验收：沿用下文的专用 SSH fixture，安装本分支服务器后运行
`GRASP_REMOTE_EDIT_TEST=1 python3 -m pytest tests/test_remote_import_ssh.py -v`。两项测试覆盖剪贴板 example1 直接新建运行，
以及选择已有本机 isodata 输入、rsync 上传后运行，均比较参考能量；只在显式指定的验收目录中新建。

The maintainer ran the two direct-import cases plus the three remote-edit cases against a restricted sshd
fixture using the branch merged with main, server source and the official 2.9.0 old-server package:
**5 passed**, GRASP `9006157` ([review evidence](https://github.com/skq1998/grasp_gui/pull/49#pullrequestreview-5829004138)).
This evidence precedes the confirmation/entry-visibility review fixes; repeat against the updated head before merging.
维护者在受限 sshd 中，以合入 main 的源码和官方 2.9.0 旧端验收：新建两项、编辑三项共 **5 passed**，GRASP `9006157`。
该记录早于本轮确认提示及入口显示修订，更新后的提交仍需维护者复测。

### Local editing and upload / 在本机编辑并上传

Remote pipelines remain read-only in the remote viewer. Use the existing desktop Calculation tab for editing;
no additional workspace tabs or remote editor are introduced. This requires a server advertising
`capabilities: ["workdir_write_v1"]`. Missing capability disables the edit button and gives an explicit
Install / update server message before any upload request. Protocol/GREETING remain 1; `server_types` is unchanged.

1. **New calculation:** in a local Calculation tab, choose **Upload to server…** or **Upload and run…** beside
   the pipeline run buttons. The existing connection dialog selects the host and browses an existing parent;
   the target field can name a new directory. Targets must be absent or empty. An existing pipeline must first
   be fetched through Edit locally; unrelated files are never overwritten to create a pipeline.
2. **Fetch an existing calculation:** choose **Edit locally…** in the remote Calculation tab. The editable local
   path defaults to `~/grasp_calcs/remote/<host>/<server-directory>/`. The server returns shared configuration,
   small attached inputs and a revision. A local path containing unrelated data is refused; fetching the same
   linked copy again asks default-No before discarding pipeline edits. Existing local input conflicts are retained.
3. **Edit:** use the normal program forms, pipeline actions, validation and preview. A persistent
   **Server copy host:/path** label and main-window title identify the copy. Local missing inputs are labelled
   **To be checked on the server**, while invalid parameters remain errors. Running the copy on this computer
   requires a separate default-No confirmation.
4. **Upload:** review the server-side changes, selected inputs, pending uploads, missing inputs and custom
   commands. Small local files are selected by default; generated outputs, large files and existing server files
   are not. Internal `.grasp*` files are excluded. Custom commands require their own confirmation, and the final
   upload confirmation defaults to No. Shared attached text files travel with the record; selected other inputs
   use the existing rsync transport after the record has been saved.
5. **Run:** Upload and run opens the usual remote window and `run_plan` confirmation flow, using the selected
   host's workstation or Slurm mode. Required inputs are checked again with the actual run settings. Uploading
   a record with unresolved missing inputs does not bypass the final run preflight.

**Conflict handling:** preview is read-only. The write locks the directory and checks its revision and idle
state again. A changed revision, active background calculation, queued/running Slurm job or unknown Slurm
submission refuses the write. Use **Fetch server pipeline again**, exporting local edits first if needed.
Server files with the same name are retained in this workflow, including shared-input conflicts; use the separate
file-transfer dialog when an intentional replacement is needed. A failed rsync leaves the saved record and link
intact and explains how to retry the inputs. It never removes the server record to make a retry possible.

**Unconfirmed writes:** create/update are mutations and are never replayed after disconnect. A pending intent
is saved in local preferences. On reconnect, `get_pipeline` compares the actual server pipeline and attached
configuration inputs with the pending configuration. An equal configuration establishes the new revision;
it still asks for confirmation before retrying input transfer or applying later local edits. If the server revision
is unchanged from the saved baseline, the update was not applied: pending is cleared and the local edits remain
available for a new user confirmation. When a new calculation has no record, a separate locked read-only preview
must prove that its target is absent/empty before enabling a manually confirmed create retry. Neither case sends
a write automatically. Changed revisions/content, nonempty targets and failed checks remain conflicts.
Closing and reopening the uploader retains pending verification; all retried writes repeat server-side checks.
Unlink local copy asks default-No and changes only local preferences, leaving the server untouched.

中文操作：本机计算页的“上传到服务器…”新建计算；远程计算页的“在本机编辑…”取出已有流程，继续使用原程序页修改。
关联信息只保存在用户设置 `remote_copies`（主机、服务器路径、修订号，以及待核对写入的指纹），不改变 `.grasp_gui.json`。
主窗口标题与计算页常驻显示服务器副本来源。本机缺少的结果/输入标为“由服务器检查”，本机直接运行副本另有默认否确认。
上传先预览差异并确认，再在锁内核对修订号和运行状态，先建/更新记录再传输入。记录或任务状态冲突时拒绝覆盖，提供重新取出。
同名服务器文件保留；传输失败只需补传，不删除记录。丢失回答先核对：配置一致则关联；修订号仍等于保存值则提示“写入未执行，
可以再次确认上传”，保留本机修改。新建没有记录时须先证明目标仍为空，才允许用户重新确认创建。均不自动重发；其他变化保持冲突。
选择“上传并运行…”后仍走现有工作站/Slurm 的确认和最终输入检查。

Screenshots: [target EN](images/en_remote_edit_target.png) / [目标中文](images/zh_remote_edit_target.png),
[upload EN](images/en_remote_edit_upload.png) / [上传中文](images/zh_remote_edit_upload.png),
[confirmation EN](images/en_remote_edit_confirm.png) / [确认中文](images/zh_remote_edit_confirm.png),
[linked copy EN](images/en_remote_edit_copy.png) / [副本中文](images/zh_remote_edit_copy.png),
[confirmed retry EN](images/en_remote_edit_retry.png) / [重新确认中文](images/zh_remote_edit_retry.png).

### Optional remote-edit SSH acceptance / 可选 SSH 写入验收

Reuse the dedicated restricted sshd/rsync fixture below, install this branch's server build, then opt in:

```sh
export GRASP_REMOTE_EDIT_TEST=1
# Also set GRASP_SSH_TEST_HOST, GRASP_SSH_TEST_DIRECTORY and optional CONFIG/PROGRAM as below.
GRASP_BINDIR=/path/to/local/grasp/bin python3 -m pytest tests/test_remote_edit_ssh.py -v -ra
```

The directory must be the dedicated fixture root, never a production calculation root. Tests create unique
`edit-*` subdirectories and retain them for inspection. They prepare isodata locally, upload it with rsync,
run example1, fetch/edit/upload an rci setting, and compare energies against a direct SSH script with the same
parameters. They also reject stale revisions and writes during a running fixture job. Set
`GRASP_SSH_TEST_OLD_PROGRAM` to an old server executable for the old-capability check.
Both local rsync/GRASP and server GRASP are required. No fixture means these three tests skip.
The maintainer ran all three tests against this branch's server source in a restricted sshd fixture (MaxSessions 10,
forwarding disabled), GRASP `9006157`, and the official 2.9.0 package as the old server: **3 passed**.
See the [acceptance review](https://github.com/skq1998/grasp_gui/pull/47#pullrequestreview-5820086104).
The current unexecuted-write recovery changes have local GUI coverage; the maintainer will repeat SSH acceptance
before merging. This development environment still has no dedicated sshd container.

### 安装服务器端

优先使用无 Qt 的服务器目录版 (glibc ≥ 2.17)，可在下方的安装对话框中明确确认后安装。
手动安装时，把服务器版压缩包和校验文件上传到服务器，先核对校验和，再解压到新的版本目录：

```sh
sha256sum -c grasp_gui-server-linux-x86_64.tar.gz.sha256
mkdir -p "$HOME/.local/lib/grasp_gui/2.8.0" "$HOME/.local/bin"
tar xzf grasp_gui-server-linux-x86_64.tar.gz --strip-components=1 -C "$HOME/.local/lib/grasp_gui/2.8.0"
"$HOME/.local/lib/grasp_gui/2.8.0/grasp_gui-server" --version
ln -s "$HOME/.local/lib/grasp_gui/2.8.0/grasp_gui-server" "$HOME/.local/bin/grasp_gui.next"
mv -Tf "$HOME/.local/bin/grasp_gui.next" "$HOME/.local/bin/grasp_gui"
```

默认依次尝试 `~/.local/bin/grasp_gui`、PATH。非交互 SSH 的 PATH/module 环境可能与终端不同，可在连接对话框
指定完整路径；成功选择目录时按主机保存实际路径和目录 (`remote_hosts` 用户偏好)。找不到、不能启动或协议不兼容时，
对话框显示原始错误输出，可明确选择安装捆绑版、使用已有路径或取消。手动更新后也可设置完整路径重试。
示例中的版本目录需替换为所下载的版本，不覆盖仍运行的版本；管理员可统一安装到 `/opt` 或通过 module 提供路径。
工作站上的 GRASP bin、MPI 等运行设置由服务器端原有配置决定；Slurm 作业的路径、资源和模块命令在提交对话框中核对。

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

工作站类型在启动前调用 `logout_kills()` 检查 systemd-logind 的 KillUserProcesses。如果会随退出登录清理，默认取消；可选：

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
The hello reply also lists supported `server_types`; Slurm classification requires the `slurm` capability.
Requests and replies are newline-delimited UTF-8 JSON; request IDs match replies and replies may arrive out of order.
`levels` and `view(kind=levels)`, including the brief preview, return per-level `zero_group` and `zero_warning`.
The server reads the matching `.csum` through the common Hamiltonian parser; no new request or protocol number
is needed. The existing directory signature includes `.csum` size/mtime, so changing only the summary refreshes
remote grouping. Older servers without these fields are shown as unknown for `.cm`, without local file reads.

`levels` 和 `view(kind=levels)`（含预览）逐行返回哈密顿量组及警告；由服务器读取 `.csum`，不增加请求或改变协议号。
目录签名已有 `.csum` 大小/修改时间，单独更新总结文件也使远程缓存失效；旧端缺字段时按未知组提示，不查本机文件。

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
| `levels` | Directory, optional files/limit/components; jj/LSJ metadata/components and additive `zero_group`/`zero_warning` |
| `transitions` | Directory, optional files/limit; existing λ, A, gf, S, computed-only branching ratios |
| `orbitals` | Directory, optional files/limit; properties computed on the original grid |
| `wavefunctions` | Directory, optional files/names; points 2–10000, at most 100 sampled curves |
| `summary` | Directory, optional per_block (1–200, default 5), selected sections; server-rendered text and configuration availability |
| `transfer_files` | Directory, optional relative names, rsync path and destination names; regular-file inventory and rsync capabilities |
| `composition` | Directory, mixing file, block, index; up to 500 leading components |
| `view` | `kind: levels` or `wfn`; a whole view in one request, optional file/name selection and known signature |
| `pipeline` | Read-only saved jobs and revision |
| `get_pipeline` | Read-only shared configuration, bounded attached text inputs, warnings and revision |
| `preview_upload` | Read-only new/update validation, changes, input checks, pending/existing files, additive `missing_programs` from server bindir/PATH; no directory creation |
| `create_workdir` | Mutation: checked configuration and explicit custom-command authorization; absent/empty target under lock |
| `update_workdir` | Mutation: revision, configuration and explicit custom authorization; locked conflict and idle checks |
| `run_plan` | Revision and custom commands; optional `server_type: slurm` returns server-side Slurm defaults |
| `clear_slurm_record` | Mutation: explicit `confirmed: true`, matching record revision, fresh queue check, then archival |
| `run` | Revision, one-based start, explicit custom/logout/scope confirmations; Slurm also accepts reviewed `slurm` settings |
| `stop` | Existing background stop, or verified scancel for the recorded Slurm job |
| `run_status` / `tail_log` | Background/Slurm state and the matching bounded log tail |
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
Older clients use keys only. Passwords are never saved or logged. Analysis traffic uses the desktop-initiated
stdio session, without forwarding or a server listening socket. Slurm clients additionally contact their
configured cluster scheduler/accounting services.

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


## Headless server package / 无界面服务器版

`grasp_gui-server` is the CLI-only entry point: `remote`, `mcp`, `check`, `script`, `steps`, `reference`
and `_run` use the same implementations as the desktop build. `--version` reports the application version and
remote protocol version; `--lang en` selects English. Opening a window exits with code 2 and directs the user
to the desktop or legacy build. MCP remains disabled until the user enables it; plotting tools require Qt and
return a clear error in this build. There are no automatic downloads; Slurm mode uses the installed cluster clients.

服务器版使用独立的标准库入口 `grasp_server.py`，不导入界面。上述命令与普通版共用实现；
`--version` 同时显示程序和远程协议版本，`--lang en` 可切换英文。窗口命令返回 2，提示改用桌面版或旧系统版；
MCP 开关仍由用户控制，绘图工具在没有 Qt 时返回明确错误。目录版构建及手动安装见
[legacy/README.md](https://github.com/skq1998/grasp_gui/blob/legacy/legacy/README.md)。


## Selected file transfer / 选中文件传输

The remote window File menu transfers only confirmed files using the system **rsync** on both computers.
Analysis still runs on the server; no raw files are downloaded merely to view results. There is no scp/sftp
fallback and no bundled rsync or Python dependency. A custom server rsync path is stored per host.

Transfer windows are non-modal: analysis and the main window remain usable, with progress also shown in the
remote status bar. Closing a busy transfer window asks whether to cancel (default No); declining keeps the
window open and the transfer running. Closing the remote window cancels all of its transfers directly.
传输窗口为非模态，传输时仍可分析结果、操作主窗口，远程状态栏同步显示进度。
传输中关闭传输窗口需确认取消（默认否）；拒绝时窗口保持打开，传输继续。关闭远程窗口直接取消其全部传输。

Close confirmation / 关闭确认：[English](images/en_rsync_cancel.png) / [中文](images/zh_rsync_cancel.png)。

The dialog recursively lists regular files with sizes in decimal MB. Symbolic links and special files are excluded and their
skipped count is shown; links are never followed. Files **larger than 100 MB** are unchecked by default;
1 MB is 1,000,000 bytes. The editable
per-host threshold resets selections, and large files can be selected individually or with Include large files.
The large-file reminder explains that .w/.c/.cm/angular files can usually stay on the server. Confirmation
separately reports selected small files, selected large files and unselected large files, with counts and totals.
Upload uses the same rules. Download initially targets `~/grasp_calcs/remote/<host>/<remote-directory-name>/`,
which can be changed. Selecting local directories preserves their basename; selecting files preserves their
relative paths from the common parent. Existing files default to Skip; Resume / verify and Overwrite are explicit.

Final options are -rt, --checksum, --partial, --partial-dir=.grasp-rsync-partial, --safe-links, --files-from=-
and --from0. The NUL-separated manifest contains only checked relative names. Resume uses rsync's delta algorithm
and partial data; transferred content is also checked by rsync's full-file verification. Overwrite adds
--ignore-times --whole-file; Skip adds --ignore-existing. No --delete, --copy-links or --keep-dirlinks is used.
Source size/mtime is checked again before transfer; destination paths with symlinks are refused.

The -e command derives from the existing SSH Client options, including ControlPath, aliases, identity and jump
host settings in the master. Explicit -J is omitted from the auxiliary command because that route is already
established; otherwise SSH rejects -J together with the no-fallback ProxyCommand. Prepending ControlMaster=no,
ProxyCommand=false and BatchMode=yes permits only the already
authenticated master: if it vanishes, transfer fails instead of authenticating again. rsync needs a second SSH
session; MaxSessions 1 reports an explicit administrator-action message and leaves analysis usable. Missing
rsync reports installation/path guidance. Cancellation interrupts only the rsync process group, retains partial
data, and does not stop the analysis connection. Progress, speed and ETA are displayed without blocking Qt.

Capabilities are read from --version/--help on both ends. GNU rsync 3.1+ uses --info=progress2; older capable
versions use --progress. Special paths require --protect-args/-s support at both ends. Implementations without
the required partial/checksum/manifest options (including basic OpenBSD openrsync) fail clearly and suggest
GNU rsync; they never silently drop integrity or resume guarantees.

用系统 rsync 显式传输勾选文件，不改变服务器分析结果的方式，不自动传输程序；服务器版的安装更新另有确认流程。
两端缺少 rsync 或缺少必要的续传/校验选项时明确提示，不换用其他传输工具。MaxSessions 1 不能再开传输会话，
因此给出管理员调整提示，原分析会话仍可继续。GNU 新版显示总进度，旧版按可用能力显示逐文件进度；
基础 openrsync 缺少必要选项时提示安装 GNU rsync。断线或取消后的部分数据保留，重新选择“续传 / 校验”可继续。

### Optional SSH acceptance / 可选真实验收

Use dedicated test accounts and a disposable writable directory. One fixture needs MaxSessions > 1 and an
optional second fixture uses MaxSessions 1; forwarding and external routing may remain disabled. Tests generate
200 MiB dummy files, cancel and kill the transfer SSH client, resume, compare SHA-256, exercise all overwrite
policies and missing rsync, then remove only their generated subdirectories and local large files.

```sh
GRASP_RSYNC_TEST_HOST=test-alias \
GRASP_RSYNC_TEST_SINGLE_HOST=single-session-alias \
GRASP_RSYNC_TEST_CONFIG=/path/to/test-ssh-config \
GRASP_RSYNC_TEST_DIRECTORY=/absolute/disposable/directory \
python3 -m pytest tests/test_transfer_ssh.py -q
```

Tests skip without the dedicated fixture or local rsync. Never point the test directory at calculation results.

## Bundled installation / 从桌面端安装服务器版

In **Connect to server**, **Install / update server…** opens an explicit installation dialog;
**Remove from server…** uses the same host settings. The application never installs automatically. When the
server program cannot be started, a build with a bundle offers installation, an existing path, or cancel;
authentication, host-key, network and timeout errors only update the status. Only a numerically older compatible
server version offers an update, with No as the default; newer servers never prompt for a downgrade. An
incompatible protocol is reported in the status area: use **Install / update server…** or choose another server
executable.

The dialog displays the host, bundled version/size, install root, executable link, architecture, glibc and free
space. **Check server** closes this dialog's analysis session before using SSH, so MaxSessions 1 works. It checks
x86_64, glibc ≥ 2.17, write access, executable filesystem and space for the archive plus staging; when the install
root does not exist yet, the nearest existing parent is tested and the root is not created. No sudo or shell/PATH
changes are made. An administrator-managed executable or link is not replaced.

**Confirm installation** sends the bundled archive on SSH stdin, checks SHA-256, unpacks into a private staging
directory, checks --version and the remote handshake, then atomically replaces ~/.local/bin/grasp_gui.
The default root is ~/.local/lib/grasp_gui/<version>/; a custom root is saved per host. The previous version and
any older running versions remain; obsolete application-owned versions are removed. The progress bar supports
cancel; failed/interrupted transfers clean temporary files and keep the current link. An existing version
directory with different contents is refused rather than overwritten.

Removal first lists application-owned version directories and the link and asks for confirmation. It refuses
while an installed version is running and keeps unrelated files, calculation directories and results. It also
clears the previous-version record; a missing install root is left alone.

Build the server archive on legacy first, then use
`./build.sh [--onedir] --server-bundle /path/to/grasp_gui-server-linux-x86_64.tar.gz`
(or GRASP_SERVER_BUNDLE). The adjacent .sha256 file and SERVER_VERSION inside the archive must match this
desktop release/protocol. Every published desktop release must include the server archive. For development,
CI or self-built binaries, omitting the option still builds normally; the installation button is then disabled
with **This build does not include the server package**, and removal remains available. Legacy builds do not
bundle it. No runtime downloads or external server connections occur.

“连接服务器”中的“安装 / 更新服务器版…”和“从服务器移除…”沿用每台主机的设置。程序不自动安装；
服务器端程序不能启动时，带捆绑包的构建可选安装、使用已有路径或取消；认证、主机密钥、网络和超时错误只显示在状态栏。
只有服务器版本较旧且协议兼容时才提示更新 (默认否)，较新的服务器不会提示降级；协议不兼容时在状态栏说明，可手动安装 / 更新
或改用其他程序。安装对话框先列主机、版本、大小、目录和链接，检查架构/glibc/空间/写权限及 noexec
(安装目录还不存在时检查最近的已存在上级目录，不创建目录)，再由用户明确确认。
分析会话先关闭，安装通过 SSH 标准输入传输，不需要 scp、sftp、端口转发、管理员权限或外网。
校验、解压及版本/握手检查通过后才原子切换链接；保留上一版及仍运行的旧版，取消或失败清理临时目录。
移除前列出本程序安装的版本和链接，运行中的版本拒绝移除；不动工作目录、计算结果、无所有权标记的目录和管理员程序，
同时清除上一版记录；安装目录不存在时不做任何操作。
正式桌面发行版全部捆绑服务器包。开发、CI 或自行构建时可不捆绑，仍可连接已有程序，安装按钮禁用并明确显示“本构建不含服务器版”，
移除仍可用。
