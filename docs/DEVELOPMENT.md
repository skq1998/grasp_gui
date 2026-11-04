# GRASP GUI 开发记录

GRASP2018 的图形界面：填写参数后自动生成各程序的交互输入并运行，也可以导出 shell / Slurm 脚本到服务器上运行；计算完成后可以查看波函数、能级和原子态组成。

- 仓库：https://github.com/skq1998/grasp_gui（MIT 许可证）
- 技术栈：Python 3 + PySide6 (Qt 6)；核心逻辑 (`grasp_core.py`、`grasp_wfn.py`、`grasp_mix.py`) 只用标准库
- 代码量：约 6800 行，9 个模块
- 发布形式：PyInstaller 打包的单文件 Linux 可执行文件（约 89 MB，内置字体，不需要 Python）

## 版本一览

| 版本 | 提交 | 主要内容 |
|---|---|---|
| v1.0.0 | `76e672f` | 全部常用程序的参数页、流程、MPI、Slurm 导出、odd/even 态组、工作目录记录、独立可执行文件 |
| v1.1.0 | `1ea52a2` | 布局调整：参数区扩大，GRASP 路径和 MPI 设置可收起 |
| v1.1.1 | `c595c15` | 内置字体；记住 GRASP bin 目录 |
| v1.2.0 | `b64e6de` | 波函数查看器、能级与组成查看器；按内容识别结果文件 |
| v1.2.1 | `76d582a` | 可调字号；打包程序使用自己的 fontconfig 配置 |
| v1.3.0 | `712c906` | 可导入的 GRASP2018 手册算例；修复只优化一个能级时 rmcdhf 的输入（V1 的最后一个版本） |
| v2.0.0 | 见标签 `v2.0.0` | 在能级图上计算跃迁、标注与导出；主窗口分为计算 / 径向波函数 / 能级图三页；直接打开结果的入口；Wayland 下清晰的窗口边框 |
| v2.1.0 | 见标签 `v2.1.0` | 共享配置 (导出 / 导入计算方案，手册算例采用同一格式)；旧系统版 `grasp_gui-legacy` (glibc ≥ 2.17、ssh -X，`legacy` 分支) |
| v2.2.0 | 见标签 `v2.2.0` | 零阶 / 一阶划分：`rcsfzerofirst` 程序页，`rangular`、`rci` 按零阶列表自动给出各块 ICCUT |
| v2.3.0 | 见标签 `v2.3.0` | 关闭窗口后计算在后台继续 (重新打开目录时接上)；供脚本和 AI agent 使用的配置规范与 `check` / `script` / `steps` / `reference` 命令 |
| v2.3.2 | 见标签 `v2.3.2` | 只装有 `jj2lsj_2025` (Makefile 编译的 GRASP、GRASPG) 时自动改用它；“停止”恰好在程序启动时到达也能结束程序 |
| v2.3.1 | 见标签 `v2.3.1` | 修正：很快结束的计算 (如单独运行 rnucleus) 不再报告“无法启动后台运行进程”；自动测试与 CI，参与开发的规则 (`AGENTS.md`) |

## 开发经过

### 1. 第一版界面（Tk → Qt）

- **目标**：在表单中填写参数，界面按 GRASP 各程序的提问顺序生成标准输入（stdin）并运行。每个程序的提问顺序都对照 Fortran 源码核对过（默认设置模式）。
- **第一版用 Tk 实现**，遇到两个问题：
  - Anaconda 自带的 Tk 没有 Xft 支持，中文显示为乱码（改用系统的 python3-tk 后解决）；
  - 切换程序页时要等很久才显示。
- **改用 Qt (PySide6) 重写**，同时做了几项性能优化：
  - 参数页全部预先创建，切换时只是显示和隐藏；
  - 程序输出分块读取，合并后每 100 ms 插入一次，并只保留最后 5000 行，所以 GRASP 输出很多时界面也不会卡；
  - 计算在子进程中运行，不阻塞界面。
- **其他基础功能**：
  - 中文 / English 界面切换；
  - 流程：把多个步骤串起来顺序运行；
  - 导出 shell 脚本，以及可以直接 `sbatch` 提交的 Slurm 作业脚本，用于把要跑一两天的计算放到服务器上。

### 2. 运行环境问题（WSL / MPI）

- **WSLg 下 Qt 连不上 Wayland**：`$XDG_RUNTIME_DIR` 中缺少 wayland 套接字，改为直接指向 `/mnt/wslg/runtime-dir/wayland-0`（`fix_wsl_display`）。
- **关闭窗口后终端不退出**：Qt 在 Wayland 下的退出清理会卡住。参数在操作时已经保存，所以 `app.exec()` 之后直接调用 `os._exit`。
- **MPI**：
  - 进程数大于 1 时，自动改用 `*_mpi` 版本，并设置 `MPI_TMP`；
  - mpirun 启动时卡住，原因是 hwloc 的 GL 插件会连接图形显示，用 `HWLOC_COMPONENTS=-gl` 解决；
  - mpirun 报 "exiting improperly"，原因是用串行 rangular 生成的角系数文件去跑 `rmcdhf_mpi`。现在由 `check_mcp` 在运行前检查 rangular 和 rmcdhf 的进程数是否一致；
  - 相对路径的工作目录会让 `MPI_TMP` 出错，改用绝对路径。
- **其他**：
  - 支持内存版 `rmcdhf_mem`；
  - 停止计算时按进程组发送信号（先 SIGTERM，再 SIGKILL），mpirun 的子进程也能一并结束。

### 3. 方便多次计算的功能

- **工作目录记录**：参数、态组和流程自动保存在 `工作目录/.grasp_gui.json`，再次打开时恢复，启动时打开上次使用的目录。
- **odd / even 态组**：11 个与宇称有关的程序页按组分别保存参数，文件名自动对应；“复制到另一组”时会替换文件名。

