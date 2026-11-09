# Interface reference / 界面说明

A detailed description of the GUI: what each window, tab, menu and control does. The README only lists the
features; this file is the reference for users who want the details and for development (to know the interface
without starting it). Labels are given as *English (中文)*. Screenshots: `docs/images/` (made by
`tools/make_screenshots.py`). Keep this file up to date when the interface changes.

本文件详细说明界面的各个窗口、标签页、菜单和控件，供需要细节的用户和开发时参考 (不必启动程序即可了解界面)。
控件名写作“英文 (中文)”。界面改动时请同步更新本文件。

## Main window / 主窗口

```
┌ Menu: File  View  Settings  Window  Language ──────────────────────────────────────────┐
│ Working directory [__________________________] Browse… Recent▾ ▸Settings     │
│ GRASP bin: /opt/grasp/bin · serial          (summary; ▾Settings expands it)  │
│ ┌ Calculation ┬ Radial wave functions ┬ Levels ┐          Open in new window │
│ │  tab contents                                                              │
│ └───────────────────────────────────────────────────────────────────────────┘│
│ status bar                                                                   │
└──────────────────────────────────────────────────────────────────────────────┘
```

* **Working directory (工作目录)**: edit + *Browse… (浏览…)* + *Recent (最近)* menu (its first item, *Open another
  directory… (打开其他目录…)*, opens a folder dialog). Changing it saves the session
  of the old directory, loads the new one's, and refreshes the viewers. On start the last directory is opened.
* **Settings (设置)** toggle: collapsed it shows a one-line summary (`GRASP bin: … · serial` / `MPI n processes`);
  expanded: *GRASP bin directory (GRASP bin 目录)* + *Browse…*, *MPI processes (MPI 进程数)*, *mpirun command
  (mpirun 命令)*, *MPI_TMP*, *Lower priority of calculations (nice) (降低计算进程优先级)*. With more than one process
  `rangular`, `rmcdhf`, `rci`, `rbiotransform`, `rtransition` run as `mpirun -np N <prog>_mpi`. The collapsed state
  is remembered. For a new user, the process count defaults to `min(physical cores, 4)` when the configured
  MPI launcher and the MPI versions of rangular, rmcdhf, rci, rbiotransform and rtransition can be found
  through the normal GRASP bin/PATH lookup;
  otherwise it defaults to 1. Physical cores come from Linux CPU topology or macOS `hw.physicalcpu`, with
  a fallback of half the logical cores (at least 1). The initial value is saved immediately. Existing saved
  counts are retained; changing the bin directory or launcher never recalculates them. The input maximum
  remains the logical core count. Its tooltip explains that hyperthreading offers little benefit for GRASP.
* **GRASP bin lookup**: `$GRASP_BINDIR` → last used → directory of `rmcdhf` on `PATH` → `../bin` of the program.
* **Tabs (标签页)**: *Calculation (计算)*, *Radial wave functions (径向波函数)*, *Levels (能级图)*; bold labels.
  The viewer tabs show the working directory and refresh after a run finishes. *Open in new window (在新窗口中打开)*
  (corner button, viewer tabs only) opens the current viewer in a separate window with its own directory bar
  (*Directory (目录)*) and a *File* menu (*Open directory…*, recent directories, *Close*) — e.g. to compare two
  calculations or to see a viewer next to the calculation tab.
* **Drag and drop**: directories and files dropped on a viewer open there (a mixing file → Levels, a `.w` file →
  Radial wave functions, only that file checked).
* **Status bar (状态栏)**: messages of the current tab only (background viewers do not overwrite it).
* **Closing** while a program runs asks for confirmation and terminates the whole process group.
* **Splitters (分隔条)**: wide handles with five dots, highlighted under the mouse.
* On Wayland the window frames use a blue-grey colour so dialogs are distinguishable from the main window.

### Menus / 菜单

| Menu | Items |
|---|---|
| *File (文件)* | *Import example… (导入示例…)*, *Import configuration file… (导入配置文件…)*, *Export configuration… (导出配置…)* — *Save parameters… (保存参数…)*, *Load parameters… (载入参数…)* — *Export pipeline as shell script… (导出流程为 shell 脚本…)*, *Export Slurm job script… (导出 Slurm 作业脚本…)* — *Open working directory… (打开工作目录…)* (Ctrl+O), *Recent working directories (最近的工作目录)* — *Quit (退出)* |
| *View (视图)* | *Larger font (放大字体)* Ctrl++ / Ctrl+=, *Smaller font (缩小字体)* Ctrl+−, *Default font size (默认字体大小)* Ctrl+0 — the three tabs, Ctrl+1/2/3. The font size is remembered; changing it rebuilds the interface (parameters, pipeline and output are kept). |
| *Window (窗口)* | *Open Radial wave functions / Levels in new window (在新窗口中打开…)* |
| *Settings (设置)* | *Allow AI agents via MCP (允许 AI agent 通过 MCP 使用本程序)*: checkable, off by default. Enabling shows a selectable explanation and Claude/Codex/Gemini client configurations; disabling rejects subsequent MCP tools, while background runs continue. 设置项默认关闭；勾选后弹出说明和可复制配置，取消后后台计算继续。See [MCP](MCP.md). |
| *Language / 语言* | 中文, English (rebuilds the interface; remembered) |

### External session changes / 外部记录修改

Before saving and on focus return, the calculation panel checks whether another program changed its
workdir record. A dialog shows the same change summary as the configuration update preview. **Load new
record (载入新的记录)** is the default and does not start new steps. **Keep my changes (保留我的修改)**
requires another confirmation before overwriting. Cancel leaves the record untouched. A write error is
reported; a read-only viewer can still close, while starting a run requires a saved record.

保存之前和窗口重新获得焦点时，计算页检查工作目录记录。外部修改会显示与更新预览相同的摘要；
默认载入新记录，新增步骤不自动运行；保留自己的修改须再次确认覆盖。取消不写入。保存失败会提示，
只读查看仍可关闭；开始计算前必须保存成功。

While MCP is enabled, opening a calculation with `started_by: "mcp"` shows **[This calculation was
started by an MCP agent] ([此计算由 MCP agent 启动])** in the output, including after completion.
With MCP disabled, the toggle is the only agent-related interface item.
MCP 打开时，打开该来源的计算会在输出区显示来源；关闭时不显示 agent 提示，只有菜单开关。

### Connect to server / 连接服务器

**File → Connect to server… (文件 → 连接服务器…)** opens an SSH dialog: editable aliases from ssh_config,
server executable (default discovery or full path), directory, Connect/refresh, Parent directory, file metadata,
and Use this directory. Double-click directories to browse. Host-specific executable/directory preferences persist;
credentials do not.
**Server type (服务器类型)** selects Workstation (existing background execution) or Slurm cluster (batch submission,
scheduler state and cancellation); it is saved per host. Unclassified profiles require an explicit choice; the dialog
explains that login nodes should not run calculations directly. Slurm needs
a supporting server build; selecting it against an older build asks the user to update instead of running directly.
Errors show the server diagnostic and manual installation guidance. Password/verification-code
prompts use OpenSSH ASKPASS; old OpenSSH supports keys only. First-contact host-key confirmation shows the full
prompt/fingerprint with a visible input field and a verification reminder; passwords remain hidden. Hardware-key
notifications have no input or reply and close when ssh finishes the operation. Details and installation: [REMOTE.md](REMOTE.md).

