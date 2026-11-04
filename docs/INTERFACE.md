# Interface reference / 界面说明

A detailed description of the GUI: what each window, tab, menu and control does. The README only lists the
features; this file is the reference for users who want the details and for development (to know the interface
without starting it). Labels are given as *English (中文)*. Screenshots: `docs/images/` (made by
`tools/make_screenshots.py`). Keep this file up to date when the interface changes.

本文件详细说明界面的各个窗口、标签页、菜单和控件，供需要细节的用户和开发时参考 (不必启动程序即可了解界面)。
控件名写作“英文 (中文)”。界面改动时请同步更新本文件。

## Main window / 主窗口

```
┌ Menu: File  View  Window  Language ──────────────────────────────────────────┐
│ Working directory [__________________________] Browse… Recent▾ ▸Settings     │
│ GRASP bin: /opt/grasp/bin · serial          (summary; ▾Settings expands it)  │
│ ┌ Calculation ┬ Radial wave functions ┬ Levels ┐          Open in new window │
│ │  tab contents                                                              │
│ └───────────────────────────────────────────────────────────────────────────┘│
│ status bar                                                                   │
└──────────────────────────────────────────────────────────────────────────────┘
```

* **Working directory (工作目录)**: edit + *Browse… (浏览…)* + *Recent (最近)* menu. Changing it saves the session
  of the old directory, loads the new one's, and refreshes the viewers. On start the last directory is opened.