### 4. 手册算例全面核对

- 用 GRASP2018 手册中的全部算例（case1–4、example1–5、8、9）逐条检查：从原始脚本中解析出每次程序调用的输入，填入界面字段后重新生成输入，再与原文逐行比较。
- **结果**：399 次调用中 383 次完全一致，其余是等价写法、手写的自定义输入或界面未覆盖的程序。
- **由此发现并修复的问题**：
  - rtransition 计算 M1 / E2 时的追加提问没有回答；
  - rwfnestimate 缺少方法 4；
  - rnucleus 读不了 `9.6D1` 这种 Fortran 格式的数。
- **新增的专门页面**：rhfs、ris4、rcsfinteract、rtablevels、rtabtrans1、rtabtrans2。程序页按功能分为四组：核与组态、自洽场与 CI、性质与跃迁、结果与工具。

### 5. 独立可执行文件与布局

- **`build.sh` 用 PyInstaller 打包成单个文件**，遇到两个问题：
  - shiboken 需要 `__future__` 模块，PyInstaller 检测不到，需要用 hidden import 手动加入；
  - 打包程序会把 `LD_LIBRARY_PATH` 指向自带的库，这个设置会传给 GRASP 子进程，所以启动子进程前要恢复原值。
- **布局**：参数区扩大；GRASP 路径和 MPI 设置放进可收起的设置区；窗口大小和分隔条位置会被记住。

### 6. 结果查看器（v1.2.0，在 `wavefunction-viewer` worktree 中开发）

- **读取 GRASP 的二进制结果文件**（Fortran 无格式记录，每条记录前后各有 4 字节的长度标记）：
  - `.w` 波函数文件，文件头为 `G92RWF`；
  - `.m` / `.cm` 混合系数文件，文件头为 `G92MIX`，总能量 = EAV + EVAL；
  - 文本格式的 `.c` CSF 文件（每个 CSF 占 3 行，各块之间用 `*` 分隔）和 jj2lsj 输出的 `.lsj.lbl` 文件。
- **波函数查看器**：
  - 画 P(r)、Q(r) 和 P² + Q²，横轴可选 r、log r 或 √r，可叠加多个文件比较；
  - 表格列出轨道能量、⟨r⟩、峰值位置、节点数和归一化；
  - 积分在 GRASP 的指数网格 r = RNT·(exp((i−1)h) − 1) 上用 Simpson 公式计算；
  - 节点数按 GRASP `count.f90` 的方法计算：小于最大值 5% 的波瓣不计入（第一版没有这样处理，1s 轨道会显示出一个节点）。
- **能级与组成查看器**：
  - 能级图中每个 J^π 一列，能级线旁边不加标签，鼠标悬停时显示信息；
  - 点击能级后显示该原子态的 CSF 混合系数和 LSJ 组成；
  - 能量单位默认 eV，可切换为 cm⁻¹ 或原子单位，并记住选择。
- **按内容识别文件**：
  - 波函数文件和混合系数文件按文件头识别；
  - `.c` 文件按特征匹配（轨道数，以及各块的 CSF 数和 J^π）；
  - `.lsj.lbl` 文件按能级能量匹配；
  - 因此改过名的文件、`rwfn.inp`、`rwfn.out` 都能找到。
- **测试**：
  - 在手册全部算例上与 GRASP 自带的 rlevels、rwfnplot 对比，数据一致；
  - 中英文界面下都打开了所有文件、点了每个能级，没有出现异常。
- **测试中发现并修复的问题**：
  - CSF 的耦合行可能是空行，跳过空行会让“每个 CSF 三行”错位；
  - 表格调整列宽太慢，改为填完数据后统一调整一次；
  - 曲线改为先画到缓存图上，鼠标悬停时只重画标记点；
  - 列数很多时表头重叠，放不下的表头改为缩写。

### 7. 在其他机器上的问题（v1.1.1、v1.2.1）

- **字体显示为方框**：Qt 6 不自带字体，没有安装中文字体的机器上文字全部显示为方框。现在打包了 Noto Sans CJK 和 DejaVu Sans / Sans Mono（附许可证），启动时注册。
- **GRASP bin 目录重启后丢失**：原来只在运行计算时保存。现在修改后立即保存，关闭窗口时也会保存。
- **Arch 上出现 Fontconfig 报错**：可执行文件带的是打包机器上较旧的 libfontconfig，读不懂 Arch 上新版的 `/etc/fonts` 配置（如 `xsi:nil`）。现在打包程序使用 `~/.cache/grasp_gui/fonts.conf` 中一份自己的简单配置，只列出字体目录，并且不传给 GRASP 子进程。
- **Arch 上字体偏小**：打包程序不读取桌面环境的字体设置，Qt 默认使用 9 磅。为此新增了“视图”菜单调整字号，字号会被记住；高分辨率屏可以用 `QT_SCALE_FACTOR` 整体放大。

### 8. V2：在能级界面计算跃迁

- **目标**：用户不需要事先算好双正交变换和跃迁，在能级图上把两个能级连起来，界面自动计算。
- **GRASP 的做法**（来自源码）：rtransition 读取 isodata 和两个态各自的 `name.c`、`name.bw`、`name.(c)bm`，输出 `name1.name2.(c)t`；
  两组轨道来自不同计算时先运行 rbiotransform（由 `.c`、`.w`、`.(c)m` 生成 `.bw`、`.(c)bm`）；同一个文件时直接把 `.w`、`.(c)m` 当作 `.bw`、`.(c)bm`。