The separate remote window shows **Server host:/path (服务器 host:/path)** above all three tabs and in its title.
Calculation is read-only: select a pipeline step to inspect parameters/input, run all/from selection, stop, refresh,
and follow pushed logs. Custom commands require confirmation. Logging-out policies may require choosing user scope,
Slurm export or explicit normal startup. Radial and level tabs reuse the existing viewers with remote data;
levels/compositions and coarse/fine curves appear progressively. Hover, selection, zoom and pan use cached data.
Load more components explicitly requests up to 500 items. Remote transitions display existing results; drawing a
pair never starts a new transition calculation. Diagram marks are temporary. Export CSV/figures locally;
original GRASP transition-file export is unavailable because raw data stays on the server. Window opens extra viewers sharing
the same session, with the same source in their titles, and offers Reconnect after bounded automatic retries.
Results summary on the remote Levels tab uses the same dialog, with server-generated text and server-only run
state. Changing sections/level count requests a new summary. The dialog title shows the source; save chooses a
local file, starting in the desktop home directory. Cancel/older responses do not alter the clipboard or preview.
Closing the remote window closes its viewers and connection; it does not stop a background calculation.

For Slurm, **Submit Slurm job (提交 Slurm 作业)** reviews resource settings and asks for default-No confirmation.
The state shows the job ID and scheduler state; pending/running/unknown jobs disable submission. Stop uses
scancel only after verifying the saved job identity. The log belongs to that submission and reopens on a new
desktop connection. Missing queue/accounting information stays Unknown; it never becomes Completed merely
because the login-node process is absent. Resource settings persist per host; job identity persists on the server.
Lost submission replies recover by the saved queue comment. When accounting is unavailable after leaving the
queue, an atomic EXIT marker may establish script exit (`BATCH_EXITED`). After successful scancel,
a saved cancellation request plus a 130/143 exit marker displays Cancelled without accounting;
other failures and successful completion retain their own states. The request alone is not a terminal result.
Unknown/malformed records expose **I have checked; clear this job record…**: default-No confirmation,
a fresh server queue check and revision check,
then archival, with logs/scripts retained. Clearing does not cancel a job or establish success. Failed checks leave
the record unchanged. Valid records copied from a different directory are ignored in the copy.
选择“工作站”沿用后台直跑；选择“Slurm 集群”后通过资源设置及默认否确认提交 sbatch，按作业 ID 查询调度状态，
停止调用 scancel。排队、运行或状态未知时禁止重复提交；更换电脑或登录节点后，打开同一集群的原目录可恢复状态和日志。
类型和资源偏好按主机保存在本机，作业记录保存在服务器。调度状态不表示每步物理计算均成功。
**Edit locally… (在本机编辑…)** fetches the shared pipeline and revision into a local working directory, defaulting
to the same `~/grasp_calcs/remote/<host>/<directory>/` location as downloads. An unrelated nonempty local directory
is refused. Re-fetching the same copy asks default-No before replacing pipeline edits; local input conflicts remain.
The main window opens its original Calculation tab, with a persistent Server copy host:/path label and title.
Old servers without `workdir_write_v1` show an update instruction and receive no write requests.
远程“在本机编辑…”取出流程、附带小输入和修订号，用本机原计算页编辑；不提供第二套远程表单或新标签页。
无作业号时按队列 comment 找回；离队且无记账时可用 EXIT 标记判定脚本退出。未知/损坏状态显示“我已核对，清除此作业记录…”，
默认否确认后由服务器重查队列和记录修订号，通过后归档原记录，保留日志与脚本。清除不取消任务，也不代表计算成功。
路径不匹配的有效记录不属于当前目录，不影响副本使用。新连接提示说明集群登录节点不宜直接运行计算。

“连接服务器”对话框可选 SSH 别名或手输 user@host、设置程序完整路径、浏览目录；成功使用目录后保存每台主机的
程序位置与目录，不保存密码。连接失败显示原始诊断和手动安装提示。远程窗口及独立查看器显示服务器来源，计算流程
只读，参数和输入预览不能编辑；可运行、停止和跟踪日志，自定义命令须确认。会受退出登录清理影响时默认取消，
用户可选 scope、导出 Slurm 或明确照常启动。能级/组成、粗/细曲线渐进显示；选中、悬停、缩放和平移使用缓存，
“读取更多组成”才补取数据。远程跃迁页只读已有结果，图上标记不写文件。“窗口”菜单可开共享连接的查看器并重新连接。
远程“结果摘要…”由服务器读流程、状态并生成文本；勾选/能级数改变后重新请求，复制与本机保存沿用同一对话框。
标题显示服务器来源，保存从本机家目录开始选择；不使用桌面会话历史，不在桌面读取服务器目录，取消或旧回答不改变剪贴板。
关闭远程窗口同时关闭这些查看器和连接，后台计算继续。完整协议、缓存/重连限制见 [REMOTE.md](REMOTE.md)。

The remote window's **File** menu offers **Download to this computer…**, **Upload from this computer…**
and **Upload local directory…**. The transfer dialog lists all regular files and sizes in decimal MB, excludes symbolic
links/special files and defaults to selecting files no larger than 100 MB each (decimal MB). The threshold and
server rsync path are saved per host. Changing the threshold resets the selection; large files need individual
selection or Include large files. Confirmation separately counts selected small, selected large and unselected
large files. Skip existing files is the default, with Resume / verify and Overwrite alternatives. The background
progress shows speed and remaining time, and can be cancelled; partial data is kept for a later resume.
Transfer windows are non-modal, so analysis and the main window remain usable. Closing a transfer window
while it is busy asks whether to cancel, with No as the default; declining keeps it open and transferring. Closing
the remote window cancels its transfers. The default download location is `~/grasp_calcs/remote/<host>/<directory>/`.

远程窗口“文件”菜单提供下载、上传文件和上传目录；清单列出全部普通文件及十进制 MB 大小，跳过符号链接及特殊文件。
单个文件大于 100 MB 默认不勾选，可逐个勾选或使用“包括大文件”；阈值按主机保存，修改后重置勾选。
确认时分别列出小文件、选中大文件和未选大文件的数量/大小；同名文件默认跳过，也可续传/校验或覆盖。
传输窗口为非模态，传输时仍可操作远程分析和主窗口；进度、速度、剩余时间同时显示在传输窗口和远程状态栏。
传输中关闭传输窗口会询问是否取消，默认否；拒绝时窗口保持打开，传输继续。取消保留部分数据。
关闭远程窗口会直接取消其传输；下载目录可自行选择。

