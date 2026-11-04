# GRASP GUI

A graphical interface for [GRASP2018](https://github.com/compas/grasp): fill in the parameters of each
program, and the GUI generates the interactive input (stdin) and runs the program in a working directory.
The interface is available in Chinese and English.

GRASP2018 的图形界面：在表单中填写各程序的参数，界面自动生成交互输入 (stdin)，并在工作目录中运行。
界面支持中文和 English。

**Download / 下载**: standalone Linux executables (no Python needed) on the
[Releases page](https://github.com/skq1998/grasp_gui/releases/latest): `grasp_gui-linux-x86_64` for desktops
(glibc ≥ 2.35), `grasp_gui-legacy-linux-x86_64` for old servers and `ssh -X` (glibc ≥ 2.17).
在 [Releases 页面](https://github.com/skq1998/grasp_gui/releases/latest) 下载独立可执行文件 (不需要 Python)：
桌面电脑用 `grasp_gui-linux-x86_64` (glibc ≥ 2.35)，旧服务器和 `ssh -X` 用 `grasp_gui-legacy-linux-x86_64` (glibc ≥ 2.17)。

## Screenshots / 截图

The screenshots show example 4 of the GRASP2018 manual (Fe XV), imported with *File → Import example…* and run
with *▶ Run all*. / 截图为 GRASP2018 手册的算例 4 (Fe XV)：用“文件 → 导入示例…”导入，点“▶ 运行全部”计算。

**Calculation** — one form per program, odd / even state groups, the pipeline and the generated input (here
`rmcdhf`, followed by `rsave`):

![Calculation tab](docs/images/en_calculation.png)

**Levels** — drag from one level to another to compute the transition (`rbiotransform` and `rtransition` run
automatically); the result is drawn as an arrow with its wavelength, with A, gf, S, branching ratio and lifetime:

![Levels tab with a computed E1 transition](docs/images/en_levels_transition.png)

**Radial wave functions** — P(r), Q(r) and P² + Q² of several `.w` files overlaid, with orbital properties:

![Radial wave functions tab](docs/images/en_wavefunctions.png)

**Examples and shared configurations** — what a configuration does, the programs it needs, every custom command
(highlighted) and reference energies, before importing:

![Import example dialog](docs/images/en_examples.png)

<details>
<summary>中文界面 (Chinese interface)</summary>

**计算**：每个程序一个参数页，odd / even 态组，计算流程和自动生成的输入 (这里是 `rmcdhf`，之后运行 `rsave`)：

![计算页](docs/images/zh_calculation.png)

**能级图**：从一个能级拖到另一个能级即可计算跃迁 (自动运行 `rbiotransform` 和 `rtransition`)，结果画成标有波长的箭头，
并列出 A、gf、S、分支比和寿命：

![能级图页与已算的 E1 跃迁](docs/images/zh_levels_transition.png)

**径向波函数**：叠加多个 `.w` 文件的 P(r)、Q(r) 和 P² + Q²，并列出轨道性质：

![径向波函数页](docs/images/zh_wavefunctions.png)

**示例与共享配置**：导入前显示配置的说明、所需的程序、全部自定义命令 (醒目标出) 和参考能量：

![导入示例对话框](docs/images/zh_examples.png)

</details>

## Features / 功能

- **Forms for every GRASP2018 program** (`rnucleus` … `rtabtrans2`, plus a *Custom* tab): the GUI writes the
  interactive input, runs the program in the working directory and shows its output. Odd / even state groups.
- **Pipelines**: chain the steps, run them all, export as a shell script or a Slurm job script; serial or MPI.
  Runs can continue in the background after the window is closed (or an ssh connection drops) and are reattached
  when the directory is opened again ([servers that end all processes at logout](docs/INTERFACE.md#background-runs-and-logging-out--后台运行与退出登录-killuserprocesses)).
- **Radial wave functions**: plot P(r), Q(r), P² + Q² of several `.w` files with orbital properties.
- **Levels**: energy level diagram, ASF compositions (jj and LSJ), transitions computed by dragging between two
  levels; export as PDF / SVG / PNG, CSV or a GRASP `.ct` file.
- **Examples and shared configurations**: the GRASP2018 manual's examples ready to run ([coverage](docs/MANUAL_EXAMPLES.md)); export a calculation to a
  small file that others can import and repeat ([format](docs/CONFIG_FORMAT.md); scripts and AI agents can write
  such files too: [rules](docs/AGENT_SPEC.md)).
- Chinese / English interface; parameters saved per working directory; a single executable, also for old servers
  over `ssh -X`.

- **各程序的参数表单** (`rnucleus` … `rtabtrans2`，以及“自定义”页)：界面生成交互输入，在工作目录中运行并显示输出；odd / even 态组。
- **计算流程**：串联各步、一键运行全部，可导出 shell 脚本或 Slurm 作业脚本；串行或 MPI。关闭窗口 (或 ssh 断开) 后计算可在后台继续，
  再次打开该目录即可接上 ([登录结束时结束全部进程的服务器](docs/INTERFACE.md#background-runs-and-logging-out--后台运行与退出登录-killuserprocesses))。
- **径向波函数**：叠加多个 `.w` 文件的 P(r)、Q(r)、P² + Q² 并列出轨道性质。
- **能级图**：能级图、原子态组成 (jj 与 LSJ)，在两个能级之间拖动即可计算跃迁；导出 PDF / SVG / PNG、CSV 或 GRASP `.ct` 文件。
- **示例与共享配置**：GRASP2018 手册算例可直接运行 ([覆盖清单](docs/MANUAL_EXAMPLES.md))；把计算方案导出为小文件，别人导入即可重复计算 ([格式](docs/CONFIG_FORMAT.md)；脚本和 AI agent 也可以生成：[规范](docs/AGENT_SPEC.md))。
- 中文 / English 界面；参数按工作目录自动保存；单个可执行文件，旧服务器也可通过 `ssh -X` 使用。

Every window, control and option is described in [docs/INTERFACE.md](docs/INTERFACE.md).
各窗口、控件和选项的详细说明见 [docs/INTERFACE.md](docs/INTERFACE.md)。

## Requirements / 依赖

- Python ≥ 3.9 and PySide6: `pip install -r requirements.txt`
- A compiled GRASP2018 (`bin/` with `rmcdhf`, …; the `*_mpi` programs and an MPI library for parallel runs)

## Usage / 用法

```bash
python3 grasp_gui.py                  # 中文界面，打开上次的工作目录
python3 grasp_gui.py path/to/workdir  # 打开指定的工作目录 / open this working directory
python3 grasp_gui.py --lang en        # English interface
python3 grasp_gui.py levels [dir]     # 直接打开能级图页 (默认为当前目录) / open on the Levels tab
python3 grasp_gui.py wfn [dir]        # 直接打开径向波函数页 / open on the Radial wave functions tab
python3 grasp_gui.py even4.cm         # 按文件内容打开对应的页，只勾选这个文件 / open the page for this file
python3 grasp_gui.py --help
```

A symbolic link named `grasp_levels` or `grasp_wfn` opens that tab directly, e.g. for the standalone executable:

以 `grasp_levels` 或 `grasp_wfn` 为名的符号链接直接打开对应的页，例如：

```bash
ln -s ~/bin/grasp_gui-linux-x86_64 ~/.local/bin/grasp_gui
ln -s ~/bin/grasp_gui-linux-x86_64 ~/.local/bin/grasp_levels
ln -s ~/bin/grasp_gui-linux-x86_64 ~/.local/bin/grasp_wfn
```

The language can also be switched in the *Language / 语言* menu; the choice is remembered in
`~/.config/grasp_gui.json`.

The GRASP `bin` directory is looked up in this order: `$GRASP_BINDIR`, the directory used last time,
the directory of `rmcdhf` on `PATH`, `../bin` relative to this program. It can be changed at the top of the
window.

GRASP `bin` 目录的查找顺序：`$GRASP_BINDIR` → 上次使用的目录 → `PATH` 中 `rmcdhf` 所在目录 →
本程序上一级目录的 `bin`。也可以在窗口顶部修改。

### Standalone executable / 独立可执行文件

```bash
./build.sh            # -> dist/grasp_gui   single file, runs without Python or PySide6
./build.sh --onedir   # -> dist/grasp_gui/  folder variant
```

`build.sh` creates its own build environment (`build/venv`) with PyInstaller and the essential PySide6
modules on first use. The executable (about 90 MB) needs a glibc at least as new as the build machine's
(e.g. built on Ubuntu 22.04: glibc ≥ 2.35); to use it on an older system, run `build.sh` there. GRASP
itself is not included: point the GUI at a GRASP `bin` directory as usual.

**Old servers, `ssh -X`:** each release also has `grasp_gui-legacy`, the same program built for glibc ≥ 2.17
(CentOS 7, Rocky 8, Ubuntu 18.04, Debian 9 and newer) with Qt 5. It needs nothing else on the server, so you can
start it where the (large) result files are and see the window on your own computer over X11 forwarding.
See [legacy/README.md](https://github.com/skq1998/grasp_gui/blob/legacy/legacy/README.md) (branch `legacy`).

Fonts are bundled, so the GUI also works on machines without (Chinese) fonts: Noto Sans CJK
(SIL Open Font License) and DejaVu Sans / DejaVu Sans Mono (free licence), with their licence files.
`build.sh` takes them from the build machine via fontconfig (Debian/Ubuntu:
`apt install fonts-noto-cjk fonts-dejavu-core`); missing fonts are reported and left out.

`build.sh` 首次运行时自动建立打包环境。生成的可执行文件约 90 MB，不需要 Python 或 PySide6；
要求系统的 glibc 不低于打包机器的版本（在 Ubuntu 22.04 上打包则需 glibc ≥ 2.35），
在更老的系统上使用时，在那台机器上重新运行 `build.sh` 即可。GRASP 本身不包含在内。
**旧服务器、`ssh -X`**：每个发布版还附带 `grasp_gui-legacy`，是为 glibc ≥ 2.17 (CentOS 7、Rocky 8、Ubuntu 18.04、
Debian 9 及更新的系统) 用 Qt 5 构建的同一程序，服务器上不需要安装其他东西：在存放 (很大的) 计算结果的服务器上运行，
通过 X11 转发在自己的电脑上显示窗口。见 `legacy` 分支的 [legacy/README.md](https://github.com/skq1998/grasp_gui/blob/legacy/legacy/README.md)。
可执行文件内置了字体 (Noto Sans CJK、DejaVu Sans / Sans Mono 及其许可证)，没有安装中文字体的机器也能正常显示；
打包时从本机取这些字体 (Ubuntu：`apt install fonts-noto-cjk fonts-dejavu-core`)。

### Submitting to a cluster / 提交到服务器

1. Build the pipeline locally and test it on a small case.
2. *File → Export Slurm job script…*, fill in the cluster settings.
3. Upload the script together with the input files of the working directory (`isodata`, `*.c`, `*.w`, …)
   to one directory on the server and run `sbatch <script>` there.

## Notes for WSL2 / WSLg

- Qt uses Wayland directly. If `$XDG_RUNTIME_DIR` lacks the WSLg socket (common when systemd is enabled),
  the GUI points `WAYLAND_DISPLAY` at `/mnt/wslg/runtime-dir/wayland-0` automatically. For the X11
  fallback, install `libxcb-cursor0`.
- hwloc's GL plugin makes `mpirun` hang under WSLg; MPI runs therefore set `HWLOC_COMPONENTS=-gl`
  unless it is already set.
- Qt's teardown can hang on Wayland, so the GUI exits directly after the window is closed. If a program is
  still running, it asks for confirmation and terminates the whole process group first.

## Files / 文件

| File | Content |
|---|---|
| `grasp_core.py` | Toolkit-independent core: field definitions and input rules of each program, running (serial / MPI), shell and Slurm export, translations |
| `grasp_gui.py` | Qt (PySide6) main window: working directory, run settings, the three tabs, menus, command line |
| `gui_common.py` | Widgets shared by the tabs: run settings, recent directories, base class of the viewer panels and their separate window |
| `calc_panel.py` | *Calculation* tab: forms generated from the field definitions in `grasp_core.py`, pipeline, runs and output |
| `grasp_wfn.py` | Reads GRASP radial wave function files (`.w`) and computes orbital properties (standard library only) |
| `wfn_panel.py` | *Radial wave functions* tab (plots drawn with QPainter) |
| `grasp_mix.py` | Reads mixing coefficient files (`.m`, `.cm`), CSF lists (`.c`) and `jj2lsj` labels (`.lsj.lbl`) (standard library only) |
| `level_panel.py` | *Levels* tab: energy level diagram, ASF composition and transitions |
| `grasp_trans.py` | Transitions between two levels: prepares and runs `rbiotransform` / `rtransition` in a private directory, reads the results (standard library only) |
| `grasp_config.py` | Shared configurations: format, conversion of older versions, checks, import and export (standard library only) |
| `demos/*.json` | The examples (shared configurations): parameters and pipeline of each manual example, reference energies |
| `tools/make_demos.py` | Generates `demos/*.json`; `--check` compares the generated input with the manual's input files |
| `tools/make_screenshots.py` | Makes the screenshots in `docs/images/` (runs example 4 through the GUI; needs a GRASP `bin` directory) |
| `build.sh` | Builds a standalone executable with PyInstaller |

## License / 许可证

[MIT](LICENSE)