- **实现**（`grasp_trans.py`）：
  - 在 `工作目录/.grasp_gui/tr/<编号>/` 中计算，输入文件用软链接命名为 `a.*`、`b.*`，混合系数一律按 `.cm` 处理。这样改过名的文件、`.m` 与 `.cm` 混用都能算，也不会覆盖用户的文件。
  - 跃迁类型按选择定则自动选：宇称改变时为 E1（E1 禁戒时为 M2），宇称不变时为 M1 和 E2；J = 0 → 0 时提示禁戒。
  - 默认只算两个能级所在的 J^π：把 `.c` 和 `.cm` 中的对应块写成新文件（块号重新编排，文件头的 CSF 总数、能级总数、Σ ncf·nev 和块数随之更新）。双正交变换按块进行，结果与全部计算时相同。rbiotransform 自带的“只变换部分 J”选项不能用：没选的块会原样写进 `.cbm`，rtransition 仍全部计算，结果是错的。
  - “计算全部 J (MPI)”算两个文件间的全部跃迁，用于寿命和分支比；找不到 MPI 程序或路径过长时改用串行。
  - 每次计算有自己的子目录，结果按（文件对，J^π 范围，跃迁类型）缓存，合并显示时同一跃迁只保留一个；记录输入文件的修改时间和大小，文件改变后重算。
  - case1 计时：所选 J 串行 11 秒；全部 J 串行 104 秒，4 个 MPI 进程 33 秒。
  - `.ct` 按固定列宽读取（能级序号超过 99 时文件标记与序号相连，按空格切分会出错）。
- **遇到的问题**：
  - GRASP 的 MPI 库把路径存放在 128 个字符的变量中，路径过长时报 `sys_getwd` 错误，但退出码仍为 0。为此计算目录用短名字，路径仍过长时改用串行版本，并在计算后检查输出文件是否真的生成。
  - 改名后的混合系数文件找不到同名 `.w` 时，不同计算的 `.w` 常有相同的轨道集合（如 `odd4.w` 与 `even4.w`），猜错会悄悄算出错误结果，因此有多个候选时拒绝计算并列出候选。
- **验证**：重新计算手册中全部 6 组跃迁参考结果（example1、3、4、5、9 和 case1，共 389 条跃迁），全部找到；强跃迁与参考值的相对偏差不超过 7×10⁻⁶，几条很弱的跃迁偏差达 3.4×10⁻⁴，这是串行与 MPI 之间的数值差异（用 MPI 计算时与参考值完全一致）。界面端到端测试（拖动连线、Shift 点击、计算、缓存、输入改变后重算、J = 0 → 0）中英文均通过。

### 9. V2：导出与图上标注

- **标注跃迁**：在图上连线并算好的跃迁画成带波长的箭头，右键移除，保存在 `工作目录/.grasp_gui/marked.json`；“选中能级的全部已算跃迁”改为可选（默认不画），图不再被大量连线占满。
- **导出**：能级图（PDF / SVG / PNG）标注组态、谱项（如 [Ne]3s3p ¹P°₁），上下标自己绘制，不依赖字体中的上下标字符；只画标注的跃迁，箭头上可写波长。能级和跃迁导出为 CSV；已算跃迁可保存为 GRASP 的 `.ct` 文件。
- **兼容性**：
  - GRASP 文件逐行沿用 rtransition 的原始输出，只把文件头中的 `a`、`b` 换回真实名称（rtabtrans1 靠它与态文件对应）。用 example4 验证：导出文件与 GRASP 原文件逐字节相同，rtabtrans1/2 生成的跃迁表、寿命表也完全相同。
  - CSV 用 UTF-8 带 BOM（Excel 能识别中文和特殊字符），表头和组态、谱项都用 ASCII（NIST 写法），便于程序读取。
  - Qt 6.8.0–6.8.2 写出的 PDF 文档信息字典有错（poppler 报 48 处语法错误），6.7 和 6.8.3 正常；打包固定为 Qt 6.8 LTS 且 ≥ 6.8.3，源码运行时遇到这些版本会提示。
  - 计算缓存加了格式版本号，以后格式改变时旧缓存自动重算。
- **组态缩写**：规则为“缩写 ↔ 完整组态”，按子壳层和占据数匹配（与书写顺序无关），子壳层多的先用，缩写可为空（省略）；LSJ 标记不含核心轨道，先从 CSF 文件补上。

### 10. V2：主窗口分为计算、径向波函数、能级图三个标签页

- **起因**：正式版用户希望不经过计算界面，直接打开能级图和波函数查看结果。“波函数”页后来改名为“径向波函数”。曾考虑在 v1.4 中只加命令行入口和单独的查看器窗口，后来决定在 V2 中改主界面，v1.4 不发布（代码留在本地分支 `viewers`）。
- **结构**：`grasp_gui.py` 只做外壳（启动参数、字体、语言、菜单、工作目录和运行设置、三个标签页）；计算页 `calc_panel.py` 从原来的 MainWindow 拆出；两个查看器改为面板 `wfn_panel.py`、`level_panel.py`；共用部分在 `gui_common.py`（运行设置 RunSettings、最近的目录、面板基类 Panel、单独窗口 PanelWindow）。面板之间不直接调用，由外壳传递：工作目录改变 → 各面板 `set_dir`；计算完成 → 查看器重新扫描；面板的状态消息只在其标签页显示时出现。
- **工作目录**：三页共用顶部的工作目录，计算页按目录载入记录，查看器显示该目录。要同时看另一个目录，用“在新窗口中打开”（有自己的目录栏、最近的目录），能级窗口仍使用主窗口的运行设置；关闭主窗口时一起关闭。
- **入口**：`grasp_gui levels|wfn [目录]` 打开主界面的对应页（默认为当前目录）；`grasp_gui 文件` 按文件内容（与扩展名无关）打开对应的页并只勾选这个文件；以 `grasp_levels` / `grasp_wfn` 为名的符号链接同样处理。拖放到查看器上的目录作为工作目录，文件切换到对应的页。启动时回到上次的标签页。
- **切换语言 / 字号**：计算页保留原对象重建控件（运行中的计算不受影响）；查看器面板重新创建并恢复勾选的文件，正在计算跃迁的面板保留原样。
- **测试**：新增主窗口测试 47 项（入口解析、标签页、目录联动、打开文件、拖放、计算后刷新、新窗口、切换语言后的状态、关闭后恢复），中英文均通过；原有的跃迁界面 32 项、导出 20 项、示例导入对话框测试改为新的模块后均通过。