The connection dialog also has **Install / update server…** and **Remove from server…**. They show the host,
bundled version/size, editable installation root, checks and exact paths before a default-No confirmation.
Transfer progress and Cancel are available; returning from installation verifies the server and saves its path.
Development or self-built binaries without a bundle disable the installation button with a clear message; removal remains available.
Only older compatible server versions offer an update, which can be declined. Authentication, host-key and
network failures remain in the status area and do not open the installer.
“连接服务器”另有安装/更新与移除入口：先检查并列出主机、版本、大小、目录、链接及服务器检查结果，
默认不确认；可取消传输。无捆绑包时安装按钮禁用，移除仍可用；只对较旧服务器提示升级，认证和网络错误不弹安装框。安装成功核验后保存路径并重新连接。移除只处理列出的本程序文件，计算结果保留。

### Command line / 命令行

```
grasp_gui [DIR]            main window (default: last working directory)
grasp_gui levels [DIR]     open on the Levels tab (default: current directory)
grasp_gui wfn [DIR]        open on the Radial wave functions tab
grasp_gui FILE             open the tab for this file by content, only this file checked
                           (.m/.cm → Levels, .w → Radial wave functions); unrecognised file → exit code 2
--lang zh|en               interface language (remembered)

grasp_gui check CONFIG.json             check a shared configuration (exit code 1 if it cannot be imported)
grasp_gui script CONFIG.json [--bindir DIR] [--mpi "mpirun -np N"]
                                        print the shell script of its pipeline (runs in the current directory)
grasp_gui steps                         reference of all program tabs and fields (Markdown, = docs/STEPS.md)
grasp_gui reference FILE.cm …           level energies of mixing coefficient files as "reference" JSON
```

`check`, `script`, `steps` and `reference` open no window; `python3 grasp_config.py …` does the same from the
sources. They are meant for scripts and AI agents ([AGENT_SPEC.md](AGENT_SPEC.md)).

A symbolic link named `grasp_levels` / `grasp_wfn` behaves like `levels` / `wfn`.

## Calculation tab / 计算页

```
┌ Setup ┬ SCF & CI ┬ Properties & transitions ┬ Results & tools ┐  State group [odd][even] Copy to even
│ rangular·odd ┬ rwfnestimate·odd ┬ rmcdhf·odd ┬ rsave·odd ┬ rci·odd       │
│  description of the program                                             │
│  form fields (label, editor, hint)                                      │
├ Preview input · ▶ Run this program · Add to pipeline ↓ ········ ■ Stop ─┤
│ ┌ Pipeline ┐            │ Input preview      │ Program output  List files Clear │
│ │ 1. rnucleus  ▶Run all  │ rmcdhf <<'EOF'     │  dark terminal-style output      │
│ │ 2. …         Edit …    │ …                  │                                  │
└─────────────────────────────────────────────────────────────────────────┘
```

* **Program tabs**, two levels (top: group, bottom: program):
  * *Setup (准备)*: `rnucleus`, `rcsfgenerate`, `rcsfinteract`, `rcsfsplit`, `rcsfzerofirst`
  * *SCF & CI (自洽场与 CI)*: `rangular`, `rwfnestimate`, `rmcdhf`, `rsave`, `rci`
  * *Properties & transitions (性质与跃迁)*: `rhfs` (optionally followed by `rhfs_lsj`), `rhfszeeman95`, `ris4`,
    `rbiotransform`, `rtransition`
  * *Results & tools (结果与工具)*: `jj2lsj`, `Coupling`, `rlevels`, `rtablevels`, `rtabtrans1`, `rtabtrans2`, `rmixextract`, `rmixaccumulate`, `rtabhfs`, `rasfsplit`, *Custom (自定义)*
    (any program with hand-written input)
* **Forms**: one per program, generated from the field definitions in `grasp_core.py` (description line, fields
  with hints at the right). Inputs follow each program's default-settings question order; they reproduce the inputs
  of the manual's test cases (`grasptest/` case1–4, example1–5, 8, 9) except non-default `jj2lsj` runs (use
  *Custom*). `rcsfgenerate` has several CSF lists (*+ Add list*, *− Delete current list*, *Reference configurations
  (one per line)*). `rmcdhf` can use the in-memory version `rmcdhf_mem` / `rmcdhf_mem_mpi`.
* **rmcdhf NSIC**: *Modify NSIC (修改 NSIC)* enables a non-negative count of extra first-cycle orbital improvements; unchecked uses the program default.
  EOL defaults: `(NW-NFIX)/4` serial, `4+(NW-NFIX)/4` MPI, or 0 with varied correlation orbitals; AL: `2+(NW-NFIX)/4` (integer division).
  The page description explains the defaults; older configurations import with this option unchecked and no warning.
* **rhfszeeman95**: default-settings mode only. *State name (态名称)* is an ASCII basename of at most 23 characters;
  *Mixing coefficients from CI (混合系数来自 CI 计算)* selects `.cm` rather than `.m`.
  *Compute off-diagonal matrix elements (计算非对角矩阵元)* is checked by default and additionally writes
  `name.(c)gjhfs`. All J blocks must belong to the same saved state and orbital set; no biorthogonal transformation
  is performed. Reads `isodata`, `name.c`, `name.w` and `name.(c)m`. The constants output `name.(c)h` shares its name
  with rhfs but includes an energy table: the later run overwrites the earlier output.
  *Save a copy of constants name.(c)h as (常数结果 name.(c)h 另存为)* copies the new constants after the run
  (empty = no copy); it does not back up the previous output or copy the matrix file.
  *Output log file (输出日志文件)* is optional. Parameters are separate for odd/even state groups.
  中文：本页只使用默认设置，非对角矩阵元默认勾选；不同 J 块须在同一态文件中、共用一组轨道。与 rhfs 共用常数文件名，
  后运行的覆盖先运行的；“另存为”只在运行后复制本次常数结果，不备份旧文件，也不复制矩阵文件。参数按态组保存。
* **Zero/first-order partition (零阶 / 一阶划分)**: `rcsfzerofirst` (*CSF list of the zero-order space*, *CSF list to
  partition*, *Save the result rcsf.out as*) puts the zero-order CSFs at the top of each block. `rangular` and `rci`
  have the check box *Zero/first-order partition: CSFs outside the zero-order space treated as first-order
  perturbations* with *CSF list of the zero-order space*: they then answer the non-default questions (`rangular`: `n`
  + ICCUT per block; `rci`: `n`, name, no restart, no speed-of-light change, `y`, ICCUT per block). The ICCUT of each
  block is the number of CSFs in that block of the zero-order list; the input holds the placeholder line
  `@ICCUT <zero list> <CSF list>`, which the run replaces by the numbers (checking that the J/parity blocks match, and
  1 < ICCUT < ncf for `rangular_mpi`); the preview and exported scripts compute them with `awk` in an unquoted
  here-document. `rmcdhf` needs no change. Configurations from 2.1.0 without these fields import without warnings
  (partition off).