* **Settings (设置)** toggle: collapsed it shows a one-line summary (`GRASP bin: … · serial` / `MPI n processes`);
  expanded: *GRASP bin directory (GRASP bin 目录)* + *Browse…*, *MPI processes (MPI 进程数)*, *mpirun command
  (mpirun 命令)*, *MPI_TMP*, *Lower priority of calculations (nice) (降低计算进程优先级)*. With more than one process
  `rangular`, `rmcdhf`, `rci`, `rbiotransform`, `rtransition` run as `mpirun -np N <prog>_mpi`. The collapsed state
  is remembered.
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
| *File (文件)* | *Import example… (导入示例…)*, *Import configuration file… (导入配置文件…)*, *Export configuration… (导出配置…)* — *Save parameters… (保存参数…)*, *Load parameters… (载入参数…)* — *Export pipeline as shell script… (导出流程为 shell 脚本…)*, *Export Slurm job script… (导出 Slurm 作业脚本…)* — *Recent working directories (最近的工作目录)* — *Quit (退出)* |
| *View (视图)* | *Larger font (放大字体)* Ctrl++ / Ctrl+=, *Smaller font (缩小字体)* Ctrl+−, *Default font size (默认字体大小)* Ctrl+0 — the three tabs, Ctrl+1/2/3. The font size is remembered; changing it rebuilds the interface (parameters, pipeline and output are kept). |
| *Window (窗口)* | *Open Radial wave functions / Levels in new window (在新窗口中打开…)* |
| *Language / 语言* | 中文, English (rebuilds the interface; remembered) |

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
┌ Nucleus & CSFs ┬ SCF & CI ┬ Properties & transitions ┬ Results & tools ┐  Package [GRASP2018▾]  State group [odd][even] Copy to even
│ rangular·odd ┬ rwfnestimate·odd ┬ rmcdhf·odd ┬ rsave·odd ┬ rci·odd       │
│  description of the program                                             │
│  form fields (label, editor, hint)                                      │
├ Preview input · ▶ Run this program · Add to pipeline ↓ ········ ■ Stop ─┤
│ ┌ Pipeline ┬ Input preview ┐ │ Program output (last 5000 lines) List files Clear │
│ │ 1. rnucleus   ▶Run all ▶Run from selected │  dark terminal-style output         │
│ │ 2. …          Move up / Move down / Show / Remove / Clear / Export▾             │
└─────────────────────────────────────────────────────────────────────────┘
```

* **Program tabs**, two levels (top: group, bottom: program):
  * *Nucleus & CSFs (核与组态)*: `rnucleus`, `rcsfgenerate`, `rcsfinteract`, `rcsfsplit`, `rcsfzerofirst`
  * *SCF & CI (自洽场与 CI)*: `rangular`, `rwfnestimate`, `rmcdhf`, `rsave`, `rci`
  * *Properties & transitions (性质与跃迁)*: `rhfs` (optionally followed by `rhfs_lsj`), `ris4`, `rbiotransform`,
    `rtransition`
  * *Results & tools (结果与工具)*: `jj2lsj`, `rlevels`, `rtablevels`, `rtabtrans1`, `rtabtrans2`, *Custom (自定义)*
    (any program with hand-written input)
* **Package (程序包)** combo box at the top right: *GRASP2018* (default) or *GRASPG* (V3). It selects which program
  tabs are shown and is saved per working directory; importing a GRASPG configuration switches to it. With GRASPG
  the tabs are:
  * *Nucleus & CSFs*: `rnucleus`, `rcsfggenerate_csfg`, `rcsfginteract_csfg`, `rcsfgsplit_csfg`, `rcsfgzerofirst_csfg`
  * *SCF & CI*: `rangular_csfg`, `rwfnestimate_csfg`, `rmcdhf_csfg`, `rsave_csfg`, `rci_csfg`, `rci_block_csfg`
    (block-by-block CI of one block file such as `oddCI6_odd1`: *Mode* one step / only build and save the H matrix /
    restart from the saved H matrix; *First copy this wave function file to name.w*; the `rci_csfg` options;
    *Lower the eigenvalue accuracy* (restart only); before a restart the GUI checks that
    `MPI_TMP/<rank>/rci<rank>.res` exist for exactly the current number of MPI processes)
  * *Properties & transitions*: as GRASP2018 (they read the expanded `name.c` written by `rsave_csfg` / `rci_csfg`)
  * *Results & tools*: `jj2lsj_csfg`, `rlevels_csfg`, `rtablevels`, `rtabtrans1`, `rtabtrans2`, *Custom*
  * *CSFG tools (CSFG 工具)*, a fifth group only for GRASPG: `rcsfgexpand_csfg` (CSFG list → CSF list `rcsf.out`,
    *Save rcsf.out as*), `rcsfgextend_csfg` (larger orbital sets, lines `orbital set  label` like the split),
    `rcsfgblocksplit_csfg` (one file per J/parity block, answers the "same orbital set" question with y),
    `rmixaccumulate_csfg` (condense by accumulated weight; optional new orbital set → `rcsfg.nmax`; *Save the result
    as* writes `name.g` and copies `state.l`), `rdistHmatrix_csfg` (redistribute a saved H matrix to another number
    of processes: renames the MPI temporary directory to the source directory, creates it again and fills it)

  GRASPG forms work on CSFG lists `name.g` + labelling-orbital files `name.l` (names given without extension; the
  GUI copies them to `rcsfg.inp` / `rlabel.inp`, or `rcsfgmr.inp` / `rcsfgmr.l` for the multireference of
  `rcsfginteract_csfg`). `rcsfggenerate_csfg` is the `rcsfgenerate` form without the ordering question, with a note
  that the lists with 0 excitations must come first (they define the labelling orbitals); *Save as* writes `name.g`
  and `name.l`. `rcsfgsplit_csfg` has *Copy this labelling-orbital file to rlabel.inp*. `rci_csfg` adds *Limit the
  Breit interaction by n / l* (with the n / l values, always sent), *Largest n of the spectroscopic orbitals
  (NQEDMAX)* (always asked) and *Memory limit per process (GB)*, and copies `.g/.l/.w`. `rangular_csfg`,
  `rmcdhf_csfg`, `rci_csfg` exist only as MPI programs (`*_csfg_mpi`): with 1 MPI process they are started directly
  (MPI_TMP is still set), otherwise with the MPI command. Before `rmcdhf_csfg` the GUI checks the angular files
  `MPI_TMP/<rank>/csfg_mcp<rank>.*` (same process count, not older than `rcsfg.inp`). GRASPG programs exit with code 0
  on errors; the GUI recognises their error messages (`Stop Error`, `Error!`, `… is not found`, `not exists`,
  `Problems with opening`, `STOP …error…`) and stops the pipeline.
  Choosing GRASPG when the bin directory has no `rcsfggenerate_csfg` shows a warning in the status bar.
  `rcsfgzerofirst_csfg` takes the full `.g` names of the zero-order list and the list to partition; both need
  same-named `.l` files with identical contents. *Save the result rcsfg.out as (结果 rcsfg.out 保存为)* takes
  a `.g` file name and also saves the matching `.l` and `.icut`; using the same name as the list to partition
  overwrites that list (blank leaves the outputs as `rcsfg.out` and `icut`). It supports odd/even.
  Then enable *Zero/first-order partition (零阶 / 一阶划分)* in `rangular_csfg` / `rci_csfg` and supply the same zero-order `.g`.
  ICCUT counts CSFG records in that list at run time, not expanded CSFs. Each block must contain at least one
  zero-order CSFG; block symmetries must match, and counts cannot exceed the full list. The GUI and exported
  shell scripts check this before starting the angular/CI program. Both diagonal-block questions are answered
  `n`, as in the author's `script3_ZF`. The option defaults to off; `rci_block_csfg` has no partition option.

* **Forms**: one per program, generated from the field definitions in `grasp_core.py` (description line, fields
  with hints at the right). Inputs follow each program's default-settings question order; they reproduce the inputs
  of the manual's test cases (`grasptest/` case1–4, example1–5, 8, 9) except non-default `jj2lsj` runs (use
  *Custom*). `rcsfgenerate` has several CSF lists (*+ Add list*, *− Delete current list*, *Reference configurations
  (one per line)*). `rmcdhf` can use the in-memory version `rmcdhf_mem` / `rmcdhf_mem_mpi`.
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
  (从选中开始)*, *Move up / Move down (上移 / 下移)*, *Show (查看)* (input preview of the step), *Remove (删除)*,
  *Clear (清空)*, *Export ▾ (导出)*: shell script / Slurm job script.
* **Input preview (输入预览)**: the command, the here-document with the program's stdin and the file actions, e.g.
  `rmcdhf <<'EOF' … EOF` followed by `rsave odd4`.
* **Program output (程序输出)**: dark terminal-style pane, last 5000 lines (set a log file in the form for the full
  output); *List files (查看文件)*, *Clear (清屏)*. Errors in red, step headers in blue.
* **Background runs (后台运行)**: every run (a program or the pipeline) is executed by a separate background process
  (`grasp_gui _run`); the tab shows its output live and *■ Stop* stops it. The status bar shows *Running: step i/n
  title (运行中：第 i/n 步 …)*.
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
example1–5, 8, 9, case1, case4; GRASPG: graspg1 = `graspgtest/script1/csfg`, case 1 with CSFGs; graspg_zf = C III zero/first-order partition through n = 4, both package GRASPG) or, with *Open configuration file… (打开配置文件…)*, a file; right an information
pane: title, origin (*Example included with the program*), summary, *Estimated time (预计时间)*, *Package (程序包)*,
*Pipeline (流程)* (n steps), *Programs used (用到的程序)* (missing ones in the GRASP bin directory marked), attached
files, compatibility warnings, **custom commands** (orange, on a light background: *Custom* tab steps and steps
stored as commands — they can run any program), *Reference results (参考结果)* (energies of the mixing coefficient
files). Bottom: *Import into directory (导入到目录)* + *Browse…* (default `~/grasp_demos/<id>` for examples,
`~/grasp_calcs/<name>` for files; must be empty or new), *Import (导入)*, *Cancel*. Importing a file with custom
commands asks for confirmation. After import the GUI switches to that directory; *▶ Run all* computes it.

示例列表还包括 GRASPG 的 `graspg_zf`：C III 零阶 / 一阶划分，到 n = 4，24 个能级；使用 2 个 MPI 进程约 10 秒。

### Export configuration (导出配置)

Dialog: explanation (parameters of every step, no results, no machine settings), *Title* (required), *Summary*,
*Author* (remembered), *Estimated time*; *Reference results*: checklist of the `.cm` files of the directory (their
level energies are stored); *Attached files*: small text input files the GUI cannot generate (*Add…*, *Remove*;
≤ 1 MB each, ≤ 4 MB total); *Notes (提示)*: portability problems (steps stored as commands, absolute paths, custom
commands) or *No portability problems found*. *Export…* asks for the file name (default: the title). Format:
`docs/CONFIG_FORMAT.md`.

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
  CSFs and J^π of each block, level energies — they need not share the mixing file's name. A file that is still
  being written block by block (GRASPG `rci_block_csfg`) shows its complete blocks; its tooltip says *The file is not
  complete yet; only the finished blocks are shown*. Unreadable files are red with the error as tooltip.
* **Toolbar**: *Refresh*, *Energy unit (能量单位)* eV / cm⁻¹ / a.u. (relative to the lowest level; remembered),
  *Export ▾ (导出)*: *Level diagram (PNG / SVG / PDF)… (能级图)*, *Level data (CSV)… (能级数据)*, *Configuration
  abbreviations… (组态缩写…)*; *Show all computed transitions of the selected level (显示选中能级的全部已算跃迁)*
  (draws them as thin links); *Tips*.
* **Diagram**: one column per J^π, grouped by file (file name above, J^π labels such as 0⁺ 1⁻); odd parity red, even
  blue; axis *Relative energy (eV) (相对能量)*. Hover a level: tooltip with details. Click: select (orange) and show its
  composition. Wheel zooms around the mouse, drag on empty space pans, double-click resets.
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
same parity; J = 0 → 0 is refused). Buttons: *Compute (selected J) (计算跃迁 (所选 J))* — only the J^π symmetries of
the two levels (the GUI writes `.c`/`.cm` files with just those blocks), with the main window's process count;
*Compute all J (MPI) (计算全部 J (MPI))* — all transitions between the two files (lifetimes, branching ratios) with
the MPI programs and *Processes (进程数)* next to it; *Stop*; *Export ▾*: transitions of the current level (CSV),
all computed transitions between the two files (CSV), *GRASP transition file (for rtabtrans1 / rtabtrans2)*
(`even4.odd4.ct`).

Results: table *Type*, *Gauge (规范)* — Babushkin (length) and Coulomb (velocity), A (s⁻¹), gf, S (a.u.); their
ratio close to 1 indicates a reliable calculation. *Transitions drawn in the diagram (图上标注的跃迁)* list with
*Remove / Clear all*. *Computed transitions of level … (能级 … 的已算跃迁)*: ↓ decay to a lower level, ↑ excitation
from a lower level, total decay rate and lifetime (computed transitions only); table *Other level*, ↓/↑, *Type*,
λ (Å), A (s⁻¹), gf, *Branching ratio (分支比)*. At the bottom the output of the GRASP programs.

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

## Saved state / 保存的内容

| Where | What |
|---|---|
| `~/.config/grasp_gui.json` | language, font size, last working directory, recent directories, GRASP bin, MPI settings, settings collapsed, window geometry, calculation splitter, last tab, Slurm settings, energy unit, level-export options, configuration abbreviations, transition process count, show-links option, configuration author |
| `<workdir>/.grasp_gui.json` | session: parameters per group, current group, pipeline, package |
| `<workdir>/.grasp_gui_run.json`, `.grasp_gui_run.log` | state and output of the last run (background runs) |
| `<workdir>/.grasp_gui/tr/` | cached transition calculations |
| `<workdir>/.grasp_gui/marked.json` | transitions drawn as arrows |

## Legacy build / 旧系统版

Same interface on Qt 5 (branch `legacy`, `legacy/README.md`). Differences: on Wayland it runs through XWayland;
without a display it prints a bilingual hint (`ssh -X`) instead of opening a window.