### 11. V2 发布前：窗口边框、分隔条和键盘报错

- **对话框看不出边界**：Wayland 下（WSLg）窗口的标题栏和边框由 Qt 自己画（bradient 装饰），颜色取自程序调色板的窗口背景色，与窗口内容相同，WSLg 又没有窗口阴影。试过 Qt 的 adwaita 装饰：有边框线，但按钮布局只从 xdg-desktop-portal 读取，WSL 中没有这个服务，结果没有关闭 / 最大化按钮。最后的做法：程序调色板的窗口背景色换成浅灰蓝色（装饰直接使用程序调色板），每个窗口在 Polish 时恢复原来的调色板。注意复制来的 QPalette 不带“已设置”标记，设给窗口后仍会沿用程序调色板，必须逐项显式设置。
- **分隔条不明显**：所有 QSplitter 加宽，分隔条由事件过滤器自己绘制（浅色底、5 个深色圆点，鼠标悬停时加亮），不必修改各处的 QSplitter。
- **xkbcommon 报错**：打包的 libxkbcommon（1.4.0）比较新系统的 Compose 文件旧，遇到不认识的按键名（如 `dead_hamza`）时每行打印一条 ERROR；这些行本来就被跳过，因此默认设置 `XKB_LOG_LEVEL=critical`。
- 这些修复最初也提交在 main 上（未发布），V1 停在 v1.3.0。

### 12. V2.1：共享配置

- **目标**：鼓励用户共享计算方案。配置文件只保存各步的表单参数 (不保存生成的程序输入)，导入时由导入方的界面重新生成，
  所以生成规则修正后旧配置仍然正确；不含结果和与机器有关的设置。格式见 `docs/CONFIG_FORMAT.md`，实现见 `grasp_config.py`。
- **兼容性**：格式版本号 (示例格式为第 1 版，共享配置为第 2 版)，旧版本由 `upgrade()` 逐版转换，比程序新的版本拒绝并提示更新；
  缺少的参数用默认值、不认识的参数忽略，都告知用户；`package` 字段 (目前只有 GRASP2018) 留给 V3。
- **流程任务记录来源**：`Step.job` 中加入 `step`、`vals`，加入流程时记录态组。2.1 之前加入流程的任务没有这些信息，只能按命令导出并提示。
- **安全**：“自定义”程序页和按命令保存的步骤可以运行任意程序；导入对话框醒目列出这些命令，文件中的配置含有自定义命令时需要确认。
  附带文件只能是普通文件名的小文本文件 (每个 ≤ 1 MB，合计 ≤ 4 MB)。
- **手册算例**改为同一格式 (`tools/make_demos.py` 生成)，内容不变；`--check` 仍与手册的输入文件逐行一致。
- **验证**：9 个示例用新代码生成的输入与旧版本逐字相同，“导出 → 导入”后流程不变；case4 运行 → 导出 (附参考能量) → 导入到新目录 →
  重算，能量与配置中的参考值一致，场移结果 `.fci` 逐字节相同。配置测试 41 项 (英文界面 42 项)。
- **顺带修复**：翻译函数 `T(s, **kw)` 的占位符不能叫 `s`。能级图中“无法确定对应的 .w 文件”的提示用了 `{s}`，在 2.0.0 中会直接报错而不显示提示。

### 13. 旧系统版 (legacy 分支)

- **需求**：用户的结果文件在服务器上，太大不能下载；在服务器上直接运行界面，通过 `ssh -X` 显示。服务器常是 CentOS 7 / RHEL 8 之类，
  普通版要求 glibc ≥ 2.35。
- **方案**：PySide6 的所有版本都要求 glibc ≥ 2.28，因此旧系统版用 PySide2 (Qt 5.15，glibc 2.17)。代码仍按 PySide6 编写，
  `legacy/PySide6/` 是一个替身包，把界面用到的 Qt 6 写法对应到 PySide2；`legacy` 分支只比 main 多一个 `legacy/` 目录，合并 main 不冲突。
  在 CentOS 7 容器 (manylinux2014) 中打包，另外打包 libxcb 和 glvnd 的 libGL，除 glibc 外不依赖系统的库。
- **X11 转发下的问题**：关闭 MIT-SHM 和 OpenGL；没有会话 D-Bus 且缺少 `/etc/machine-id` 时旧版 libdbus 会终止程序，因此不连接 D-Bus；
  没有 DISPLAY 时给出明确提示 (Qt 5 只会异常终止)；Qt 5 下粗体标签宽度不对，替身中一并修正。
- **验证**：CentOS 7、Debian 9、Ubuntu 18.04、Rocky 8 和不装任何 X 库的 CentOS 7 容器中启动并显示到 Xvfb 容器；
  两个容器之间真实的 `ssh -X` 登录并操作能级图；全部界面测试在 PySide2 下通过。详见 `legacy/README.md`。

### 14. GRASPG 能否直接替换 GRASP2018 (V3 前的调查)

结论：**不能无缝替换**，V3 仍需专门支持；但计算结果的文件格式相同，现有的查看器基本可以直接使用。