* **CSF/ASF tools**: *rmixextract* extracts CSFs above an absolute-coefficient cutoff, either per state or across all states;
  *rmixaccumulate* retains a fraction of the squared coefficients. Both read `name.c` and `name.(c)m`, write `rcsf.out`,
  and can copy it to *Save rcsf.out as (结果 rcsf.out 保存为)*; extraction also writes `name.rmx`.
  *rtabhfs* reads 1–20 `name.(c)hlsj` files (one per line) from rhfs_lsj and writes `hfs.tex`; this source version supports HFS only.
  *rasfsplit* splits `.c` and available `.m`/`.cm` files into `name_evenN.*`/`name_oddN.*` and copies `.w`.
  Prerequisite: all blocks must share the same orbital set, otherwise the split `.w` files are meaningless.
  The form always answers `y` to the common-orbital question; there is no confirmation checkbox.
  The three single-state tools keep separate odd/even parameters; the multi-file HFS table is shared.
* **Other coupling schemes**: `jj2lsj` has *Write files for Coupling (写出 Coupling 需要的文件)*, with an optional
  `block: levels` selection (empty = all), composition print cutoff and backup of the original LSJ labels
  to `name.lsj.lbl_SAVE` **before** conversion. Enable this backup only after running default jj2lsj to create
  the complete `name.lsj.lbl`; the checkbox hint states this prerequisite.
  The external *Coupling* program ([compas/coupling](https://github.com/compas/coupling), separately installed) reads
  `name.lsj.c`/`name.lsj.j` and writes `name.coupN.*.lbl`/`.sum`. Select 1, 2 or 3 coupled shells and the shells to recouple;
  available label schemes depend on that count. *Use the selected scheme labels afterwards (结束后采用指定方案的标记)*
  optionally backs up the current `name.lsj.lbl` to **`name.lsj.lbl_LSJ`** before copying the chosen result there,
  ready for `rlevels`. This preserves the separate pre-conversion `.lsj.lbl_SAVE` even when both backups are
  enabled. The configuration key is `coupling`; the executable is `Coupling`. Parameters are per odd/even group.
  New examples `example2_coupling` and `example3_coupling` preserve the original examples; the Si VIII example generates
  partial labels only, without the manual's hand-edited repairs or a full recoupled energy table.
* **Built-in file steps**: save `rcsf.out` as `name.c`, copy a `.c` file to `rcsf.inp` before `rangular`, run
  `rsave` after `rmcdhf` (*rsave afterwards as*), copy `.c/.w` before `rci`.
* **State groups odd / even (态组)**: `rcsfgenerate`, `rcsfinteract`, `rcsfsplit`, `rcsfzerofirst`, `rangular`, `rwfnestimate`,
  `rmcdhf`, `rsave`, `rci`, `jj2lsj`, `rhfs`, `ris4` keep separate parameters per group (tab labels show
  `· odd`); file names such as `odd2.c` / `even2.c`, `outodd_rci` / `outeven_rci` correspond automatically. The
  other tabs are shared. *Copy to even/odd (rename files) (复制到 …（替换名称）)* copies the current group to the
  other one, replacing the group name in file names (asks first).
* **Buttons**: *Preview input (预览输入)* (shows the generated stdin in *Input preview*), *▶ Run this program
  (运行当前程序)*, *Add to pipeline ↓ (加入流程)*, *■ Stop (停止)*.
* **Pipeline (流程)**: list of steps; *▶ Run all (运行全部)* (stops at the first error), *▶ Run from selected
  (从选中开始)*, *Move up / Move down (上移 / 下移)*, *Edit (编辑)* (loads the selected step's parameters into its
  form, switching to its program tab and state group), *Update selected (更新所选)* (replaces the selected step by
  the current form; it must be the same program), *Show (查看)* (input preview of the step), *Remove (删除)*,
  *Clear (清空)*, *Export ▾ (导出)*: shell script / Slurm job script. Steps imported as commands have no form and
  cannot be edited. The buttons stand in one column, grouped (run; edit / update / show; move; remove / clear;
  export); when the pane is too low for one column they rearrange into two.
* **Check input files (检查输入文件)**, next to *Preview input*: checks the pipeline from the selected step
  (or the current form if there is no pipeline). Starting a run also checks its selected steps, including
  *Update and run added steps*. Existing files and files declared as produced by earlier selected steps are
  accepted. Fixed intermediates and file copies are considered in order; `rsave` moves `rmix.out`, so it is
  unavailable afterwards. The angular-file check uses the same `.30` sentinel convention as the existing
  runner, including each MPI rank; it is not an integrity or physics check. Custom/undeclared steps are
  marked unchecked; a non-modal notice in program output reports their count on run. Missing files produce a step/filename report with similar names; **Run anyway (仍然运行吗？)**
  defaults to No. The same report includes invalid names/parameters with step, program, field, name and limit.
  Derived names are traced to the producer and identify the downstream step that cannot read them. Name/parameter
  issues always block the run, with no Run anyway option, even if files are also missing. Without issues the run
  check is silent; the manual button reports no missing inputs or name issues. It does not generate invalid stdin.
  “检查输入文件”检查从选中开始的流程；流程为空时检查当前表单。运行前也检查，包括更新后运行新增步骤。
  未声明依赖的步骤标为无法检查；缺失时列步骤、文件及相近文件名，默认取消，可选仍然运行。
  名称/参数问题与缺文件合在一份报告，列步号、程序、字段、名字及上限；派生名称追溯到写出它的步骤，并注明后续
  哪一步会失败。只要有名称或参数问题就禁止运行，不提供“仍然运行”，不会先生成错误输入再执行。
* **Input preview (输入预览)**: when the lower area is at least 1300 pixels wide it is a column of its own between the
  pipeline and the program output (the three widths are remembered); when narrower it is a tab next to *Pipeline*.
  It shows the command, the here-document with the program's stdin and the file actions, e.g.
  `rmcdhf <<'EOF' … EOF` followed by `rsave odd4`.
* **Program output (程序输出)**: dark terminal-style pane, last 5000 lines (set a log file in the form for the full
  output); *Working directory (工作目录)* (update, restore record, clear directory; see below),
  *List files (查看文件)*, *Clear (清屏)*. Errors in red, step headers in blue.
* **Background runs (后台运行)**: every run (a program or the pipeline) is executed by a separate background process
  (`grasp_gui _run`); the tab shows its output live and *■ Stop* stops it. A permanent label at the right of the
  status bar shows *Running: step i/n title, this step h:mm:ss (运行中：第 i/n 步 …，本步 …)*, updated every second,
  plus *last output … ago (最后输出于 … 前)* when the programs have printed nothing for a minute or more (GRASP may be
  silent for hours in a long calculation). Messages of other tabs and menu hints do not replace it. The step is
  shown only there (no second status message). After the run the label keeps the result of the directory's last run,
  e.g. *Last run failed at step 3/7 rmcdhf (ended …) (上次运行在第 3/7 步 … 出错)*, also after reopening the
  directory; a failed or stopped run also ends the output with this line.
  * Quitting while a run is in progress asks *Continue in background (后台继续)* (default) / *Stop the run and quit
    (停止计算并退出)* / *Cancel (取消)*; without a run the program quits without asking. If the window disappears
    unexpectedly (ssh disconnected, crash), the run continues in the background.
  * Switching to another working directory leaves the run of the old one in the background (noted in the output).
  * Opening a directory whose run is still going reattaches: the output so far (up to 1 MB) is replayed, then live
    output follows; *Stop* works again. A run that ended while no window was attached is shown once: the end of its
    output and *\*\*\* The background run finished / was stopped (step i …) / failed at step i …*.
  * On machines with systemd-logind `KillUserProcesses=yes`, the end of the login session ends background runs too;
    see [below](#background-runs-and-logging-out--后台运行与退出登录-killuserprocesses).
  * Only one run per directory. If the directory's run is on another machine (e.g. another login node of a
    cluster), its output is read from the log file every 0.2 s and *Stop* explains that it can only be stopped there;
    starting a new run there asks first.
  * Files in the working directory: `.grasp_gui_run.json` (process, host, start / end, steps, current step, result),
    `.grasp_gui_run.log` (the whole output of the last run).
* **Session (工作目录记录)**: parameters, groups and pipeline are saved in `<workdir>/.grasp_gui.json` on every change
  and restored when the directory is opened again. *Save / Load parameters* write / read the same content as a
  JSON file elsewhere.
* **Slurm job script (Slurm 作业脚本)** dialog: job name, partition, nodes, tasks, time, memory, modules, e-mail,
  extra `#SBATCH` lines (remembered). Upload the script with the inputs of the working directory and `sbatch` it.

### Background runs and logging out / 后台运行与退出登录 (KillUserProcesses)

Background runs survive closing the window and an ssh disconnect on most systems. On a machine where systemd-logind
has `KillUserProcesses=yes`, however, everything started in a login session — the GUI, its background run and the GRASP
programs — is ended when **that session ends** (ssh disconnected, or logout from that session). Most common
distributions (RHEL / CentOS / Rocky, Debian, Ubuntu, Fedora) set `no`, but the systemd default is `yes` and
administrators may enable it. When it applies to the current session, the quit dialog says so.

后台运行在多数系统上可以挺过关窗和 ssh 断开。但如果机器上 systemd-logind 设置了 `KillUserProcesses=yes`，在一次登录中启动的
全部进程 (界面、后台运行进程和 GRASP 程序) 会在**这次登录结束** (ssh 断开或退出登录) 时被结束。常见发行版 (RHEL / CentOS /
Rocky、Debian、Ubuntu、Fedora) 默认是 `no`，但 systemd 本身的默认值是 `yes`，管理员也可能打开它。对当前登录有影响时，退出对话框会提示。

Check / 检查 (`b true` = enabled; a user listed in `KillExcludeUsers` is not affected / 列在其中的用户不受影响):

```bash
busctl get-property org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager KillUserProcesses
busctl get-property org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager KillExcludeUsers
```

Remedies / 解决办法:

1. Ask the administrator to set `KillUserProcesses=no` in `/etc/systemd/logind.conf` or to add you to
   `KillExcludeUsers`. 请管理员关闭该设置，或把你加入 `KillExcludeUsers`。
2. Without the administrator: keep your systemd user manager running after logout (lingering; may need permission)
   and start the GUI outside the login session, so that it and its background runs belong to the user manager:
   不需要管理员时：让 systemd 用户管理器在退出登录后继续运行 (lingering，可能需要权限)，并在登录会话之外启动界面，界面和它的
   后台运行进程就属于用户管理器，不随登录结束：

   ```bash
   loginctl enable-linger
   systemd-run --user --scope grasp_gui
   ```

   `loginctl show-user $USER -p Linger` shows `Linger=yes` when the first step worked. 第一步成功后显示 `Linger=yes`。
3. For long calculations on clusters, submit the pipeline as a Slurm job (*Export ▾ → Slurm job script*); login nodes
   often limit long-running processes anyway. 集群上的长时间计算请导出 Slurm 作业脚本提交；登录节点通常本来就限制长时间运行的进程。

### Import example / configuration (导入示例 / 导入配置)

Dialog *Import configuration (导入配置)*: an explanatory line at the top; left the list of examples (GRASP2018 manual:
example1–5, 8, 9, case1–4; case2 is C IV at Z=6, case3 Fe XV at Z=26; manual_csfs covers §§5.1–5.8, manual_zf covers §14.1) or, with *Open configuration file… (打开配置文件…)*, a file; right an information
pane: title, origin (*Example included with the program*), summary, *Estimated time (预计时间)*, *Package (程序包)*,
*Pipeline (流程)* (n steps), *Programs used (用到的程序)* (missing ones in the GRASP bin directory marked), attached
files, compatibility warnings, **custom commands** (orange, on a light background: *Custom* tab steps and steps
stored as commands — they can run any program), *Reference results (参考结果)* (energies of the mixing coefficient
files). Bottom: *Import into directory (导入到目录)* + *Browse…* (default `~/grasp_demos/<id>` for examples,
`~/grasp_calcs/<name>` for files; must be empty or new), *Import (导入)*, *Cancel*. Importing a file with custom
commands asks for confirmation. After import the GUI switches to that directory; *▶ Run all* computes it.

*From clipboard (从剪贴板读入)* beside *Open configuration file…* reads the normal CLIPBOARD (Ctrl+C),
not X11's mouse PRIMARY selection. It accepts one JSON object, optionally wrapped in a `json` or plain Markdown
code block. Preview, warnings, attached files and custom-command confirmation are the same as file import;
the source is shown as *Clipboard (剪贴板)*. *File → Import configuration file…* opens this dialog so either
source can be chosen. Clipboard imports suggest a directory under `~/grasp_calcs/` from the configuration id,
or its title when no id is present (sanitized as a file name, with a suffix if occupied). The shared text/file limit is 32 MiB; the existing attached-file limits still apply.

中文：从剪贴板读入支持纯 JSON 和 Markdown 代码块，使用 Ctrl+C 的剪贴板，不读鼠标选中的 PRIMARY。
读取后照常预览并确认自定义命令，来源显示“剪贴板”；默认目录优先用配置 id，没有时用标题，并清理路径字符、避开已有目录。
文件和文本采用相同检查和格式错误提示，文本总上限为 32 MiB。

新增示例：case2 为 C IV (Z=6)，case3 为 Fe XV (Z=26)，只计算所选元素；manual_csfs 为 §5.1–5.8 的 CSF 教学例，
只生成组态列表；manual_zf 为 §14.1 的 Fe XIII 零阶 / 一阶能量计算。[完整清单 / Full inventory](MANUAL_EXAMPLES.md)。

### Update an existing directory / 更新现有工作目录

The import dialog defaults to a new, empty directory. Check *Update current working directory (更新当前工作目录)*
to use the current directory, or open *Program output → Working directory → Update current directory from
configuration… (程序输出 → 工作目录 → 用配置更新当前目录…)*. Running directories cannot be updated or cleared.
The same *Working directory (工作目录)* menu also has *Open working directory… (打开工作目录…)* and *Recent working
directories (最近的工作目录)* for switching directories.
The update preview shows a copyable list of added, removed, moved and changed steps (field values before → after),
with other program-form changes listed separately. Steps are matched by content after filling defaults, so an
insertion keeps later steps unchanged. Attached files are added when absent, skipped when identical; conflicts show
a text diff and are kept unless checked. *Update only (只更新)* retains existing results and highlights the first
changed step. For an unchanged prefix followed by new steps, *Update and run added steps (更新并运行新增的步骤)* starts
at the first new step; overwriting existing inputs disables this shortcut so the required recomputation can be
reviewed first. Before updating, the original record is backed up as `.grasp_gui.json.bak-<timestamp>` (last five
retained). *Restore previous working-directory record (恢复上一次的工作目录记录)* restores parameters and pipeline,
retaining results, attached files and their input list. Restoring first backs up the current record, so restoring
twice returns to the state before the first restore; the confirmation explains this. Shared configuration and
session formats are unchanged.

导入默认写入新的空目录；勾选“更新当前工作目录”可改为原目录，也可在“程序输出 → 工作目录 → 用配置更新当前目录…”进入。
预览按内容比较流程，列出新增、删除、移动、各参数的前后值，其他程序页参数另列；摘要可复制。附带文件不存在则新增、相同则跳过，
冲突显示文本差异并默认保留，勾选才覆盖。“只更新”保留已有结果，并选中首个受影响的步骤；仅在末尾追加时可“更新并运行新增的步骤”，
覆盖旧输入时需先核对重算范围。每次更新前备份原记录，最多保留五份；“恢复上一次的工作目录记录”只恢复流程和参数，
保留结果、附带输入及其清单。恢复前也会备份当前记录，所以连续恢复两次会回到首次恢复前的状态，确认框会明确提示。
正在运行的目录禁止更新、恢复和清空。

### Clear a working directory / 清空工作目录

*Program output → Working directory → Clear working directory… (程序输出 → 工作目录 → 清空工作目录…)* lists items to
delete (left, uncheck to retain), retained items (right), and the selected count and total size. Sizes use B / KB /
MB / GB with one decimal place. *All / None (全选 / 全不选)* above the list changes only deletable items. Results-only
mode is the default: preserve the session, its backups, `.grasp_gui_inputs.json`, and the attached inputs named in
that list; run-state/log files and `.grasp_gui/` caches can be removed. Import writes the input list as a JSON array
of validated basenames, including an empty array when there are no attachments. Updates add new attached names to
the existing list without dropping earlier inputs. The list survives restarting the GUI. For an old directory
without a list, ordinary items remain unchecked until explicitly selected. A malformed list is reported and
results-only cleanup is refused. Shared configuration and session formats are unchanged.
*Delete everything (全部删除)* includes inputs and the record, and requires typing the directory name. Deletion is
permanent, including selected subdirectories; symbolic links are only unlinked. Root, home and system directories
are refused; a directory without a session requires a separate confirmation. Clearing everything also clears the
visible pipeline, and closing the window does not recreate the removed record.

“清空工作目录…”先列出删除与保留项、总数量和大小（B / KB / MB / GB，保留一位小数），可取消勾选或用“全选 / 全不选”
批量选择可删除项；默认只删结果，保留工作目录记录、备份、输入清单及清单中的附带输入，运行状态、日志和缓存可以清理。
导入时把经校验的附带输入基名写入 `.grasp_gui_inputs.json`（无附带输入时为空数组）；更新时合并新旧输入名，恢复记录不移除输入，
重启后仍可自动选中清单之外的结果。没有清单的旧目录不猜测输入，普通项目默认不勾选；损坏的清单会报错并拒绝只删结果。
“全部删除”包括输入、清单和记录，必须输入目录名确认；删除不能恢复，选中的子目录会递归删除，
符号链接只删除链接本身。根目录、家目录、系统目录禁止清空；没有工作目录记录的目录另行确认。
全部删除后界面流程也清空，关闭窗口不会重新生成记录。

### Export configuration (导出配置)

Dialog: explanation (parameters of every step, no results, no machine settings), *Title* (required), *Summary*,
*Author* (remembered), *Estimated time*; *Reference results*: checklist of the `.cm` files of the directory (their
level energies are stored); *Attached files*: small text input files the GUI cannot generate (*Add…*, *Remove*;
≤ 1 MB each, ≤ 4 MB total); *Notes (提示)*: portability problems (steps stored as commands, absolute paths, custom
commands) or *No portability problems found*. *Export…* asks for the file name (default: the title). Format:
`docs/CONFIG_FORMAT.md`.

*Save to file… (保存为文件…)* and *Copy to clipboard (复制到剪贴板)* use identical JSON, including the selected
attachments and reference results. The pipeline's *Export ▾* menu also has *Copy configuration to clipboard…
(复制配置到剪贴板…)*, which opens the same dialog with copy as the default action. Successful copying is shown
in the status bar. An oversized copy keeps the previous clipboard and asks the user to save a file instead.
Clipboard output is limited to 4 MiB; file output and both input routes allow 32 MiB.

中文：保存和复制使用同一份配置与排版，包含所选附带文件和参考结果；流程的“导出 ▾”也可打开复制入口。
复制成功会在状态栏提示；太大时不改动原剪贴板，可继续选择保存为文件。

## Radial wave functions tab / 径向波函数页

* **Left**: *Refresh (刷新)*; *Radial wave function files (check those to compare) (径向波函数文件 (勾选要比较的文件))*
  — every file of the directory recognised by content (`.w`, `.bw`, `rwfn.inp`, `rwfn.out`, renamed files);
  *Add other file… (添加其他文件…)*; *Orbitals (轨道)* checklist (1s, 2s, 2p-, 2p, …) with *All / None (全选 / 全不选)*.
* **Toolbar**: checkboxes *P(r)*, *Q(r)*, *P² + Q²* (one plot each), *x axis (横轴)*: r, log r or √r (a.u.);
  *Reset zoom (复位缩放)*, *Export image… (导出图片…)* (PNG), *Export data… (导出数据…)* (CSV), *Tips (操作提示)*.
* **Plots**: each orbital one colour, each file one line style (solid, dashed, …); legend with *… n more*.
  Wheel zooms the x axis, Shift + wheel the y axis, drag pans, double-click resets; hovering shows values.
* **Table**: *File*, *Orbital*, κ, −ε (a.u.) (GRASP stores bound orbital energies as positive), ⟨r⟩ (a.u.),
  *r of peak (峰值位置)*, *Nodes / expected (节点 / 应有)* (nodes of P(r) vs. n − l − 1), *Norm (归一化)*,
  *Grid points (网格点数)*; orbital names in their plot colour.

## Levels tab / 能级图页

**Results summary… (结果摘要…)** in the top bar opens a preview of a results summary for an agent: levels with
leading components, energy changes between layers, rmcdhf SCF convergence (from logs, plus the final
self-consistency and generalised occupation of each orbital from `name.sum`, with the orbitals new in the layer
marked), CSF counts and the run status of the steps. *Include the complete configuration (附带完整配置)* is an optional extra, off by default and
unavailable when the directory has no pipeline record. 1–200 levels per symmetry block (default 5); reading runs
in the background. The Markdown/JSON text is capped at 200 KiB; leading components and earlier results are
omitted first, with an explicit list. *Copy to clipboard (复制到剪贴板)* or *Save to file… (保存为文件…)*
(`<directory>_summary.md`); cancel leaves the clipboard unchanged. For the directory open on the Calculation tab
the summary uses its current pipeline and this session's run records; otherwise the directory record.
Exporting a configuration alone stays under *Calculation → Export*.

顶栏的“结果摘要…”：给 agent 的结果摘要，包括能级与主要成分、能级随层的变化、rmcdhf 收敛情况、CSF 数和各步运行状态。
rmcdhf 部分另含 `name.sum` 中每个轨道最终的自洽程度和广义占据数，并标出本层新增的轨道。“附带完整配置”是可选的附加部分，默认不选；目录没有流程记录时不可选。每块默认 5 个能级 (1–200)，后台读取，文本最多
200 KiB (先省主要成分、再省较早的结果，并写明)。可以复制到剪贴板或保存为文件 (`<目录名>_summary.md`)。单独导出配置仍在计算页的“导出”。

Large mixing and CSF files are indexed in the background. The status bar shows *Reading … (正在读取 …)*
and progress; changing the directory or file selection cancels obsolete work. Tabs and controls remain usable.
The composition pane briefly shows *Reading composition… (正在读取组成…)* when the selected level's CSFs are read.
Only the displayed components are parsed; changing the weight threshold reuses the same data. Small mixing files
are analyzed serially to avoid process startup delays; larger files can use up to four local analysis processes.

Indexes and leading-component summaries are cached in `<workdir>/.grasp_gui/index/`. For a read-only directory,
the cache falls back to `~/.cache/grasp_gui/index/<directory hash>/` (`$XDG_CACHE_HOME` if set). If neither location
is writable, viewing still works without a disk cache. A changed source invalidates its cache; damaged caches
are rebuilt, and entries for deleted sources are removed on the next directory scan. These caches contain no
copies of the full calculation files and can be deleted to reclaim space.

中文：大型混合系数和 CSF 文件在后台建立索引，状态栏显示读取进度；切换文件或目录会取消旧请求，界面保持响应。
选中能级时只解析所需的 CSF，阈值改变时复用已读数据。小混合文件串行分析，避免启动进程池的延迟；大文件至多用四个本地分析进程。
索引和主要成分摘要保存在工作目录的 `.grasp_gui/index/`；
只读目录改用用户的 `~/.cache/grasp_gui/index/<目录哈希>/`，两处都不可写时仍可查看，但不保留磁盘缓存。
源文件变化时缓存失效，损坏时重建，源文件删除后在下次扫描清理；缓存不含完整结果文件的副本，可直接删除。

```
Refresh  Energy unit [eV▾]  Export▾  ☐ Show all computed transitions of the selected level        Tips
┌ Mixing coefficient files ? ┐┌ level diagram          ┐┌ odd4.cm J^π = 1⁻ #2 43.79 eV (… a.u.)      ┐
│ ☑ even4.cm                 ││ columns per J^π, file  ││ Show weights ≥ [0.100 %]                   │
│ ☐ even4.m                  ││ headers, arrows        ││ ┌ jj-coupled CSFs ┬ LSJ composition ┬ Transitions ┐
│ ☑ odd4.cm                  │├ level list (table) ────┤│ │ table / transition panel                │
└────────────────────────────┘└────────────────────────┘└────────────────────────────────────────────┘
```

* **Files**: *Mixing coefficient files (混合系数文件)* checklist (`.m`, `.cm`, by content), *?* explains how the
  matching `.c` (configurations) and `.lsj.lbl` (LSJ composition) files are found by content: number of orbitals,
  CSFs and J^π of each block, level energies — they need not share the mixing file's name.
* **Toolbar**: *Refresh*, *Energy unit (能量单位)* eV / cm⁻¹ / a.u. (relative to the lowest level; remembered),
  *Export ▾ (导出)*: *Level diagram (PNG / SVG / PDF)… (能级图)*, *Level data (CSV)… (能级数据)*, *Configuration
  abbreviations… (组态缩写…)*; *Show all computed transitions of the selected level (显示选中能级的全部已算跃迁)*
  (draws them as thin links); *Tips*.
* **Diagram**: one column per J^π, grouped by file (file name above, J^π labels such as 0⁺ 1⁻); odd parity red, even
  blue; axis *Relative energy (eV) (相对能量)*. Hover a level: tooltip with details. Click: select (orange) and show its
  composition. The wheel (or two-finger scrolling on a touchpad) and dragging empty space scroll the energy axis up
  and down; **pinch** on a touchpad (or Ctrl + wheel) zooms around the pointer; double-click resets. Pinch arrives as
  a gesture on macOS and Wayland and as Ctrl + wheel on Windows. **Box zoom**: drag a box
  with the right button (or Ctrl + left button) to zoom to its energy range; a right-click without dragging or
  Backspace returns to the previous view (repeatedly), Esc cancels a box being drawn.
  **Drag from one level to another** (or Shift-click a second level) selects a pair and opens the *Transitions* tab.
  Computed pairs are drawn as arrows from the upper to the lower level, labelled with the wavelength (e.g.
  `283.13 Å`); right-click an arrow to remove it. Arrows are saved in `<workdir>/.grasp_gui/marked.json`.
* **Level list (below the diagram)**: *File*, J^π, *Pos (序号)*, *Energy (eV)*, *Total energy (a.u.)*, *Purity (纯度)*,
  *Main composition (主要组成)*; sortable; clicking a row selects the level.
* **Composition (right)**: header `file J^π #pos energy (total a.u.)`; *Show weights ≥ (只显示权重 ≥)* threshold;
  tabs:
  * *jj-coupled CSFs (jj 耦合 CSF)*: #, CSF, *Coefficient (系数)*, *Weight (权重)* (with a bar), *Configuration (组态)*,
    *Coupling (intermediate J, total J^π)*; footer *Showing n / m CSFs, total weight …, configurations from X.c*.
  * *LSJ composition (LSJ 组成)*: coefficient, weight, LSJ label (from `jj2lsj`'s `.lsj.lbl`).
  * *Transitions (跃迁)*: see below.

### Transitions on demand / 计算跃迁

For the selected pair: header `upper → lower`, ΔE and λ; a plan (whether `rbiotransform` is needed — skipped when both
levels come from the same file — and which multipoles: E1, or M2 when E1 is forbidden; M1 and E2 between levels of the
same parity; J = 0 → 0 is refused). Buttons, in two rows: *Compute (selected J) (计算跃迁 (所选 J))* — only the J^π symmetries of
the two levels (the GUI writes `.c`/`.cm` files with just those blocks), with the main window's process count;
*Compute all J (MPI) (计算全部 J (MPI))* — all transitions between the two files (lifetimes, branching ratios) with
the MPI programs and *Processes (进程数)* next to it (first use: the same physical-core/MPI availability rule as the main window,
saved independently as `tr_np`; existing values are retained, and its maximum remains four times the logical
core count); *Stop*; *Export ▾*: transitions of the current level (CSV),
all computed transitions between the two files (CSV), *GRASP transition file (for rtabtrans1 / rtabtrans2)*
(`even4.odd4.ct`).

Results: table *Type*, *Gauge (规范)* — Babushkin (length) and Coulomb (velocity), A (s⁻¹), gf, S (a.u.); their
ratio close to 1 indicates a reliable calculation. *Transitions drawn in the diagram (图上标注的跃迁)* list with
*Remove / Clear all*. *Computed transitions of level … (能级 … 的已算跃迁)*: ↓ decay to a lower level, ↑ excitation
from a lower level, total decay rate and lifetime (computed transitions only); table *Other level*, ↓/↑, *Type*,
λ (Å), A (s⁻¹), gf, *Branching ratio (分支比)*. At the bottom the output of the GRASP programs. The page scrolls
when the right column is small, so it does not set the minimum size of the window.

All levels within the computed symmetries are done at once, so other pairs there show immediately. Results are
cached in `<workdir>/.grasp_gui/tr/<hash>/` and reused until an input file changes. Needs `isodata` and the matching
`.w` files (named like the mixing file, or the only candidate; never guessed when several fit). GRASP's MPI programs
cannot handle paths longer than 128 characters; the GUI then falls back to the serial versions.

### Export / 导出

* **Level diagram** dialog (*Export level diagram (导出能级图)*): checkboxes *label configuration and term (or J^π)*,
  *label energies*, *draw the marked transitions*; *Text on the arrows (箭头上的文字)*: none / wavelength (nm) /
  wavelength (Å); *Configuration (组态)*: *Configuration abbreviations…*; *Axis text (坐标轴文字)*: English / 中文;
  *Height (图的高度)* 300–6000 px; *PNG resolution (PNG 分辨率)* ×1–×6. The range is the one on screen (zoom first);
  the width follows from the labels. PDF embeds the fonts (for papers); SVG can be edited in Inkscape. Options
  remembered. The format follows the file name (PNG / SVG / PDF).
* **Configuration abbreviations (组态缩写)**: table *Abbreviation (缩写)* / *Full configuration (完整组态)*
  (default noble-gas cores [He] … [Rn]); when a configuration contains all subshells of a row with the same
  occupations they are replaced by the abbreviation, with a live preview. Core orbitals of the CSF file count.
* **CSV**: UTF-8, NIST-style configurations (`[Ne]3s.3p`) and terms (`1P*`).

### Upload local pipeline / 上传本机流程

The pipeline run-button group also contains **Upload to server… (上传到服务器…)** and **Upload and run…
(上传并运行…)**. Unlinked directories select a host and a new/empty target using the connection browser plus
an editable target field. Linked copies reconnect to their saved host/directory. The upload dialog reuses the
rsync file table, threshold and progress. Small inputs default on; generated outputs, large files, internal
records and server-existing names default off/excluded. Existing server files are retained in this workflow.

The dialog previews server changes/input checks before its default-No confirmation; custom commands have a
separate default-No confirmation. It saves the record before transferring files. Failed transfer keeps the record
and association and offers retry; the next attempt checks the current server revision again. Upload and run
opens the remote window and invokes its ordinary run-confirmation flow after the pipeline loads, once only.
Conflict offers **Fetch server pipeline again**; users can export local edits first. **Unlink local copy…**
asks default-No, changes only desktop preferences, and never deletes server data.

本机计算页流程运行按钮旁增加“上传到服务器…”和“上传并运行…”。新目录先选主机和新建/空目标；关联副本沿用主机与路径。
确认前显示服务器预览、选中的输入、待上传/缺失与冲突文件；默认否，自定义命令单独确认。先写记录再传输入，同名服务器文件保留。
失败后提示补传，不删记录。丢失回答先核对：配置一致则关联；旧修订号未变或新建目标经检查仍为空时，提示“写入未执行，
可以再次确认上传”，保留本机修改，等待用户重新确认。其他变化仍提示冲突；绝不自动重发写入。
副本常驻来源标识，本机缺少输入标明“由服务器检查”；本机直接运行另有默认否确认。

## Saved state / 保存的内容

| Where | What |
|---|---|
| `~/.config/grasp_gui.json` | language, font size, last working directory, recent directories, GRASP bin, MPI settings, settings collapsed, window geometry, calculation splitter, last tab, Slurm settings, energy unit, level-export options, configuration abbreviations, transition process count, show-links option, configuration author |
| `<workdir>/.grasp_gui.json` | session: parameters per group, current group, pipeline |
| `<workdir>/.grasp_gui.json.bak-<timestamp>` | original session records before updating/restoring; latest five retained / 更新及恢复前的记录备份，保留五份 |
| `<workdir>/.grasp_gui_inputs.json` | validated attached-input basenames for cleanup after restarting / 附带输入基名清单，重启后清理仍会保留这些输入 |
| `<workdir>/.grasp_gui_run.json`, `.grasp_gui_run.log` | state and output of the last run (background runs) |
| `<workdir>/.grasp_gui_slurm.json` | Slurm submission identity, job ID and returned cluster; restored across desktop connections |
| `<workdir>/.grasp_gui_slurm_<token>.sh`, `.log` | preserved batch script and its Slurm output |
| `<workdir>/.grasp_gui_slurm_<token>.done` | atomic token/job ID/exit-status marker; optional for older version-1 submissions |
| `<workdir>/.grasp_gui_slurm.json.cleared.<id>` | archived original bytes after confirmed queue/revision checks |
| `~/.config/grasp_gui.json` → `remote_copies` | local path → host, remote directory, revision; pending write fingerprint for reconnect verification |
| `<workdir>/.grasp_gui/tr/` | cached transition calculations |
| `<workdir>/.grasp_gui/marked.json` | transitions drawn as arrows |

## Legacy build / 旧系统版

Same interface on Qt 5 (branch `legacy`, `legacy/README.md`). Differences: on Wayland it runs through XWayland;
without a display it prints a bilingual hint (`ssh -X`) instead of opening a window.