- **来源**：[compas/graspg](https://github.com/compas/graspg) (基于组态波函数生成器 CSFG；Li et al., CPC 283 (2023) 108562)。
  它不是独立的 GRASP，而是装进已有的 GRASP2018 目录 (`srcg/`)，向 `$GRASP/bin` **新增**程序，不修改 GRASP2018 的程序。
  所以装了 GRASPG 的机器上，现有界面照常可用。
- **新增的程序** (都带 `_csfg` 后缀)：`rcsfggenerate_csfg`、`rcsfginteract_csfg`、`rcsfgzerofirst_csfg`、`rcsfgsplit_csfg`、
  `rcsfgexpand_csfg`、`rcsfgextend_csfg`、`rcsfgblocksplit_csfg`、`rwfnestimate_csfg`、`rangular_csfg_mpi`、`rmcdhf_csfg_mpi`、
  `rci_csfg_mpi`、`rci_block_csfg_mpi`、`rmixaccumulate_csfg`、`jj2lsj_csfg`、`rlevels_csfg`、`rsave_csfg`、`rdistHmatrix_csfg`。
  `rangular`、`rmcdhf`、`rci` 只有 MPI 版本。
- **计算流程不同** (对照 `graspgtest/script1` 中作者给出的 csf / csfg 两套脚本)：
  - 组态列表是 CSFG 列表 `*.g` 加标记文件 `*.l` (输入文件为 `rcsfg.inp`、`rlabel.inp`)，不是 `*.c`/`rcsf.inp`；
  - `rcsfggenerate_csfg` 的提问不同 (没有开头的排序问题，另有生成 CSFG 的轨道和激发设置)；
  - `rci_csfg_mpi` 多出 Breit 相互作用按 n、l 截断和每个进程内存上限的问题；
  - 其他程序 (`rwfnestimate`、`rmcdhf`、`jj2lsj`) 的提问与 GRASP2018 相同，只是程序名和输入文件不同；`rnucleus`、`rtransition`、
    `rbiotransform` 等沿用 GRASP2018。
- **结果文件相同**：`rsave_csfg` 照常写出 `.w`、`.c` (展开后的 CSF 列表)、`.m`，并另存 `.g`、`.l`；`rci_csfg_mpi` 自动写出 `.c`。
  `rlevels_csfg` 与 `rlevels` 读的是同样格式的 `.m`/`.cm`，区别只在于能读部分写完的文件 (`rci_block_csfg_mpi` 逐块计算) 和更长的
  LSJ 标记 (128 字符)。因此径向波函数和能级图页应能直接读 GRASPG 的结果；V3 需要实测，并让 `grasp_mix` 容忍不完整的混合系数文件。
- **另外注意**：GRASPG 的脚本用了较新的 GRASP 程序 (`jj2lsj_2024`、`rtransition_2024`)，GRASP 主线在 2018 版之后也有更新。
- **实测** (2026-10-31，单独编译的 GRASP2018 + GRASPG，`graspgtest/script1`，B 样 Mo 到 n = 6)：CSFG 版 5 分钟，CSF 版
  50 分钟 (4 个 MPI 进程)；`n6.lev` 最大能量差 4.0e-7、`n6CI.clev` 1.1e-6 Hartree (约 0.24 cm⁻¹)，J/宇称顺序相同。
  V2 的查看器能读 GRASPG 的结果：能量与 `rlevels_csfg` 一致，`.c` 与 LSJ 标记对得上，`.g`/`.l` 不会被当成结果文件。
- **编译**：GRASPG 的 Makefile 到 `src/lib/*` 找 `.mod`，所以 GRASP2018 要用 make 编译 (CMake 不行)，并串行编译
  (并行时模块顺序出错)；`srcg` 中 `make` 时需导出 `GRASP`、`FC`、`FC_FLAGS`、`FC_MPI`、`FC_MPIFLAGS`、`LAPACK_LIBS`。

#### V3 中的实现 (分支 `v3`)

- 计算页右上角的“程序包”选择 GRASP2018 / GRASPG，按工作目录保存；只换程序页 (`core.STEP_GROUPS[package]`)，
  rnucleus、性质与跃迁、表格程序和“自定义”两者共用同一个表单。GRASPG 的程序页是独立的 `Step` 子类 (key 带 `_csfg`)，
  参数与 GRASP2018 的程序页分开保存，态组 odd/even 照常使用。
- **标记轨道**：`rcsfggenerate_csfg` 的提问与 `rcsfgenerate` 相同，只是没有开头的排序问题。激发数为 0 的列表 (多参考)
  必须先给出：它们的活跃轨道成为“标记轨道” (写入 `rlabel.out`)，其余关联轨道才由 CSFG 合并。不给出时所有轨道都是标记轨道：
  script1 的奇宇称列表由 2420 个 CSFG 变成 16354 个。多参考加激发列表生成的 `rlabel.out` 与只生成多参考时相同。
- **文件**：`rcsfgsplit_csfg` 读 `name.g` 和 `rlabel.inp`，为每层复制一份 `.l`；`rangular_csfg_mpi` 读 `rcsfg.inp`、
  `rlabel.inp`，写展开的 `rcsf.out`；`rsave_csfg` 另存 `.g`、`.l`；`rci_csfg_mpi` 读 `name.g/.l/.w`，写 `name.c`。
  `rcsfginteract_csfg` 读 `rcsfgmr.inp`/`rcsfgmr.l` 和 `rcsfg.inp`/`rlabel.inp` (两个标记文件必须相同)，写 `rcsfg.out`。
- **rci_csfg 的提问** (`getcid.f90`)：横向频率之后总是问“按 n 限制 Breit”(y/n) 和 n 值、“按 l 限制”和 l 值 (不限制时也读入)；
  自能之后总是问 NQEDMAX (谱学轨道的最大 n，≤ 8 且不超过列表中的最大 n，超出时程序反复要求重新输入) 和每个进程的内存上限 (GB)。
- **MPI**：`rangular`、`rmcdhf`、`rci` 只有 `*_csfg_mpi`。不经 mpirun 直接启动 (MPI 单例) 可以运行，所以进程数为 1 时直接启动；
  角系数总是写在 `$MPI_TMP/000/csfg_mcp000.30` 等 (单进程时也是)，`rmcdhf_csfg` 前按此检查进程数 (`core.check_mcp_csfg`)。
  任务的 `mpi` 为 `"only"`，导出的脚本在没有 mpirun 时也设置 `MPI_TMP`。
- **出错时退出码为 0**：如缺少 `rlabel.inp` 时打印 “rlabel.inp not exists, Stop Error!” 后正常退出，Fortran 的
  `STOP 'Error! …'` 也是。GRASPG 程序结束后检查输出的最后 4 KB (`core.csfg_failed`)，发现错误时停止流程。
- **示例** `demos/graspg1.json` (`tools/make_demos.py` 的 `graspg1`)：按作者的 `script1/csfg` 逐步写成 (每次 rmcdhf_csfg
  运行两次，同作者)，参考能量取自该脚本的结果；包括 GRASP2018 的 `rbiotransform`、`rtransition` (作者的脚本中未运行)。
  共享配置的 `package` 为 `"GRASPG"` (用到 GRASPG 程序页时由 `core.package_of` 确定)；GRASPG 配置也可以用 GRASP2018 的程序页。
- **CSFG 工具** (GRASPG 独有的第五个分组)：`rcsfgexpand_csfg` (无提问，读 rcsfg.inp/rlabel.inp，写 rcsf.out；结果与
  rsave_csfg 的 .c 逐字节相同)、`rcsfgextend_csfg` (提问同 rcsfgsplit_csfg，读 name.g/name.l)、`rcsfgblocksplit_csfg`
  (名称 + “各块同一组轨道?” 回答 y；输出 name_odd1.g …)、`rmixaccumulate_csfg` (名称、CI?、比例、排序?、换轨道集? [+ 轨道集]；
  输出 rcsfg.out 或 rcsfg.nmax)。错误识别补充了 “Error!… is not found”“Problems with opening the file” 等写法。
- **逐块 CI** (`rci_block_csfg`，一个程序页三种方式)：默认设置时提问与 rci_csfg 相同，能级行为空 (一个空格) 时
  “No state is selected, just build H-matrix”：只构造 H 矩阵，存在 `MPI_TMP/<rank>/rci<rank>.res`、`ncols<rank>.txt`，
  并写出没有能级的 name.cm。重启用非默认设置：`n`、名称、`y` (Restarting RCI90?)、自能 y/n、降低 CRITE y/n、内存、能级
  (光速等不再询问)。重启必须与保存时进程数相同，否则先用 `rdistHmatrix_csfg` (源目录、目标目录、源进程数、目标进程数；
  目标目录必须已存在) 重新分配；程序页先把 MPI 临时目录 `mv -T` 为源目录再 `mkdir`。实测 (graspg1 的 oddCI6/evenCI6 拆块，
  2 进程构造、重新分配后 1 进程重启) 能量与 rci_csfg 相同 (1e-8 Hartree 以内)。
- **未写完的混合系数文件**：`grasp_mix.read_mix` 读到不完整的记录时保留已完整的块，结果的 `partial` 为真 (能级图在文件的
  提示中说明)；只有文件头不完整时才报错。
- 尚未做成程序页 (可用“自定义”运行)：`rcsfgzerofirst_csfg` (零阶 / 一阶空间划分，要配合 rangular_csfg 和 rci_csfg 的
  非默认提问：一阶微扰、icut，见 `graspgtest/script3_ZF`)。

### 15. V2.2：零阶 / 一阶划分 (GRASP2018 原有的功能)

- GRASP2018 的 `rcsfzerofirst` 把零阶空间 (如多参考) 的 CSF 排到各块最前面 (输出 rcsf.out，不写 icut 文件，各块的数只打印在
  屏幕上)；`rangular`、`rci` 的非默认设置逐块询问 ICCUT，各块前 ICCUT 个 CSF 按变分处理，其余按一阶微扰处理；`rmcdhf` 不变。
- 提问 (源码 `rangular90*/getinf.f90`、`rci90*/getcid.f90`、`setres.f90`)：rangular 为 `n` + 各块 ICCUT (rangular_mpi
  要求 1 < ICCUT < ncf，否则反复要求重新输入，串行版不检查)；rci 为 `n`、名称、`n` (不重启)、`n` (不改光速)、`y` (一阶微扰)、
  各块 ICCUT (0 ≤ ICCUT ≤ ncf)，之后与默认设置相同。GRASPG 作者的 `script3_ZF/csf` 也是这样输入，ICCUT 从 rcsfzerofirst 的
  屏幕输出中截取。
- 界面：ICCUT 取零阶列表各块的 CSF 数。加入流程时该列表可能还不存在，所以输入中写占位行 `@ICCUT 零阶列表 CSF 列表`，运行时由
  `core.expand_iccut` 换成各块的数并检查对称块是否对应；预览和导出的脚本用 awk (`core.ICCUT_AWK`) 在不加引号的 here-document
  中计算，与 Python 的结果相同。新增字段标记 `added="2.2.0"`：2.1.0 导出的配置 (和示例) 中没有时用默认值 (不划分)，不提示。
- 实测 (B 样 Mo 奇宇称，n = 2 多参考为零阶空间，到 n = 3 的单双激发，ICCUT = 2、4，CSF 数 325、489)：划分后 MCDHF 和 CI
  能量比不划分高约 4e-4 Hartree (零阶空间很小时的近似)；串行和 2 个 MPI 进程都正常，rangular 和 rci 的日志中 ICCUT 正确。
  另外，GRASP2018 的串行 rci 与 rci_mpi 在这个算例中本身就相差约 1.1e-4 Hartree (与划分无关)。
- 示例的参数和生成的输入不变 (手册算例比对：373 次完全一致，与之前相同)。

### 16. 供脚本和 AI agent 使用的配置规范

- 目的：别的 agent 运行 GRASP 时顺手给出与本程序匹配的配置文件，操作者导入后逐步核对参数、重算并与参考能量对照。
- 原则是“配置就是计算本身”：agent 先写配置，只用由配置生成的脚本运行 GRASP，这样配置与实际计算一致 (手写输入再“翻译”
  成表单容易对不上)。规则见 `docs/AGENT_SPEC.md`，其中有可直接放进 agent 指令的一段文字。
- 不打开窗口的命令 (`grasp_config.cli`，`grasp_gui` 和 `python3 grasp_config.py` 都可调用)：`check` (检查，列出警告、提示和
  自定义命令)、`script` (与界面“导出流程为 shell 脚本”相同)、`steps` (由字段定义生成参数说明，即 `docs/STEPS.md`，改动字段后
  重新生成)、`reference` (混合系数文件的能级能量，即配置的 `reference`)。
- 导入时的当前态组改为第一个按态组保存参数的步骤的组 (原来取第一步，rnucleus 没有组时总是 odd)。
- 实测：例 1 和规范中的最小示例按 check → script → 运行 → reference 走一遍，能量与参考值完全一致，导入正常。

### 17. 关闭窗口后继续运行 (后台运行)

- 需求：前台 / 后台由用户选择，并且能无缝切换；退出时只在有计算运行时询问；窗口意外消失 (ssh 断开、崩溃) 时自动转为后台。
- 程序的输出管道连着界面进程，无法把正在运行的程序“搬”出界面，所以所有计算都由独立的运行进程 (`grasp_bg.py`，
  `grasp_gui _run 目录`，新会话，不受 SIGHUP 影响) 执行，界面只是查看者：前台 / 后台的区别只是有没有窗口连着，切换时不碰运行中的程序。
- 输出实时推送：运行进程在 Linux 抽象命名空间的 Unix 套接字 (名称由用户和目录的哈希决定，不在目录中留下文件，网络文件系统上也可用)
  上把每块输出发给已连接的界面，同时写 `.grasp_gui_run.log`。界面连上时先补发最近 1 MB 的输出，在同一把锁下再加入实时发送，
  不丢不重。套接字也是锁：同一目录只有一个运行进程。只有连不上时 (计算在另一台机器上) 才每 0.2 秒读日志文件。
- 状态文件 `.grasp_gui_run.json`：pid、主机、开始 / 结束时间、各步标题、当前步、结果；`seen` 记录界面是否已显示过结果
  (在后台结束的计算下次打开目录时显示一次)。本机 running 但进程已不在时视为意外结束 (lost)。
- 运行进程复用 `core.Runner` (加了 `started` 回调报告当前步)，运行前的检查不变；停止：界面发 "stop"，或对运行进程发 SIGTERM。
  打包程序中 `_run` 在导入 Qt 之前分派 (PyInstaller 的启动钩子只载入 QtCore，不需要显示)；子进程环境的整理提成
  `core.child_env`，运行进程本身也用它启动 (否则会继承界面自带库的 LD_LIBRARY_PATH)。
- 实测：例 1 串行和 2 个 MPI 进程 (重新连接后输出与日志完全一致，能量与参考值一致)；停止、SIGTERM、SIGKILL (lost)；
  界面被 SIGKILL 后 case1 继续从第 6 步算到第 28 步；界面离屏测试 (退出时后台继续 / 停止、重新打开接上、后台结束后显示一次、
  另一台机器)；打包程序和旧系统版 (CentOS 7 容器中 ssh -X：断开 ssh 后计算继续，重新连接后接上，退出对话框，空闲时直接退出)。
- 限制：systemd-logind 设置 `KillUserProcesses=yes` 时，登录会话 (session-*.scope) 结束时其中的进程全部被结束，新会话
  (setsid) 也躲不过，因为按 cgroup 结束。`grasp_bg.logout_kills()` 检查三个条件 (本进程在 session scope 中、logind 的
  KillUserProcesses 为真 (busctl 读取实际生效的值)、用户不在 KillExcludeUsers 中)，都满足时退出对话框提示；解决办法
  (管理员关闭、lingering + `systemd-run --user --scope`、Slurm) 写在 INTERFACE.md。界面不自动用 systemd-run 启动运行进程：
  需要 lingering 才有效，而且多数系统不需要。
- v2.3.1 的修正 (自动测试发现)：几毫秒就结束的计算 (如单独运行 rnucleus) 在界面连上之前运行进程已经退出，界面报告
  “无法启动后台运行进程”，看不到输出。现在运行进程先等启动它的界面连上 (最多 10 秒) 再开始；停止 / SIGTERM 时不再等待，
  一步也不执行 (`Runner.run_all` 不再在开始时清除停止标记，能级图页再次计算前自己清除)。
- v2.3.2：“停止”恰好在程序启动的同时到达时 (当时还没有进程可结束) 不再被错过，程序启动后立即结束。

v2.3.2 起，GRASP2018 的同一份 jj2lsj 源码由 CMake 安装为 `jj2lsj`，Makefile 安装为 `jj2lsj_2025`：运行和程序缺失检查优先用原名，找不到时用后者。
导出的 shell / Slurm 脚本在运行时选择名称；不替代输入不同的 `jj2lsj_2024` 或 GRASPG 的 `jj2lsj_csfg`。

GRASPG 零阶 / 一阶示例 `demos/graspg_zf.json` 按作者 `script3_ZF/csfg` 缩小到 n = 4：Be 样 C III，24 个能级；冻结 1s² 的价电子激发作为零阶空间，完整空间允许 1s 激发。ICCUT 从零阶列表自动取得。2 个 MPI 进程约 10 秒，参考能量由导出的脚本重算得到，与同规模作者脚本一致。

## 测试方法

自动测试在 `tests/` 中 (`python3 -m pytest tests`，GitHub Actions 对每个 PR 运行)：算例的配置检查和生成脚本的标准结果
(`tests/golden/`)、导出 / 导入往返、翻译完整、只用标准库的模块不导入 Qt、Python 3.10 语法、后台运行进程、offscreen 界面冒烟测试；
有 GRASP 时 (`GRASP_BINDIR`) 用 GRASP 重算算例并与参考能量比较。参与开发的规则见根目录的 `AGENTS.md`。
GRASPG 测试单独读取 `GRASPG_BINDIR` (目录中须有 `rcsfggenerate_csfg`)，未设置时跳过，
不受 `GRASP_REQUIRED` 影响。`GRASPG_NP` 指定 MPI 进程数，默认 2；角向、SCF 和 CI 共用此进程数。
`graspg_zf` 属于快算例，`graspg1` 需要 `GRASP_SLOW=1`：

```bash
GRASPG_BINDIR=/path/to/graspg/bin GRASPG_NP=2 GRASP_SLOW=1 python3 -m pytest tests/test_grasp.py -k graspg
```

下表是此前的人工 / 一次性测试：

| 测试 | 内容 |
|---|---|
| 输入生成对比 | 手册全部算例的每次程序调用：从原始输入解析出字段，重新生成后逐行比较（383 次完全一致） |
| 会话 / 态组 | 21 项检查：工作目录记录与恢复、odd/even 切换、复制、切换语言、旧格式参数 |
| 结果读取 | 所有 `.w`、`.m`、`.cm`、`.c`、`.lsj.lbl` 文件与 rlevels、rwfnplot 对比 |
| 查看器 | 中英文界面下打开所有文件、点击每个能级，统计异常 |
| 主窗口 | 命令行入口、三个标签页的联动、拖放、新窗口、切换语言后的状态保留 |
| 共享配置 | 格式检查和升级、导出 → 导入往返、附带文件、自定义命令确认；导出后在新目录重算并与参考能量对比 |
| 打包程序 | 用干净导出的提交打包；在清空的环境和无字体环境下启动；模拟新版 fontconfig；发布后以访客身份下载并核对校验和 |

## 发布流程

1. 在 main 上提交，用 `git archive <提交>` 导出干净的代码，运行 `./build.sh`。
2. 冒烟测试：在清空的环境下用中英文界面各启动一次，并在没有字体的环境下再启动一次。
3. 旧系统版：把 main 合并到 `legacy`，用该提交的干净代码运行 `legacy/build.sh`，在旧系统容器中测试。
4. 推送带注释的标签 `vX.Y.Z`，用 GitHub API 创建 Release，上传两个可执行文件和各自的 `.sha256`。
5. 以未登录访客身份下载，运行 `sha256sum -c`，并确认 Release 页面的“最新版本”正确。

## 分支与后续计划

- **V1**：停在 v1.3.0，不再更新；GitHub 上保留分支 `v1`（指向 v1.3.0）。
- **V2**：v2.0.0 起为正式版，`main` 即 V2；后续的 V2.x 在 main 上开发。
  - v2.1.0：共享配置 (见第 12 节)；公共配置库是另外的社区项目，暂不做。
  - v2.2.0：零阶 / 一阶划分 (见第 15 节)。
  - v2.3.0：供脚本和 AI agent 使用的配置规范与命令行工具 (见第 16 节)；关闭窗口后继续运行 (见第 17 节)。
  - v2.3.1：修正很快结束的计算接不上后台运行进程的问题；自动测试、CI 和参与开发的规则 (其他 agent 通过 PR 参与开发)。
  - v2.3.2：`jj2lsj_2025` 的自动替代；停止与程序启动同时发生时的修正。
- **legacy**：旧系统版 (见第 13 节)，与 main 一起发布。
- **V3** (分支 `v3`)：支持 GRASP 的扩展和再开发版本。基础工作：把各程序的输入规则（`grasp_core.py` 中的字段和提问顺序）按
  “程序包 / 版本”区分 (共享配置的 `package` 字段已为此预留)，按 bin 目录中的程序识别可用的扩展，并识别它们特有的程序和文件格式。
  - **GRASPG** (优先)：不能无缝替换 GRASP2018 (见第 14 节)。已完成：程序包选择、CSFG 流程的程序页 (`.g`/`.l` 文件、
    `rcsfggenerate_csfg` 等的提问)、示例 graspg1、CSFG 工具 (expand、extend、blocksplit、mixaccumulate)。逐块 CI (rci_block_csfg、
    rdistHmatrix_csfg)，查看器读未写完的混合系数文件。待做：零阶 / 一阶划分 (rcsfgzerofirst_csfg 及 rangular/rci 的非默认提问)。
  - **QED** (暂缓，先做 GRASPG)：[compas/grasp-rci-qed](https://github.com/compas/grasp-rci-qed) 的 `rci-qed` (改写的 `rci_mpi`，单独安装到
    `$GRASP/bin`；自能可选不同模型，包括 Shabaev 等的模型算符 QEDMOD、Flambaum–Ginges、Pyykkö–Zhao 和原来的氢样估计；设置另存为
    TOML 文件)。`rci-qed.pt <state>` (命令行参数，不是交互输入) 把各能级的能量按项列出：动能 + 核势、Coulomb、Breit、正常 / 特殊质量
    移位、真空极化、自能。需要这两个程序的程序页，并在能级图中显示各项修正。作者注明仍在开发中。GRASP2018 自带 `rci` 中的真空极化和
    自能估计已在 V2 中支持。
  - **grasp-continuum**：连续态计算。
