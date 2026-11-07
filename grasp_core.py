"""
GRASP 图形界面的核心逻辑（与界面库无关）：

  * 各 GRASP 程序的参数字段描述，以及由参数生成交互输入 (stdin) 的规则；
    提问顺序与 grasptest/case1/script 中各 sh_* 脚本的 here-document 一致
  * 在工作目录中依次运行任务 (可选 MPI)、导出 shell / Slurm 脚本
  * 界面文字的中英文翻译
"""

import codecs
import csv
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading

VERSION = "2.5.1"         # 发布时改为发布的版本号 (写入导出的共享配置)
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".config", "grasp_gui.json")

# 同一份 GRASP 源码由不同构建方式安装出的名称；不包含输入不同的旧版程序。
PROGRAM_ALIASES = {"jj2lsj": ["jj2lsj_2025"]}


def program_path(bindir, prog):
    """优先查找原名，再尝试同源程序的替代名；与导出脚本的 PATH 优先级一致。"""
    for name in [prog] + PROGRAM_ALIASES.get(prog, []):
        p = os.path.join(bindir, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
        p = shutil.which(name)
        if p:
            return p
    return None


def physical_cpu_count():
    """核数供计算默认值和数据分析共用；缺少拓扑时保守地按逻辑核数的一半估计。"""
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/cpuinfo", encoding="ascii") as stream:
                blocks = re.split(r"\n\s*\n", stream.read().strip())
            cores = set()
            for block in blocks:
                pairs = (line.partition(":") for line in block.splitlines())
                info = {key.strip(): value.strip() for key, sep, value in pairs if sep}
                if "processor" not in info:
                    continue
                package, core = info.get("physical id", ""), info.get("core id", "")
                if not package.isdigit() or not core.isdigit():
                    raise ValueError("incomplete CPU topology")
                cores.add((int(package), int(core)))
            if cores:
                return len(cores)
        elif sys.platform == "darwin":
            count = int(subprocess.check_output(["sysctl", "-n", "hw.physicalcpu"], text=True,
                                                stderr=subprocess.DEVNULL, timeout=2).strip())
            if count > 0:
                return count
    except (OSError, ValueError, subprocess.SubprocessError):
        # 拓扑不可读不会阻止启动；此估计只决定未保存过的默认值。
        pass
    return max(1, (os.cpu_count() or 1) // 2)


def default_mpi_np(bindir, mpirun):
    """仅在启动器和常用 MPI 程序均可找到时默认并行，不启动外部计算来探测。"""
    try:
        launcher = shlex.split(mpirun or "mpirun")
    except ValueError:
        return 1
    if not launcher or not shutil.which(launcher[0]):
        return 1
    programs = ("rangular_mpi", "rmcdhf_mpi", "rci_mpi", "rbiotransform_mpi", "rtransition_mpi")
    if not all(program_path(bindir, prog) for prog in programs):
        return 1
    return min(physical_cpu_count(), 4)


# 输出窗口只保留最后这么多行（完整输出可写入日志文件），避免大量输出拖慢界面
MAX_OUT_LINES = 5000

# Slurm 作业脚本的默认设置
SLURM_DEFAULTS = dict(job="grasp", partition="", nodes="1", ntasks="32", time="2-00:00:00", mem="",
                      bindir="$HOME/grasp/bin", modules="", mpirun="mpirun -np $SLURM_NTASKS",
                      mpitmp="$SLURM_SUBMIT_DIR/tmp_mpi", mail="", extra="")

# ---------------------------------------------------------------------------
#  界面语言
# ---------------------------------------------------------------------------

LANG = "zh"

EN = {
    "修改 NSIC": "Modify NSIC",
    "  NSIC": "  NSIC",
    "非负整数；仅勾选修改 NSIC 时使用": "Non-negative integer; used only when Modify NSIC is checked",
    "NSIC 必须是非负整数": "NSIC must be a non-negative integer",
    "第 {i} 步 ({step})：{e}": "Step {i} ({step}): {e}",
    "勾选后修改 NSIC；不勾选时用程序的默认值": "Override NSIC when checked; otherwise use the program default",
    # 主窗口
    "GRASP 图形界面": "GRASP GUI",
    "文件": "File",
    "保存参数…": "Save parameters…",
    "载入参数…": "Load parameters…",
    "导出流程为 shell 脚本…": "Export pipeline as shell script…",
    "退出": "Quit",
    "语言": "Language",
    "工作目录": "Working directory",
    "GRASP bin 目录": "GRASP bin directory",
    "浏览…": "Browse…",
    "查看文件": "List files",
    "预览输入": "Preview input",
    "▶ 运行当前程序": "▶ Run this program",
    "加入流程 ↓": "Add to pipeline ↓",
    "■ 停止": "■ Stop",
    "流程": "Pipeline",
    "▶ 运行全部": "▶ Run all",
    "▶ 从选中开始": "▶ Run from selected",
    "查看": "Show",
    "上移": "Move up",
    "下移": "Move down",
    "编辑": "Edit",
    "更新所选": "Update selected",
    "这一步没有程序页参数 (导入的命令)，不能载入表单。":
        "This step has no form parameters (an imported command) and cannot be loaded into a form.",
    "已把第 {i} 步载入表单；修改后点“更新所选”替换这一步":
        "Step {i} loaded into its form; after changing it, click “Update selected” to replace the step",
    "第 {i} 步不是 {p}：先选中这一步并点“编辑”，修改后再更新。":
        "Step {i} is not {p}: select the step and click “Edit” first, then update it after changing the form.",
    "已更新第 {i} 步": "Step {i} updated",
    "删除": "Remove",
    "清空": "Clear",
    "导出脚本…": "Export script…",
    "输入预览": "Input preview",
    "程序输出": "Program output",
    "清屏": "Clear",
    "就绪": "Ready",
    "完成": "Finished",
    "上次运行完成：共 {n} 步": "Last run finished: {n} step(s)",
    "上次运行在第 {i}/{n} 步 {t} 停止": "Last run stopped at step {i}/{n} {t}",
    "上次运行在第 {i}/{n} 步 {t} 出错": "Last run failed at step {i}/{n} {t}",
    "上次运行意外结束于第 {i}/{n} 步 {t}": "Last run ended unexpectedly at step {i}/{n} {t}",
    " (结束于 {e})": " (ended {e})",
    "运行中：{n} 个步骤 …": "Running: {n} step(s) …",
    "运行中": "Busy",
    "已有任务在运行，请等待或先停止。": "A job is already running. Wait for it or stop it first.",
    "有程序正在运行，退出将终止它。确定退出吗？": "A program is still running and will be terminated. Quit anyway?",
    "参数错误": "Invalid parameters",
    "参数格式有误：{e}": "Invalid parameter format: {e}",
    "错误": "Error",
    "工作目录不存在：{d}": "Working directory does not exist: {d}",
    "导出": "Export",
    "设置": "Settings",
    # 波函数查看器
    "工具": "Tools",
    "目录": "Directory",
    "刷新": "Refresh",
    "径向波函数文件 (勾选要比较的文件)": "Radial wave function files (check those to compare)",
    "添加其他文件…": "Add other file…",
    "视图": "View",
    "放大字体": "Larger font",
    "缩小字体": "Smaller font",
    "默认字体大小": "Default font size",
    "字体大小 {pt} 磅": "Font size {pt} pt",
    # 能级查看器中的跃迁计算
    "跃迁": "Transitions",
    "计算跃迁 (所选 J)": "Compute (selected J)",
    "计算全部 J (MPI)": "Compute all J (MPI)",
    "只算两个能级所在的 J^π，较快": "Only the J^π symmetries of the two levels; faster",
    "计算两个文件中全部 J^π 之间的跃迁 (用于寿命、分支比)，用 MPI 并行":
        "All J^π symmetries of both files (for lifetimes and branching ratios), in parallel with MPI",
    "计算全部 J 时的 MPI 进程数": "Number of MPI processes when computing all J",
    "默认按物理核数；超线程对 GRASP 帮助不大。":
        "Defaults use physical cores; hyperthreading offers little benefit for GRASP.",
    "进程数": "Processes",
    "需要计算 {k}。": "{k} to be computed.",
    "“所选 J”只算 {j} 这些对称性之间的跃迁；“全部 J”算两个文件中所有能级之间的跃迁，用于寿命和分支比。":
        "\"Selected J\" computes only the transitions between {j}; \"all J\" computes those between all levels of "
        "both files, for lifetimes and branching ratios.",
    "找不到 {p}": "{p} not found",
    "计算目录的路径超过 {n} 个字符，GRASP 的 MPI 版本无法处理": "The path of the calculation directory exceeds {n} "
        "characters, which GRASP's MPI programs cannot handle",
    "，改用串行版本。": "; using the serial programs.",
    "全部 J": "all J",
    "所选 J": "selected J",
    "正在计算 {k} 跃迁 ({w}，{n} 个进程) …": "Computing {k} transitions ({w}, {n} processes) …",
    "停止": "Stop",
    "类型": "Type",
    "规范": "Gauge",
    "另一能级": "Other level",
    "分支比": "Branching ratio",
    "Babushkin (长度)": "Babushkin (length)",
    "Coulomb (速度)": "Coulomb (velocity)",
    "GRASP 程序的输出": "Output of the GRASP programs",
    "鼠标停在能级上显示信息，点击查看组成；从一个能级拖到另一个能级 (或按住 Shift 点击) 计算跃迁，算好的跃迁会画成箭头，右键点箭头可移除；滚轮或拖动空白处上下平移，触控板捏合 (或 Ctrl + 滚轮) 缩放，双击复位。"
    "按住右键 (或 Ctrl + 左键) 拖出一个框可放大到框内的能量范围，单击右键或按 Backspace 回到上一个视图。":
        "Hover a level for details, click it to see its composition; drag from one level to another (or "
        "Shift-click) to compute the transition, which is then drawn as an arrow (right-click an arrow to remove "
        "it); the wheel or dragging empty space scrolls up and down, pinching on a touchpad (or Ctrl + wheel) "
        "zooms, double-click resets. Drag a box with the right button "
        "(or Ctrl + left button) to zoom to its energy range; right-click or Backspace returns to the previous view.",
    "显示选中能级的全部已算跃迁": "Show all computed transitions of the selected level",
    "图上标注的跃迁": "Transitions drawn in the diagram",
    "移除": "Remove",
    "全部清除": "Clear all",
    "已从图上移除 {u} → {l}": "Removed {u} → {l} from the diagram",
    "画出图上标注的跃迁": "Draw the transitions marked in the diagram",
    "箭头上的文字": "Text on arrows",
    "组态缩写…": "Configuration abbreviations…",
    "组态缩写": "Configuration abbreviations",
    "组态": "Configuration",
    "缩写": "Abbreviation",
    "完整组态": "Full configuration",
    "添加": "Add",
    "恢复默认": "Restore defaults",
    "组态中含有右列的全部子壳层 (占据数相同) 时，把它们换成左列的缩写，写在组态最前面；缩写可以留空，表示省略这些子壳层。子壳层多的规则先用。核心轨道 (CSF 文件中的 Core) 已计入组态。":
        "When a configuration contains all subshells of the right column (with the same occupations), they are "
        "replaced by the abbreviation on the left, written first; an empty abbreviation omits them. Rules with "
        "more subshells are applied first. The core orbitals (Core in the CSF file) are part of the configuration.",
    "第 {r} 行的完整组态无法识别 (应写成 1s2.2s2.2p6 这样)": "Row {r}: configuration not understood (write it like 1s2.2s2.2p6)",
    "预览：{a}  →  {b}": "Preview: {a}  →  {b}",
    "不标注": "None",
    "波长 (nm)": "Wavelength (nm)",
    "波长 (Å)": "Wavelength (Å)",
    "从一个能级拖到另一个能级 (或选中一个能级后按住 Shift 点击另一个)，计算两者之间的跃迁。":
        "Drag from one level to another (or select a level and Shift-click another) to compute the transition "
        "between them.",
    "J = 0 → J = 0：单光子跃迁严格禁戒。": "J = 0 → J = 0: single-photon transitions are strictly forbidden.",
    "已算出 {k}。结果保存在 {d}": "{k} computed. Results are kept in {d}",
    "工作目录中没有 isodata：{d}": "No isodata in the working directory: {d}",
    "无法确定与 {f} 对应的径向波函数文件，候选：{c}。请把对应的 .w 文件命名为与混合系数文件同名 (如 {stem}.w)。":
        "Cannot tell which wave function file belongs to {f} (candidates: {c}). Name the matching .w file "
        "like the mixing coefficient file (e.g. {stem}.w).",
    "找不到与 {f} 对应的 {what} 文件": "No {what} file matching {f} found",
    "两个能级来自同一个文件，轨道相同，不需要双正交变换。":
        "Both levels come from the same file and share the orbitals: no biorthogonal transformation needed.",
    "两个能级来自不同的计算，先用 rbiotransform 做双正交变换。":
        "The levels come from different calculations: rbiotransform first performs the biorthogonal "
        "transformation.",
    "使用的文件：{f}": "Files used: {f}",
    "两种规范之比接近 1 说明计算较可靠。": "A ratio of the two gauges close to 1 indicates a reliable calculation.",
    "能级 {l} 还没有算出的跃迁。": "No transitions computed yet for level {l}.",
    "能级 {l} 的已算跃迁 ({n} 条；↓ 衰变到更低能级，↑ 从更低能级激发)。":
        "Computed transitions of level {l} ({n}; ↓ decay to a lower level, ↑ excitation from a lower level).",
    "已算衰变的总速率 {a:.4e} s⁻¹，对应寿命 {tau:.4e} s (只计入已算的跃迁)。":
        "Total computed decay rate {a:.4e} s⁻¹, lifetime {tau:.4e} s (computed transitions only).",
    "跃迁计算完成": "Transition calculation finished",
    "跃迁计算失败，见下方的程序输出": "Transition calculation failed; see the program output below",
    # 能级查看器中的导出
    "能级图 (PNG / SVG / PDF)…": "Level diagram (PNG / SVG / PDF)…",
    "能级数据 (CSV)…": "Level data (CSV)…",
    "当前能级的跃迁 (CSV)…": "Transitions of the current level (CSV)…",
    "这两个文件之间的全部已算跃迁 (CSV)…": "All computed transitions between these two files (CSV)…",
    "保存为 GRASP 跃迁文件 (供 rtabtrans1 / rtabtrans2 使用)…": "Save as GRASP transition file (for rtabtrans1 / rtabtrans2)…",
    "保存为 GRASP 跃迁文件…": "Save as GRASP transition file…",
    "导出能级图": "Export level diagram",
    "导出能级图…": "Export level diagram…",
    "导出能级数据…": "Export level data…",
    "导出跃迁数据…": "Export transition data…",
    "标注组态、谱项 (或 J^π)": "Label configuration and term (or J^π)",
    "标注能量": "Label energies",
    "坐标轴文字": "Axis text",
    "图的高度": "Height",
    "PNG 分辨率": "PNG resolution",
    "范围与屏幕上一致 (先缩放到需要的部分)；宽度按标注自动确定。PDF 嵌入字体，适合论文；SVG 可在 Inkscape 等软件中编辑。":
        "The energy range is the one on screen (zoom in first if needed); the width follows from the labels. PDF "
        "embeds the fonts and suits papers; SVG can be edited in Inkscape and similar programs.",
    "已导出 {p} ({n} 行)": "Exported {p} ({n} rows)",
    "已导出 {p} ({n} 条跃迁)": "Exported {p} ({n} transitions)",
    "无法写入 {p}": "Cannot write {p}",
    "还没有算出的跃迁。": "No transitions computed yet.",
    "所有文件": "All files",
    "Qt {v} 写出的 PDF 文件信息有错，部分阅读器会报错。请升级 PySide6 (≥ 6.8.3)，或导出为 SVG / PNG。":
        "Qt {v} writes a faulty document information dictionary into PDF files, which some readers report as an "
        "error. Upgrade PySide6 (≥ 6.8.3) or export as SVG / PNG.",
    "{k} 只算了部分 J，文件中只有这些跃迁；rtabtrans2 算出的寿命只计入它们。先“计算全部 J”可以得到完整的结果。仍然导出吗？":
        "{k} was computed for some J only, and the file contains only those transitions; lifetimes from rtabtrans2 "
        "include only them. Use \"Compute all J\" first for complete results. Export anyway?",
    "轨道": "Orbitals",
    "全选": "All",
    "全不选": "None",
    "横轴": "x axis",
    "复位缩放": "Reset zoom",
    "导出图片…": "Export image…",
    "导出数据…": "Export data…",
    "滚轮缩放横轴，Shift + 滚轮缩放纵轴，拖动平移，双击复位；鼠标停在曲线上显示数值。":
        "Wheel zooms the x axis, Shift + wheel the y axis, drag to pan, double-click to reset; hover a curve for values.",
    "峰值位置 r (a.u.)": "r of peak (a.u.)",
    "节点 / 应有": "Nodes / expected",
    "归一化": "Norm",
    "网格点数": "Grid points",
    "GRASP 中束缚态的轨道能量记为正值": "GRASP stores orbital energies of bound states as positive numbers",
    "大分量 P(r) 的节点数；谱学轨道应为 n − l − 1": "Nodes of the large component P(r); n − l − 1 for spectroscopic orbitals",
    "{n} 个径向波函数文件": "{n} radial wave function file(s)",
    "此目录中没有径向波函数文件": "No radial wave function files in this directory",
    "{f} 不是 GRASP 径向波函数文件": "{f} is not a GRASP radial wave function file",
    "无法读取：{e}": "Cannot read: {e}",
    "正在读取 {f}… {p}%": "Reading {f}… {p}%",
    "正在读取组成…": "Reading composition…",
    "读入完成": "Loading complete",
    "… 另有 {n} 条": "… {n} more",
    # 能级与组成查看器
    "原子单位 (Hartree)": "atomic units (Hartree)",
    "相对能量 ({u})": "Relative energy ({u})",
    "能量单位": "Energy unit",
    "混合系数文件": "Mixing coefficient files",
    "文件按内容识别，与文件名无关：组态取自 CSF 数和 J^π 都对应的 CSF 文件，LSJ 组成取自能量对应的 jj2lsj 结果。":
        "Files are recognised by their content, not their names: configurations come from the CSF file whose "
        "block sizes and J^π match, LSJ compositions from the jj2lsj result with matching energies.",
    "序号": "Pos",
    "能量": "Energy",
    "总能量 (a.u.)": "Total energy (a.u.)",
    "纯度": "Purity (%)",
    "主要组成": "Leading component",
    "点击能级查看组成": "Click a level to see its composition",
    "只显示权重 ≥": "Show weights ≥",
    "系数": "Coefficient",
    "权重": "Weight",
    "耦合 (中间 J、总 J^π)": "Coupling (intermediate J, total J^π)",
    "LSJ 标记": "LSJ label",
    "jj 耦合 CSF": "jj-coupled CSFs",
    "LSJ 组成": "LSJ composition",
    "{n} 个混合系数文件": "{n} mixing coefficient file(s)",
    "此目录中没有混合系数文件": "No mixing coefficient files in this directory",
    "能量 ({u})": "Energy ({u})",
    "显示 {n} / {m} 个 CSF，权重之和 {w:.3f} %": "Showing {n} of {m} CSFs, total weight {w:.3f} %",
    "组态取自 {f}": "configurations from {f}",
    "LSJ 组成取自 {f}": "LSJ composition from {f}",
    "(找不到对应的 CSF 文件，只显示 CSF 序号)": "(no matching CSF file: CSF numbers only)",
    "没有对应的 jj2lsj 结果 (.lsj.lbl)": "No matching jj2lsj result (.lsj.lbl)",
    "展开或收起 GRASP bin 目录和 MPI 设置": "Show or hide the GRASP bin directory and MPI settings",
    "GRASP bin：{b}　·　{m}": "GRASP bin: {b}  ·  {m}",
    "MPI {n} 进程": "MPI, {n} processes",
    "串行": "serial",
    "流程为空：先在各标签页点击“加入流程”。": "The pipeline is empty: click “Add to pipeline” on a tab first.",
    "导出 shell 脚本": "Export shell script",
    "已导出 {p}": "Exported {p}",
    "保存参数": "Save parameters",
    "载入参数": "Load parameters",
    "参数已保存到 {p}": "Parameters saved to {p}",
    "已载入 {p}": "Loaded {p}",
    "----- {d} ({n} 个文件) -----": "----- {d} ({n} files) -----",
    # 运行
    "找不到程序 {p} (bin 目录: {d})": "Program {p} not found (bin directory: {d})",
    "[输出已写入 {f}]": "[output written to {f}]",
    "{p} 退出码 {rc}": "{p} exited with code {rc}",
    "\n*** 错误: {e}\n": "\n*** Error: {e}\n",
    "\n*** 已停止\n": "\n*** Stopped\n",
    # 通用
    "输出日志文件": "Output log file",
    "空 = 只显示在窗口": "empty = show in window only",
    "态名称": "State name",
    "每块一行": "one line per block",
    "混合系数来自 CI 计算": "Mixing coefficients from CI calculation",
    "初态名称": "Initial state name",
    "末态名称": "Final state name",
    "留空则不复制": "leave empty to skip copy",
    # rnucleus
    "生成核数据文件 isodata。": "Generate the nuclear data file isodata.",
    "原子序数 Z": "Atomic number Z",
    "质量数 A": "Mass number A",
    "0 = 点核模型": "0 = point nucleus",
    "修改默认的核半径 / 表面厚度": "Revise default nuclear radius / skin thickness",
    "  均方根半径 (fm)": "  RMS radius (fm)",
    "  表面厚度 (fm)": "  Skin thickness (fm)",
    "中性原子质量 (amu)": "Mass of neutral atom (amu)",
    "0 = 静止核": "0 = static nucleus",
    "核自旋 I": "Nuclear spin I",
    "磁偶极矩 (核磁子)": "Dipole moment (nuclear magnetons)",
    "电四极矩 (barn)": "Quadrupole moment (barns)",
    # rcsfgenerate
    "生成 CSF 列表 rcsf.out。组态写法如 1s(2,i)2s(2,*)2p(1,*)：c=闭壳, i=不激发, *=活跃, "
    "数字=最小占据。可添加多个列表 (Generate more lists)。":
        "Generate the CSF list rcsf.out. Configurations are written like 1s(2,i)2s(2,*)2p(1,*): "
        "c = closed, i = inactive, * = active, number = minimal occupation. "
        "Several lists can be added (Generate more lists).",
    "轨道排序": "Orbital ordering",
    "默认": "default",
    "反序": "reverse",
    "按对称性": "symmetry",
    "闭壳芯": "Core",
    "无芯": "no core",
    "CSF 列表:": "CSF lists:",
    "+ 添加列表": "+ Add list",
    "− 删除当前列表": "− Remove current list",
    "列表 {n}": "List {n}",
    "参考组态 (每行一个)": "Reference configurations\n(one per line)",
    "活跃轨道集": "Active set",
    "各 l 的最高 n，如 5s,4p,3d": "highest n per l, e.g. 5s,4p,3d",
    "2J 最小值": "2J min",
    "2J 最大值": "2J max",
    "激发数": "Number of excitations",
    "负数: 关联轨道总是双占据": "negative: correlation orbitals doubly occupied",
    "保存 rcsf.out 为": "Save rcsf.out as",
    # rcsfsplit
    "按轨道集把 name.c 拆分为 name<标签>.c 等多个文件。":
        "Split name.c by orbital set into name<label>.c files.",
    "CSF 文件名 (不含 .c)": "CSF file name (without .c)",
    "拆分 (每行: 轨道集  标签)": "Splits (per line: orbital set  label)",
    # rangular
    "计算角系数 (mcp.*)。读取 rcsf.inp。": "Compute angular coefficients (mcp.*). Reads rcsf.inp.",
    "复制此 CSF 文件为 rcsf.inp": "Copy this CSF file to rcsf.inp",
    "留空则直接用现有 rcsf.inp": "leave empty to use existing rcsf.inp",
    # rwfnestimate
    "生成初始径向波函数 rwfn.inp。可先从已有 .w 文件读取部分轨道，其余用 Thomas-Fermi 或屏蔽类氢估计。":
        "Generate initial radial wave functions rwfn.inp. Orbitals can first be read from an "
        "existing .w file; the rest are estimated with Thomas-Fermi or screened hydrogenic functions.",
    "先从已有波函数文件读取 (GRASP92 File)": "First read from an existing wave function file (GRASP92 File)",
    "  波函数文件": "  Wave function file",
    "如 odd2.w，空则为 rwfn.out": "e.g. odd2.w; empty = rwfn.out",
    "  读取的轨道": "  Orbitals to read",
    "* = 全部": "* = all",
    "其余轨道的估计方法": "Method for remaining orbitals",
    "屏蔽类氢 (Screened Hydrogenic)": "Screened Hydrogenic",
    "  轨道": "  Orbitals",
    # rmcdhf
    "自洽场计算 (读取 rcsf.inp, rwfn.inp, mcp.*；输出 rwfn.out, rmix.out)。"
    "每个 J 对称块一行能级号，例如 1,2,3 或 1-3。"
    "NSIC 是首轮 SCF 额外改进自洽性最差的轨道数。默认：EOL 串行为 (NW-NFIX)/4、MPI 为 4+(NW-NFIX)/4，"
    "含变分关联轨道时为 0；AL 为 2+(NW-NFIX)/4 (整数除法)。":
        "Self-consistent field calculation (reads rcsf.inp, rwfn.inp, mcp.*; writes rwfn.out, rmix.out). "
        "One line of level numbers per J block, e.g. 1,2,3 or 1-3. "
        "NSIC is the number of extra improvements of the least self-consistent orbitals in the first SCF cycle. "
        "Default EOL: (NW-NFIX)/4 serial, 4+(NW-NFIX)/4 MPI, or 0 if any varied orbital is a correlation orbital; "
        "AL: 2+(NW-NFIX)/4 (integer division).",
    "各块要优化的能级": "Levels to optimise\nper block",
    "使用内存版本 rmcdhf_mem": "Use the in-memory version rmcdhf_mem",
    "角系数一次读入内存：更快，但更占内存": "angular coefficients kept in memory: faster, but uses more memory",
    "能级权重": "Level weights",
    "标准 (EOL)": "standard (EOL)",
    "等权重": "equal",
    "用户指定": "user",
    "  用户权重": "  User weights",
    "选 9 时填写, 如 1,1,2": "for option 9, e.g. 1,1,2",
    "变分轨道": "Orbitals to vary",
    "如 *  或 3*  或 2s,2p*": "e.g. *  or 3*  or 2s,2p*",
    "谱学轨道 (非关联轨道)": "Spectroscopic orbitals",
    "空 = 无": "empty = none",
    "最大 SCF 迭代次数": "Max SCF cycles",
    "结束后 rsave 保存为": "rsave afterwards as",
    "空 = 不保存": "empty = do not save",
    # rsave
    "把 rcsf.inp / rwfn.out / rmix.out 等保存为 name.c / name.w / name.m ...":
        "Save rcsf.inp / rwfn.out / rmix.out etc. as name.c / name.w / name.m ...",
    # rci
    "组态相互作用计算 (可含 Breit 与 QED)。读取 name.c, name.w，输出 name.cm 等。":
        "Configuration interaction (optionally with Breit and QED). Reads name.c, name.w; writes name.cm etc.",
    "先从此态复制 .c/.w": "First copy .c/.w from state",
    "空 = 不复制": "empty = no copy",
    "包含横向 (Breit) 相互作用": "Include transverse (Breit) interaction",
    "修改横向光子频率": "Modify transverse photon frequencies",
    "  频率缩放因子": "  Frequency scale factor",
    "真空极化 (Vacuum Polarisation)": "Vacuum polarisation",
    "正常质量移动 (NMS)": "Normal mass shift (NMS)",
    "特殊质量移动 (SMS)": "Specific mass shift (SMS)",
    "估计自能 (Self-energy)": "Estimate self-energy",
    "  自能计算的最大 n (≤8)": "  Max n for self-energy (≤8)",
    "各块计算的能级": "Levels per block",
    # jj2lsj
    "把 jj 耦合的混合系数变换到 LSJ 耦合，给出 LSJ 标记。":
        "Transform jj-coupled mixing coefficients to LSJ coupling and produce LSJ labels.",
    "混合系数来自 CI 计算 (.cm)": "Mixing coefficients from CI calculation (.cm)",
    "否则读 .m": "otherwise .m is read",
    "需要唯一标记 (unique labeling)": "Unique labeling",
    "写出 Coupling 需要的文件": "Write files for Coupling",
    "生成 Coupling 文件前备份 LSJ 标记": "Back up LSJ labels before writing Coupling files",
    "备份至 name.lsj.lbl_SAVE；须先运行默认 jj2lsj": "Back up to name.lsj.lbl_SAVE; run default jj2lsj first",
    "Coupling 输出的成分打印下限": "Composition print cutoff for Coupling output",
    "Coupling 能级选择": "Coupling level selection",
    "空 = 全部；否则每行：块号: 能级号，如 2: 1-3": "Empty = all; otherwise block: levels per line, e.g. 2: 1-3",
    "Coupling 能级选择须为每行块号: 能级号": "Coupling levels must be block: levels per line",
    # coupling
    "外部程序 https://github.com/compas/coupling。先在 jj2lsj 勾选写出 Coupling 文件；读取 name.lsj.c、name.lsj.j，输出 "
    "name.coupN.*.lbl 和 name.coupN.sum。":
        "External program https://github.com/compas/coupling. First enable Coupling output in jj2lsj. Reads "
        "name.lsj.c/name.lsj.j; writes name.coupN.*.lbl and name.coupN.sum.",
    "Coupling 态名称须为不含空格的 ASCII 基名，最多 23 个字符":
        "Coupling state name must be an ASCII basename without spaces, at most 23 characters",
    "详细输出": "Detailed output",
    "耦合壳层数": "Number of coupled shells",
    "三壳层": "Three shells",
    "两壳层": "Two shells",
    "单壳层": "One shell",
    "成分打印下限": "Composition print cutoff",
    "重耦合轨道": "Shells for recoupling",
    "逗号分隔，最多 12 个": "Comma-separated, at most 12",
    "结束后采用指定方案的标记": "Use the selected scheme labels afterwards",
    "标记方案": "Label scheme",
    "耦合壳层数必须为 1、2 或 3": "Number of coupled shells must be 1, 2 or 3",
    "成分打印下限必须是 0–1 的数": "Composition print cutoff must be a number from 0 to 1",
    "请给出最多 12 个重耦合轨道，总长不超过 56 个字符": "Provide at most 12 shells for recoupling, at most 56 characters in total",
    "所选标记方案不适用于该耦合壳层数": "The selected label scheme is not available for this shell count",
    "外部程序 {p}：请从 {url} 安装": "External program {p}: install from {url}",
    "先备份 name.lsj.lbl 为 name.lsj.lbl_LSJ": "First back up name.lsj.lbl as name.lsj.lbl_LSJ",
    "采用方案标记前保存当前 LSJ 结果，不覆盖 .lsj.lbl_SAVE":
        "Save the current LSJ result before adopting scheme labels; preserve .lsj.lbl_SAVE",
    # rlevels
    "列出能级。可给多个 .m / .cm 文件。": "List energy levels. Several .m / .cm files may be given.",
    "空格分隔": "space separated",
    # rbiotransform / rtransition
    "跃迁计算前的双正交变换。": "Biorthogonal transformation before transition calculations.",
    "计算跃迁参数 (需先运行 rbiotransform)。": "Compute transition parameters (run rbiotransform first).",
    "跃迁类型": "Transition type",
    "M1 跃迁只计算不同 J 的能级之间": "M1 transitions only between levels with different J",
    "E2 跃迁只计算不同 J 的能级之间": "E2 transitions only between levels with different J",
    "跃迁类型含 M1 时询问": "asked when the types include M1",
    "跃迁类型含 E2 时询问": "asked when the types include E2",
    "屏蔽类氢，关联轨道增加 Z (custom Z)": "Screened Hydrogenic, increased Z for correlation orbitals (custom Z)",
    "  关联轨道的 Z 增加量": "  Increase in Z for correlation orbitals",
    "仅方法 4": "method 4 only",
    "如 E1  或 E1,M2": "e.g. E1  or E1,M2",
    # 自定义
    "自定义": "Custom",
    "运行任意 GRASP 程序，手动填写交互输入（每行回答一个提问）。":
        "Run any GRASP program with hand-written interactive input (one answer per line).",
    "程序": "Program",
    "命令行参数": "Arguments",
    "输入 (stdin)": "Input (stdin)",
    # MPI / 性能
    "MPI 进程数": "MPI processes",
    "mpirun 命令": "mpirun command",
    "降低计算进程优先级 (nice)": "Lower priority of calculations (nice)",
    "进程数 > 1 时 rangular / rmcdhf / rci / rbiotransform / rtransition 改用 *_mpi 版本；"
    "rangular 与 rmcdhf 须使用相同的进程数和 MPI_TMP。":
        "With more than 1 process, rangular / rmcdhf / rci / rbiotransform / rtransition use their *_mpi "
        "versions; rangular and rmcdhf must use the same number of processes and MPI_TMP.",
    "（只显示最后 {n} 行，完整输出请设置日志文件）": "(last {n} lines only; set a log file for the full output)",
    "找不到 rangular 生成的角系数文件 (mcp.*)：请先运行 rangular。":
        "No angular coefficient files (mcp.*) from rangular were found: run rangular first.",
    "角系数文件是串行 rangular 生成的 (工作目录中的 mcp.*)，rmcdhf 不能用 {np} 个 MPI 进程运行："
    "请把 MPI 进程数设为 1，或用 {np} 个进程重新运行 rangular。":
        "The angular coefficients were generated by serial rangular (mcp.* in the working directory), so "
        "rmcdhf cannot run on {np} MPI processes: set the number of MPI processes to 1, or rerun rangular "
        "on {np} processes.",
    "角系数文件是 rangular 用 {k} 个 MPI 进程生成的 (在 {d} 中)，rmcdhf 必须使用相同的进程数："
    "请把 MPI 进程数设为 {k}，或用 {np} 个进程重新运行 rangular。":
        "The angular coefficients were generated by rangular on {k} MPI processes (in {d}); rmcdhf must use "
        "the same number of processes: set the number of MPI processes to {k}, or rerun rangular on {np} "
        "process(es).",
    "rcsf.inp 比角系数文件新：请先对当前的 rcsf.inp 运行 rangular。":
        "rcsf.inp is newer than the angular coefficient files: run rangular on the current rcsf.inp first.",
    # Slurm
    "导出 Slurm 作业脚本…": "Export Slurm job script…",
    "Slurm 作业脚本": "Slurm job script",
    "把当前流程导出为可直接 sbatch 提交的作业脚本。把脚本和工作目录中的输入文件 (isodata, *.c, *.w …) "
    "一起上传到服务器的同一目录，然后运行 sbatch 脚本名。":
        "Export the current pipeline as a job script for sbatch. Upload the script together with the input "
        "files of the working directory (isodata, *.c, *.w …) to one directory on the server, then run "
        "sbatch <script>.",
    "作业名": "Job name",
    "分区 (partition)": "Partition",
    "空 = 默认分区": "empty = default partition",
    "节点数": "Nodes",
    "每节点 MPI 进程数": "MPI tasks per node",
    "时间限制": "Time limit",
    "如 2-00:00:00 (2 天)": "e.g. 2-00:00:00 (2 days)",
    "内存": "Memory",
    "如 64G；空 = 不指定": "e.g. 64G; empty = not set",
    "服务器上的 GRASP bin 目录": "GRASP bin directory on server",
    "加载模块等命令": "Environment setup\n(module load …)",
    "每行一条，如 module load openmpi": "one per line, e.g. module load openmpi",
    "MPI 启动命令": "MPI launch command",
    "MPI 临时目录 (MPI_TMP)": "MPI scratch dir (MPI_TMP)",
    "邮件通知地址": "Notification e-mail",
    "空 = 不通知": "empty = no e-mail",
    "其他 #SBATCH 选项": "Other #SBATCH options",
    "每行一条，如 --account=xxx": "one per line, e.g. --account=xxx",
    "导出…": "Export…",
    "取消": "Cancel",
    # 程序列表分组
    "准备": "Setup",
    "自洽场与 CI": "SCF & CI",
    "性质与跃迁": "Properties & transitions",
    "结果与工具": "Results & tools",
    # 态组 / 工作目录记录
    "态组": "State group",
    "复制到 {g}（替换名称）": "Copy to {g} (rename files)",
    "标签上带组名的程序页按态组分别保存参数，其余程序页两组共用。":
        "Tabs labelled with the group keep separate parameters per group; the other tabs are shared.",
    # rhfs / rhfszeeman95 / ris4
    "计算超精细结构常数 A、B 和朗德 g 因子。读取 isodata (核自旋 I、磁偶极矩、电四极矩在 rnucleus 中设置)、"
    "name.c、name.w、name.(c)m；输出 name.(c)h、name.(c)hoffd。":
        "Compute hyperfine constants A, B and Landé g factors. Reads isodata (nuclear spin I, dipole and "
        "quadrupole moments are set in rnucleus), name.c, name.w, name.(c)m; writes name.(c)h, name.(c)hoffd.",
    "结束后运行 rhfs_lsj，用 LSJ 标记输出结果": "Then run rhfs_lsj to print the results with LSJ labels",
    "需先运行 jj2lsj；输出 name.(c)hlsj": "needs jj2lsj first; writes name.(c)hlsj",
    "  按能量排序": "  Sort by energy",
    "默认设置下计算超精细和塞曼矩阵元。读取 isodata、name.c、name.w、name.(c)m；"
    "输出 name.(c)h，勾选非对角时另写 name.(c)gjhfs。不同 J 块须在同一个态文件中，使用同一组轨道；不做双正交变换。":
        "Compute hyperfine and Zeeman matrix elements with default settings. Reads isodata, name.c, name.w, "
        "name.(c)m; writes name.(c)h and, with off-diagonal elements, name.(c)gjhfs. Different J blocks must "
        "be in the same state file with one orbital set; no biorthogonal transformation is performed.",
    "与 rhfs 共用 name.(c)h，格式不同，后运行的会覆盖先运行的。另存只复制本次常数结果，不备份已有文件。":
        "Shares name.(c)h with rhfs, with a different format; the later run overwrites the earlier output. "
        "Saving a copy preserves this run's constants only; it does not back up existing files.",
    "计算非对角矩阵元": "Compute off-diagonal matrix elements",
    "常数结果 name.(c)h 另存为": "Save a copy of constants name.(c)h as",
    "rhfszeeman95 态名称须为不含空格的 ASCII 基名，最多 23 个字符":
        "rhfszeeman95 state name must be an ASCII basename without spaces, at most 23 characters",
    "计算同位素移位：正常 / 特殊质量移位参数和场移电子因子。读取 isodata、name.c、name.w、name.(c)m；"
    "输出 name.(c)i。":
        "Compute isotope shifts: normal / specific mass shift parameters and field shift electronic factors. "
        "Reads isodata, name.c, name.w, name.(c)m; writes name.(c)i.",
    "计算高阶场移电子因子": "Compute higher-order field shift electronic factors",
    "保存角系数 (name.IOB / name.ITB)，以后重复计算时直接读取":
        "Save angular coefficients (name.IOB / name.ITB) for later runs",
    "这两个文件已存在时程序不询问": "not asked when these files already exist",
    # rcsfinteract / rtablevels
    "从完整 CSF 列表中只保留与多参考组态有相互作用的 CSF。读取 rcsfmr.inp (多参考) 和 rcsf.inp (完整列表)，"
    "输出 rcsf.out。":
        "Keep only the CSFs of the full list that interact with the multireference. Reads rcsfmr.inp "
        "(multireference) and rcsf.inp (full list); writes rcsf.out.",
    "多参考 CSF 文件 → rcsfmr.inp": "Multireference CSF file → rcsfmr.inp",
    "留空则直接用现有 rcsfmr.inp": "leave empty to use existing rcsfmr.inp",
    "完整 CSF 文件 → rcsf.inp": "Full CSF file → rcsf.inp",
    "按哪种哈密顿量判断相互作用": "Interaction through",
    "结果 rcsf.out 保存为": "Save the result rcsf.out as",
    "如 odd.c；与完整列表同名时覆盖原列表；留空则不复制":
        "e.g. odd.c; the same name as the full list overwrites it; empty = no copy",
    "把 rlevels 生成的能级文件整理成 LaTeX 和 ASCII 能级表 (energytablelatex.tex、energytableascii.txt)。"
    "每个文件占表中一列，能级标记和顺序以最后一个文件为准。":
        "Make LaTeX and ASCII energy tables (energytablelatex.tex, energytableascii.txt) from energy files "
        "written by rlevels. Each file fills one column; labels and order follow the last file.",
    "标签开头跳过的字符数": "Characters to skip at the start of labels",
    "如标签以闭壳芯 1s(2). 开头，跳过 6 个字符": "e.g. skip 6 when labels start with the core 1s(2).",
    "能级文件": "Energy files",
    "每行一个，按活跃集从小到大；在 rlevels 页用“输出日志文件”生成":
        "one per line, smallest active set first; write them with the output log file of the rlevels tab",
    # rmixextract / rmixaccumulate / rtabhfs / rasfsplit
    "按混合系数绝对值提取 CSF。读取 name.c、name.(c)m；输出 rcsf.out 和 name.rmx。":
        "Extract CSFs by absolute mixing coefficient. Reads name.c and name.(c)m; writes rcsf.out and "
        "name.rmx.",
    "按混合系数平方的累积权重压缩 CSF。读取 name.c、name.(c)m；输出 rcsf.out。":
        "Condense CSFs by accumulated squared mixing coefficients. Reads name.c and name.(c)m; writes "
        "rcsf.out.",
    "提取方式": "Extraction scope",
    "各能级分别提取": "Each state separately",
    "所有能级联合提取": "Across all states",
    "混合系数绝对值下限": "Minimum absolute mixing coefficient",
    "按混合系数排序 CSF": "Sort CSFs by mixing coefficient",
    "保留的累积权重 (0–1)": "Retained accumulated weight (0–1)",
    "读取 rhfs_lsj 生成的 name.(c)hlsj，输出超精细常数表 hfs.tex。此版本只支持 HFS 数据。":
        "Reads name.(c)hlsj from rhfs_lsj and writes hfs.tex. This version supports HFS data only.",
    "超精细常数文件": "Hyperfine constant files",
    "每行一个，最多 20 个": "One per line, at most 20",
    "态名称须为 1–80 个英文字母、数字、下划线、点或短横线，且不能以点或短横线开头":
        "State name must have 1–80 ASCII letters, digits, underscores, dots or hyphens, and start with a "
        "letter, digit or underscore",
    "截断值必须是 0 到 1 之间的数": "Cutoff must be a number between 0 and 1",
    "提取方式必须为 0 或 1": "Extraction scope must be 0 or 1",
    "请给出 1–20 个文件名，每个不超过 80 个字符": "Provide 1–20 filenames, each at most 80 characters",
    "跳过字符数必须是 0–63 的整数": "Characters to skip must be an integer from 0 to 63",
    "按 J 和宇称拆分 name.c、可用的 name.m / name.cm，并复制 name.w，输出 name_even1.* / name_odd1.* "
    "等。前提：各块必须使用同一轨道集，否则拆出的 .w 没有意义。":
        "Split name.c and available name.m / name.cm by J and parity, and copy name.w to name_even1.* / "
        "name_odd1.* etc. Prerequisite: all blocks must share an orbital set; otherwise the split .w files "
        "are meaningless.",
    # rtabtrans1 / rtabtrans2
    "为 rtabtrans2 生成能级标记文件 energylabel.latex / energylabel.ascii。读取各混合系数文件及其 jj2lsj 标记 "
    "(name.lsj.lbl)。":
        "Write the energy label file energylabel.latex / energylabel.ascii for rtabtrans2. Reads the mixing "
        "coefficient files and their jj2lsj labels (name.lsj.lbl).",
    "混合系数文件 (.m / .cm)": "Mixing coefficient files (.m / .cm)",
    "每行一个，与计算跃迁的波函数对应；需先运行 jj2lsj": "one per line, the wave functions of the transitions; run jj2lsj first",
    "输出格式": "Output format",
    "能级标记文件格式": "Energy label file format",
    "与 rtabtrans1 的输出格式一致": "the format written by rtabtrans1",
    "由 rtransition 的跃迁数据和 rtabtrans1 的能级标记生成跃迁表、寿命表 (transitiontable、lifetimetable 的 .tex / .txt) "
    "和 scatterplot.m。":
        "Make transition and lifetime tables (transitiontable, lifetimetable as .tex / .txt) and scatterplot.m from "
        "the transition data of rtransition and the energy labels of rtabtrans1.",
    "注意：跃迁文件里每种跃迁类型都要至少有一条跃迁，否则 rtabtrans2 会报 End of file 错误 "
    "(例如 odd–even 之间没有 M1 / E2 跃迁时，rtransition 只算 E1)。":
        "Note: every transition type in the file needs at least one transition, otherwise rtabtrans2 stops with "
        "End of file (e.g. compute only E1 in rtransition when there are no M1 / E2 transitions between odd and "
        "even).",
    "跃迁数据文件": "Transition data file",
    "rtransition 的 .t / .ct，可把多个文件合并成一个": "a .t / .ct file from rtransition; several may be concatenated",
    "  离子名称 (表格左列)": "  Ion name (left column)",
    "仅 ASCII 格式，如 Fe X": "ASCII format only, e.g. Fe X",
    "A 值打印下限": "Cut-off for printing A values",
    "跃迁 A 值占上能级总 A 值的最小比例": "Minimum fraction of the upper level's total A value",
    "跃迁数据已按波长排序": "Transition data sorted by wavelength",
    "波长小数位数 (1–6)": "Decimals for wavelengths (1–6)",
    "当前态组：{g}": "Current group: {g}",
    "已把 {a} 组的参数复制到 {b} 组 (名称中的 {a} 已替换为 {b})；请检查 {b} 的参考组态、2J 范围和能级。":
        "Copied the {a} parameters to {b} ({a} replaced by {b} in names); check the reference configurations, "
        "2J range and levels of {b}.",
    "确定用 {a} 组的参数覆盖 {b} 组吗？": "Overwrite the {b} parameters with those of {a}?",
    "已载入此目录上次的记录": "Loaded the last session of this directory",
    "此目录还没有记录，沿用当前参数": "No session in this directory yet; keeping the current parameters",
    # 示例
    "导入示例…": "Import example…",
    "导入示例": "Import example",
    "导入到目录": "Import into directory",
    "导入": "Import",
    "预计时间": "Estimated time",
    "{n} 步": "{n} steps",
    "用到的程序": "Programs used",
    "在 bin 目录和 PATH 中找不到": "Not found in the bin directory or PATH",
    "流程运行到这些程序时会出错。": "The pipeline will stop with an error when it reaches them.",
    "参考结果": "Reference results",
    "{n} 个能级": "{n} level(s)",
    "最低": "lowest",
    "找不到示例文件 ({p})": "No example files found ({p})",
    "目录不是空的：{p}": "The directory is not empty: {p}",
    # 主窗口的三个标签页、单独的窗口
    "计算": "Calculation",
    "径向波函数": "Radial wave functions",
    "能级图": "Levels",
    "在新窗口中打开": "Open in new window",
    "在新窗口中打开{t}": "Open in new window: {t}",
    "在单独的窗口中打开当前的查看器，可以查看其他目录或与计算页并排":
        "Open the current viewer in a separate window, e.g. for another directory or next to the calculation",
    "最近": "Recent",
    "最近打开的目录": "Recently opened directories",
    "最近的目录": "Recent directories",
    "最近的工作目录": "Recent working directories",
    "打开工作目录…": "Open working directory…",
    "打开其他目录…": "Open another directory…",
    "打开目录…": "Open directory…",
    "关闭": "Close",
    "窗口": "Window",
    "(无)": "(none)",
    "找不到目录 {p}": "Directory not found: {p}",
    "无法识别 {p}：不是 GRASP 的混合系数文件或径向波函数文件":
        "Cannot open {p}: not a GRASP mixing coefficient file or radial wave function file",
    """用法：
  grasp_gui [目录]           打开主界面 (默认为上次的工作目录)
  grasp_gui levels [目录]    打开主界面的能级图页 (默认为当前目录)
  grasp_gui wfn [目录]       打开主界面的径向波函数页 (默认为当前目录)
  grasp_gui 文件             按文件内容打开对应的页并只勾选这个文件：
                             混合系数文件 (.m、.cm) → 能级图，径向波函数文件 (.w) → 径向波函数
  grasp_gui check|script|steps|reference …   共享配置的命令行工具 (不打开窗口)，见 grasp_gui check --help
  选项 --lang zh|en          界面语言

以 grasp_levels 或 grasp_wfn 为名的符号链接直接打开对应的页，例如：
  ln -s /路径/grasp_gui ~/.local/bin/grasp_levels
  ln -s /路径/grasp_gui ~/.local/bin/grasp_wfn""": """Usage:
  grasp_gui [DIR]            open the main window (default: the last working directory)
  grasp_gui levels [DIR]     open the main window on the level diagram (default: current directory)
  grasp_gui wfn [DIR]        open the main window on the radial wave functions (default: current directory)
  grasp_gui FILE             open the page for the file's contents with only this file checked:
                             mixing coefficient file (.m, .cm) -> levels, radial wave functions (.w) -> wfn
  grasp_gui check|script|steps|reference …   command-line tools for shared configurations (no window), see grasp_gui check --help
  option --lang zh|en        interface language

A symbolic link named grasp_levels or grasp_wfn opens that page directly, e.g.
  ln -s /path/to/grasp_gui ~/.local/bin/grasp_levels
  ln -s /path/to/grasp_gui ~/.local/bin/grasp_wfn""",
    "操作提示": "Tips",
    # 共享配置的命令行工具
    """用法：
  grasp_gui check 配置.json                   检查配置：列出警告和自定义命令，有错误时返回 1
  grasp_gui script 配置.json [选项]           输出按配置在当前目录运行整个流程的 shell 脚本
      --bindir 目录                           GRASP 程序目录 (默认同界面)
      --mpi "mpirun -np 4"                    有并行版本的程序用 <程序>_mpi 运行
  grasp_gui steps                             输出各程序页的参数说明 (Markdown)
  grasp_gui reference 文件.cm …               输出混合系数文件的能级能量 (配置中的 reference，JSON)
不需要图形界面；源码中也可以用 python3 grasp_config.py 代替 grasp_gui。""": """Usage:
  grasp_gui check CONFIG.json                 check a configuration: warnings and custom commands; exit code 1 on errors
  grasp_gui script CONFIG.json [options]      print a shell script that runs the whole pipeline in the current directory
      --bindir DIR                            GRASP bin directory (default: as in the GUI)
      --mpi "mpirun -np 4"                    run programs that have a parallel version as <prog>_mpi
  grasp_gui steps                             print the reference of all program tabs and fields (Markdown)
  grasp_gui reference FILE.cm …               print the level energies of mixing coefficient files (the "reference" of a configuration, JSON)
No window is opened; from the sources, python3 grasp_config.py can be used instead of grasp_gui.""",
    "错误：{e}": "Error: {e}",
    "警告：{w}": "Warning: {w}",
    "提示：{w}": "Note: {w}",
    "{n} 个计算步骤，所需程序：{p}": "{n} steps, programs needed: {p}",
    "第 {i} 步是自定义命令：": "Step {i} is a custom command:",
    "配置可以导入": "The configuration can be imported",
    "组态 (每行一个)": "configurations (one per line)",
    # 后台运行
    "*** 后台运行在第 {i} 步 {t} 出错": "*** The background run failed at step {i} {t}",
    "*** 后台运行完成": "*** The background run finished",
    "*** 后台运行已停止 (第 {i} 步 {t})": "*** The background run was stopped (step {i} {t})",
    "*** 后台运行进程意外结束": "*** The background run process ended unexpectedly",
    "*** 后台运行进程意外结束 (第 {i} 步 {t})": "*** The background run process ended unexpectedly (step {i} {t})",
    "[上次在后台运行的计算，{w}，结束于 {e}]": "[Last background run, {w}, ended {e}]",
    "[原目录 {d} 的计算在后台继续；再次打开该目录即可接上]": "[The run in {d} continues in the background; open that directory again to reattach]",
    "[接上后台运行的计算，{w}]": "[Attached to the background run, {w}]",
    "[此目录的计算正在 {h} 上运行，{w}；以下输出读自 {f}]": "[This directory's run is running on {h}, {w}; output below is read from {f}]",
    "开始于 {t}": "started {t}",
    "无法启动后台运行进程": "Could not start the background run process",
    "此目录的计算正在 {h} 上运行 (开始于 {t})。仍要在本机开始新的计算吗？": "A run in this directory is running on {h} (started {t}). Start a new run on this machine anyway?",
    "计算在 {h} 上运行 (进程 {p})，只能在那台机器上停止。": "The run is on {h} (process {p}); it can only be stopped on that machine.",
    "运行": "Run",
    "运行中：第 {i}/{n} 步 {t}": "Running: step {i}/{n} {t}",
    "，本步 {d}": ", this step {d}",
    "，最后输出于 {d} 前": ", last output {d} ago",
    "此目录已有计算在运行": "A run is already in progress in this directory",
    "停止计算并退出": "Stop the run and quit",
    "后台继续": "Continue in background",
    "注意：本机的 systemd 设置 (KillUserProcesses=yes) 会在这次登录结束 (ssh 断开或退出登录) 时结束其中的全部进程，包括后台计算。解决办法见 docs/INTERFACE.md 的“后台运行”一节。":
        "Note: this machine's systemd setting (KillUserProcesses=yes) ends all processes of this login when it ends (ssh disconnected or logged out), including background runs. See \"Background runs\" in docs/INTERFACE.md for remedies.",
    "能级图中正在进行的跃迁计算会结束。": "Transition calculations in progress in the level diagram will be stopped.",
    "计算正在运行。可以让它在后台继续，以后再打开这个工作目录即可接上并查看结果。": "A run is in progress. It can continue in the background; open this working directory again later to reattach and see the results.",
    # 工作目录的配置更新与清理
    "附带输入清单的格式有误": "Invalid attached-input list",
    "附带输入清单已改变，请重新预览": "Attached-input list has changed; preview again",
    "将删除 {n} 项，共 {size}": "Delete {n} items, {size} in total",
    "只恢复流程和参数，计算结果与附带文件不变。当前记录也会备份；连续恢复两次会回到恢复前的状态。确定恢复吗？":
        "Restore pipeline and parameters only, retaining results and attached files. The current record is also backed up; restoring twice returns to the state before restoring. Restore?",
    "工作目录记录不能是符号链接": "The working-directory record cannot be a symbolic link",
    "工作目录记录的格式有误": "Invalid working-directory record",
    "前面 {n} 步不变，新增 {m} 步": "First {n} steps unchanged; {m} steps added",
    "程序页参数的其他改动：": "Other program-form changes:",
    "现有项目不是小型普通文本文件": "The existing item is not a small regular text file",
    "工作目录记录已改变，请重新预览": "The working-directory record has changed; preview again",
    "没有可恢复的工作目录记录": "No working-directory record is available to restore",
    "拒绝清空系统目录或家目录：{p}": "Cannot clear a system directory or home directory: {p}",
    "此目录没有工作目录记录，必须另行确认": "This directory has no working-directory record; separate confirmation is required",
    "请输入目录名确认：{name}": "Enter the directory name to confirm: {name}",
    "删除列表有误，请重新预览": "Invalid deletion list; preview again",
    "移动：第 {old} 步 → 第 {new} 步": "Moved: step {old} → step {new}",
    "删除：原第 {n} 步 {step}": "Removed: former step {n} {step}",
    "新增：第 {n} 步 {step}": "Added: step {n} {step}",
    "程序页 {step} ({group})": "Program form {step} ({group})",
    "流程和参数没有变化": "Pipeline and parameters unchanged",
    "附带文件名重复：{name}": "Duplicate attached file name: {name}",
    "附带文件已改变，请重新预览：{name}": "Attached file has changed; preview again: {name}",
    "不能用附带文件覆盖目录：{name}": "Cannot overwrite a directory with an attached file: {name}",
    "目录内容已改变，请重新预览": "Directory contents have changed; preview again",
    "原第 {a}–{b} 步保持不变，现为第 {c}–{d} 步": "Former steps {a}–{b} unchanged, now steps {c}–{d}",
    "修改：第 {n} 步 {step}": "Changed: step {n} {step}",
    "复制摘要": "Copy summary",
    "更新并运行新增的步骤": "Update and run added steps",
    "永久删除所选项目": "Permanently delete selected items",
    "更新当前工作目录": "Update current working directory",
    "新增": "New",
    "内容相同": "Identical",
    "冲突（默认保留）": "Conflict (keep by default)",
    "覆盖已有输入时请先更新，再核对需要重算的步骤": "When overwriting existing inputs, update first and review which steps need recomputing",
    "只更新": "Update only",
    "清空工作目录": "Clear working directory",
    "全部删除（默认只删除计算结果）": "Delete everything (default: results only)",
    "输入清单未知，未自动勾选普通文件；请选择要删除的计算结果。": "Input list unknown: regular files are not selected automatically. Select the results to delete.",
    "已恢复上一次的工作目录记录": "Restored the previous working-directory record",
    "已永久删除所选项目": "Permanently deleted the selected items",
    "附带文件：冲突默认保留原文件，勾选才覆盖": "Attached files: keep conflicts by default; check to overwrite",
    "永久删除，不能恢复。选中的子目录及其中的内容将一并删除；符号链接只删除链接本身。": "Permanent deletion; cannot be restored. Selected directories include their contents. Symbolic links are only unlinked.",
    "左侧：可删除项目（可取消勾选）；右侧：保留项目": "Left: items to delete (uncheck to keep); right: retained items",
    "全部删除时请输入目录名：{name}": "To delete everything, enter the directory name: {name}",
    "已更新当前目录；建议从第 {n} 步开始核对和重算": "Directory updated; review and recompute from step {n}",
    "已更新当前目录；原有结果文件保留": "Directory updated; existing result files retained",
    "更新已完成，但临时备份清理失败：{e}": "Update completed, but temporary backup cleanup failed: {e}",
    "恢复上一次的工作目录记录": "Restore previous working-directory record",
    "清理未完成，部分项目可能已删除：{e}": "Cleanup incomplete; some items may already have been deleted: {e}",
    "原文件": "Existing file",
    "新配置": "New configuration",
    "用配置更新当前目录…": "Update current directory from configuration…",
    "清空工作目录…": "Clear working directory…",
    "此目录没有工作目录记录。仍要查看并选择待删除内容吗？": "This directory has no working-directory record. Still inspect and select items to delete?",
    # 共享配置
    "导入配置文件…": "Import configuration file…",
    "导出配置…": "Export configuration…",
    "导入配置": "Import configuration",
    "导出配置": "Export configuration",
    "打开配置文件…": "Open configuration file…",
    "从剪贴板读入": "From clipboard",
    "剪贴板": "Clipboard",
    "复制到剪贴板": "Copy to clipboard",
    "复制配置到剪贴板…": "Copy configuration to clipboard…",
    "保存为文件…": "Save to file…",
    "无法读取剪贴板：{e}\n内容开头：{text}": "Cannot read clipboard: {e}\nText starts with: {text}",
    "配置文本不是有效的 JSON：{e}": "Configuration text is not valid JSON: {e}",
    "配置文本太大 (最多 {n} MB)": "Configuration text is too large (at most {n} MB)",
    "配置太大，无法复制到剪贴板；请保存为文件。": "Configuration too large to copy; please save it to a file.",
    "已复制配置到剪贴板 ({n} 步)": "Copied configuration to clipboard ({n} steps)",
    "配置文件 (*.json)": "Configuration files (*.json)",
    "(无标题)": "(untitled)",
    "随程序提供的示例": "Example included with the program",
    "作者": "Author",
    "日期": "Date",
    "程序包": "Package",
    "导出自": "Exported by",
    "兼容性": "Compatibility",
    "自定义命令": "Custom commands",
    "可以运行任意程序；导入他人的配置前请确认这些命令是安全的": "they can run any program; make sure they are safe before importing someone else's configuration",
    "附带文件": "Attached files",
    "导入时写入工作目录": "written to the working directory on import",
    "配置作者算出的能量，可与自己的结果对照": "energies computed by the author of the configuration, to compare with your results",
    "这个配置含有 {n} 条自定义命令 (见右侧)，运行流程时会执行它们。确定导入吗？": "This configuration contains {n} custom command(s) (see on the right), which are executed when the pipeline runs. Import it?",
    "配置只包含各程序的参数和计算流程 (以及少量附带的输入文件)，不含计算结果。导入到一个空目录后，点“▶ 运行全部”即可从头计算；各程序页显示该程序在流程中最后一次使用的参数。": "A configuration contains only the parameters of the programs and the pipeline (and possibly a few small input files), no results. Import it into an empty directory and press “▶ Run all” to compute from scratch; each program tab shows the parameters of the last use of that program in the pipeline.",
    "已导入“{t}”到 {p}；点“▶ 运行全部”开始计算": "Imported “{t}” into {p}; press “▶ Run all” to start",
    "已导出配置 {p} ({n} 步)": "Exported configuration {p} ({n} steps)",
    "配置保存当前流程中各步的参数，不含计算结果，也不含 GRASP bin 目录、MPI 进程数等与机器有关的设置。别人导入后可以从头重复这个计算。": "The configuration stores the parameters of the steps of the current pipeline — no results, and no machine-specific settings such as the GRASP bin directory or the number of MPI processes. Others can import it and repeat the calculation from scratch.",
    "标题": "Title",
    "说明": "Description",
    "计算的内容和目的": "what is computed and why",
    "可选": "optional",
    "可选，如“约 10 分钟”": "optional, e.g. “about 10 minutes”",
    "参考结果 (勾选的混合系数文件中的能级能量)": "Reference results (level energies of the checked mixing coefficient files)",
    "附带文件 (界面不能生成的小文本输入文件，每个最多 {n} MB)": "Attached files (small text input files the GUI cannot generate, at most {n} MB each)",
    "添加…": "Add…",
    "提示": "Notes",
    "没有发现可移植性问题。": "No portability problems found.",
    "请填写标题。": "Please enter a title.",
    "不是 GRASP GUI 的配置文件": "Not a GRASP GUI configuration file",
    "配置文件的版本号有误": "Invalid version number in the configuration file",
    "配置文件的格式 (版本 {v}) 比本程序新，请更新 GRASP GUI": "The configuration file format (version {v}) is newer than this program; please update GRASP GUI",
    "配置使用的程序包 {p} 本程序不支持 (支持：{supported})": "The configuration uses the package {p}, which this program does not support (supported: {supported})",
    "配置中没有计算步骤": "The configuration has no steps",
    "第 {i} 步的格式有误": "Step {i} is malformed",
    "第 {i} 步的程序页 {step} 不存在": "Step {i}: unknown program tab {step}",
    "第 {i} 步的态组 {g} 不存在": "Step {i}: unknown state group {g}",
    "第 {i} 步 ({step}) 中不认识的参数已忽略：{k}": "Step {i} ({step}): unknown parameters ignored: {k}",
    "第 {i} 步 ({step}) 缺少的参数用默认值：{k}": "Step {i} ({step}): missing parameters set to defaults: {k}",
    "附带文件 {f} 的名称或内容有误": "Invalid name or content of the attached file {f}",
    "附带文件太大 ({f})": "Attached files too large ({f})",
    "无法读取 {p}：{e}": "Cannot read {p}: {e}",
    "{f} 太大 (附带文件最多 {n} MB)": "{f} is too large (attached files: at most {n} MB)",
    "{f} 不是文本文件": "{f} is not a text file",
    "第 {steps} 步由旧版本加入流程，没有记录表单参数，只能按原样的命令导出；在程序页中重新“加入流程”可以改为保存参数。": "Steps {steps} were added to the pipeline by an older version without their form parameters and are exported as plain commands; add them to the pipeline again from the program tab to export their parameters.",
    "第 {i} 步含有绝对路径，在别的机器上可能不存在": "Step {i} contains an absolute path, which may not exist on other machines",
    "第 {steps} 步是自定义命令，导入的人会看到这些命令并确认": "Steps {steps} are custom commands; whoever imports the configuration is shown them for confirmation",
    # GRASPG
    "GRASPG 是 GRASP2018 基于组态函数生成器 (CSFG) 的扩展，程序名带 _csfg，装在同一个 bin 目录中；大规模组态展开时计算快得多。": "GRASPG extends GRASP2018 with configuration state function generators (CSFGs); its programs end in _csfg and are installed in the same bin directory. It is much faster for large expansions.",
    "GRASP bin 目录中没有 {p}：{pkg} 似乎没有安装 (需要装在 GRASP2018 中，见 compas/graspg)": "{p} is not in the GRASP bin directory: {pkg} does not seem to be installed (it is installed into GRASP2018, see compas/graspg)",
    "程序包：{p}": "Package: {p}",
    "第 {i} 步的程序页 {step} 不属于程序包 {p}": "Step {i}: the program tab {step} does not belong to the package {p}",
    "{p} 报告了错误 (退出码为 0)，详见输出": "{p} reported an error (with exit code 0), see the output",
    "rcsfg.inp 比角系数文件新：请先对当前的 rcsfg.inp 运行 rangular_csfg。": "rcsfg.inp is newer than the angular coefficient files: run rangular_csfg on the current rcsfg.inp first.",
    "找不到 rangular_csfg 生成的角系数文件 (在 {d} 中)：请先运行 rangular_csfg。": "No angular coefficient files from rangular_csfg (in {d}): run rangular_csfg first.",
    "角系数文件是 rangular_csfg 用 {k} 个 MPI 进程生成的 (在 {d} 中)，rmcdhf_csfg 必须使用相同的进程数：请把 MPI 进程数设为 {k}，或用 {np} 个进程重新运行 rangular_csfg。": "The angular coefficient files were made by rangular_csfg with {k} MPI processes (in {d}); rmcdhf_csfg must use the same number: set the MPI processes to {k}, or run rangular_csfg again with {np} processes.",
    "生成 CSFG 列表 rcsfg.out 和标记轨道文件 rlabel.out。组态写法与 rcsfgenerate 相同。": "Generates the CSFG list rcsfg.out and the labelling-orbital file rlabel.out. Configurations are written as in rcsfgenerate.",
    "先给出激发数为 0 的列表 (多参考组态)：它们的活跃轨道是“标记轨道”。再添加激发列表 (见作者的示例 graspgtest)。标记轨道以外的关联轨道由 CSFG 合并处理，GRASPG 因此省时；没有激发数为 0 的列表时所有轨道都是标记轨道，CSFG 列表并不比 CSF 列表短。": "Give the lists with 0 excitations (the multireference) first: their active orbitals are the labelling orbitals. Then add the excitation lists (see the authors' examples in graspgtest). Correlation orbitals beyond the labelling orbitals are combined into CSFGs, which is where GRASPG saves time; without lists with 0 excitations all orbitals are labelling orbitals and the CSFG list is no shorter than the CSF list.",
    "保存为 (不含扩展名)": "Save as (without extension)",
    "写出 name.g 和 name.l；留空则不复制": "writes name.g and name.l; empty = no copy",
    "从完整 CSFG 列表中只保留与多参考组态有相互作用的 CSFG。读取 rcsfgmr.inp、rcsfgmr.l (多参考) 和 rcsfg.inp、rlabel.inp (完整列表)，输出 rcsfg.out。两个列表的标记轨道必须相同。": "Keeps only the CSFGs of the full list that interact with the multireference. Reads rcsfgmr.inp, rcsfgmr.l (multireference) and rcsfg.inp, rlabel.inp (full list); writes rcsfg.out. Both lists must have the same labelling orbitals.",
    "多参考列表 (不含扩展名)": "Multireference list (without extension)",
    "复制 name.g / name.l 为 rcsfgmr.inp / rcsfgmr.l；留空则用现有文件": "copies name.g / name.l to rcsfgmr.inp / rcsfgmr.l; empty = use the existing files",
    "完整列表 (不含扩展名)": "Full list (without extension)",
    "复制 name.g / name.l 为 rcsfg.inp / rlabel.inp；留空则用现有文件": "copies name.g / name.l to rcsfg.inp / rlabel.inp; empty = use the existing files",
    "结果保存为 (不含扩展名)": "Save the result as (without extension)",
    "写出 name.g 和 name.l；与完整列表同名时覆盖原列表；留空则不复制": "writes name.g and name.l; the same name as the full list overwrites it; empty = no copy",
    "按轨道集把 name.g 拆分为 name<标签>.g 等多个文件，并为每个文件复制一份标记轨道文件 name<标签>.l。读取 name.g 和 rlabel.inp。": "Splits name.g by orbital set into name<label>.g etc., with a copy of the labelling-orbital file name<label>.l for each. Reads name.g and rlabel.inp.",
    "CSFG 列表名 (不含 .g)": "CSFG list name (without .g)",
    "复制此标记轨道文件为 rlabel.inp": "Copy this labelling-orbital file to rlabel.inp",
    "留空则直接用现有 rlabel.inp": "empty = use the existing rlabel.inp",
    "计算角系数 (写在 MPI 临时目录中，rmcdhf_csfg 必须使用相同的 MPI 进程数)。读取 rcsfg.inp、rlabel.inp，并写出展开的 CSF 列表 rcsf.out。": "Computes the angular coefficients (written to the MPI temporary directory; rmcdhf_csfg must use the same number of MPI processes). Reads rcsfg.inp, rlabel.inp and writes the expanded CSF list rcsf.out.",
    "CSFG 列表 (不含扩展名)": "CSFG list (without extension)",
    "生成初始径向波函数 rwfn.inp (读取 rcsfg.inp)。可先从已有 .w 文件读取部分轨道，其余用 Thomas-Fermi 或屏蔽类氢估计。": "Generates initial radial wave functions rwfn.inp (reads rcsfg.inp). Some orbitals can first be read from an existing .w file, the rest estimated by Thomas-Fermi or screened hydrogenic functions.",
    "自洽场计算 (读取 rcsfg.inp, rlabel.inp, rwfn.inp 和 rangular_csfg 的角系数；输出 rwfn.out, rmix.out)。每个 J 对称块一行能级号，例如 1,2,3 或 1-3。"
    "NSIC 是首轮 SCF 额外改进自洽性最差的轨道数。默认 (只有 MPI 版本)：EOL 为 4+(NW-NFIX)/4，含变分关联轨道时为 0。":
        "Self-consistent field calculation (reads rcsfg.inp, rlabel.inp, rwfn.inp and the angular coefficients of rangular_csfg; writes rwfn.out, rmix.out). One line of level numbers per J block, e.g. 1,2,3 or 1-3. "
        "NSIC is the number of extra improvements of the least self-consistent orbitals in the first SCF cycle. "
        "Default (MPI version only): EOL 4+(NW-NFIX)/4, or 0 if any varied orbital is a correlation orbital.",
    "把 rwfn.out / rcsf.out / rmix.out / rcsfg.inp / rlabel.inp 等保存为 name.w / name.c / name.m / name.g / name.l ...": "Saves rwfn.out / rcsf.out / rmix.out / rcsfg.inp / rlabel.inp etc. as name.w / name.c / name.m / name.g / name.l ...",
    "组态相互作用计算 (可含 Breit 与 QED)。读取 name.g、name.l、name.w，输出 name.cm 和展开的 CSF 列表 name.c 等。": "Configuration interaction (optionally with Breit and QED). Reads name.g, name.l, name.w; writes name.cm, the expanded CSF list name.c etc.",
    "先从此态复制 .g/.l/.w": "First copy .g/.l/.w from state",
    "按 n 限制 Breit 相互作用": "Limit the Breit interaction by n",
    "  略去 n 大于此值的轨道的 Breit 相互作用": "  Drop the Breit interaction of orbitals with n above",
    "按 l 限制 Breit 相互作用": "Limit the Breit interaction by l",
    "  略去 l 大于此值的轨道的 Breit 相互作用": "  Drop the Breit interaction of orbitals with l above",
    "不勾选时程序也读入此值": "read by the program even when not ticked",
    "谱学轨道的最大 n (NQEDMAX)": "Largest n of the spectroscopic orbitals (NQEDMAX)",
    "自能只算到此 n，频率相关的 Breit 也用它；≤ 8，且不超过列表中的最大 n": "self-energy up to this n, also used by the frequency-dependent Breit; ≤ 8 and not above the largest n of the list",
    "每个进程的内存上限 (GB，整数)": "Memory limit per process (GB, integer)",
    "列出能级。可给多个 .m / .cm 文件；也能读 rci_block_csfg 逐块写出、尚未写完的文件。": "Lists the levels. Several .m / .cm files can be given; files that rci_block_csfg is still writing block by block can be read too.",
    "CSFG 工具": "CSFG tools",
    "把 CSFG 列表展开为普通的 CSF 列表 rcsf.out (GRASP2018 的程序可以使用)。读取 rcsfg.inp、rlabel.inp。": "Expands a CSFG list into an ordinary CSF list rcsf.out (usable by the GRASP2018 programs). Reads rcsfg.inp, rlabel.inp.",
    "把 name.g 中的 CSFG 扩展到更大的轨道集 (各 l 的最高 n 加大)，每个轨道集写出 name<标签>.g 和 name<标签>.l。读取 name.g、name.l。新的轨道集不能比原来的小。": "Extends the CSFGs of name.g to larger orbital sets (higher n per l), writing name<label>.g and name<label>.l for each set. Reads name.g, name.l. The new sets must not be smaller than the original one.",
    "扩展 (每行: 轨道集  标签)": "Extensions (per line: orbital set  label)",
    "把 name.g 按 J 和宇称拆分为每块一个文件 name_odd1.g、name_even1.g …，并各复制一份 name.l。用于逐块的 CI 计算。各块必须由同一组轨道构成 (到同一 n 的单双激发一般满足，多参考列表不一定)。": "Splits name.g by J and parity into one file per block, name_odd1.g, name_even1.g …, each with a copy of name.l. Used for block-by-block CI. All blocks must be built from the same orbital set (normally true for single and double excitations to one n, not necessarily for multireference lists).",
    "按混合系数的平方累计，只保留占波函数一定比例的 CSFG，得到压缩的列表 (可再把最高轨道换成更大的轨道集，用于更大的 CI)。读取 name.(c)m、name.g、name.l；输出 rcsfg.out (换轨道集时为 rcsfg.nmax)、rcsf.out 等。": "Accumulates the squared mixing coefficients and keeps only the CSFGs making up a given fraction of the wave function, giving a condensed list (whose highest orbitals can be replaced by a larger set for a larger CI). Reads name.(c)m, name.g, name.l; writes rcsfg.out (rcsfg.nmax with a new orbital set), rcsf.out etc.",
    "保留的波函数比例 (0–1)": "Fraction of the wave function to keep (0–1)",
    "按混合系数排序": "Sort by mixing coefficients",
    "把各 l 的最高轨道换成更大的轨道集": "Replace the highest orbitals of each l by a larger set",
    "  新的轨道集": "  New orbital set",
    "各 l 的最高 n": "highest n per l",
    "写出 name.g，并复制 state.l 为 name.l；留空则不复制": "writes name.g and copies state.l to name.l; empty = no copy",
    "逐块的 CI：每个 J/宇称块单独计算 (先用 rcsfgblocksplit_csfg 拆分，如 oddCI6 → oddCI6_odd1 …)，大规模计算时节省内存。可以一次完成，也可以先只构造并保存 H 矩阵 (在 MPI 临时目录中)，再“重启”求能级；两步之间可用 rdistHmatrix_csfg 改变进程数。输出 name.cm、name.c 等。": "Block-by-block CI: each J/parity block is computed on its own (split first with rcsfgblocksplit_csfg, e.g. oddCI6 → oddCI6_odd1 …), which saves memory in large calculations. It can run in one step, or first only build and save the H matrix (in the MPI temporary directory) and then restart to compute the levels; in between, rdistHmatrix_csfg can change the number of processes. Writes name.cm, name.c etc.",
    "运行方式": "Mode",
    "一次完成：构造 H 矩阵并求能级": "one step: build the H matrix and compute the levels",
    "只构造并保存 H 矩阵": "only build and save the H matrix",
    "重启：读入保存的 H 矩阵求能级": "restart: read the saved H matrix and compute the levels",
    "态名称 (一个块)": "State name (one block)",
    "先复制此波函数文件为 name.w": "First copy this wave function file to name.w",
    "降低本征值精度以加快计算": "Lower the eigenvalue accuracy to speed up",
    "仅重启；如只为 rmixaccumulate_csfg 计算时": "restart only; e.g. when computing only for rmixaccumulate_csfg",
    "计算的能级": "Levels",
    "如 1-3；只构造 H 矩阵时不用": "e.g. 1-3; not used when only building the H matrix",
    "把 rci_block_csfg 保存的 H 矩阵重新分配给另一个 MPI 进程数 (例如用多个进程构造、用较少的进程求能级以节省内存)。先把 MPI 临时目录改名为来源目录，再写出新的 MPI 临时目录；之后把运行设置中的进程数改为目标进程数再“重启”。": "Redistributes the H matrix saved by rci_block_csfg to another number of MPI processes (e.g. build it with many processes and compute the levels with fewer to save memory). The MPI temporary directory is first renamed to the source directory, then a new MPI temporary directory is written; afterwards set the processes in the run settings to the target number and restart.",
    "来源目录 (原 MPI 临时目录改名为)": "Source directory (the MPI temporary directory is renamed to)",
    "此目录必须不存在或为空": "must not exist or be empty",
    "MPI 临时目录": "MPI temporary directory",
    "与运行设置中的一致": "as in the run settings",
    "原进程数": "Source processes",
    "目标进程数": "Target processes",
    "找不到保存的 H 矩阵 (在 {d} 中)：请先用“只构造并保存 H 矩阵”运行 rci_block_csfg。": "No saved H matrix (in {d}): run rci_block_csfg with \"only build and save the H matrix\" first.",
    "保存的 H 矩阵是 {k} 个 MPI 进程的 (在 {d} 中)，重启必须使用相同的进程数：请把 MPI 进程数设为 {k}，或先用 rdistHmatrix_csfg 重新分配给 {np} 个进程。": "The saved H matrix is for {k} MPI processes (in {d}); a restart must use the same number: set the MPI processes to {k}, or first redistribute it to {np} processes with rdistHmatrix_csfg.",
    "文件尚未写完，只显示已完成的块": "The file is not complete yet; only the finished blocks are shown",
    # 零阶 / 一阶划分
    "零阶 / 一阶划分：零阶空间以外的 CSF 按一阶微扰处理": "Zero/first-order partition: CSFs outside the zero-order space treated as first-order perturbations",
    "先用 rcsfzerofirst 把零阶空间的 CSF 排在各块最前面": "first put the zero-order CSFs at the top of each block with rcsfzerofirst",
    "  零阶空间的 CSF 列表": "  CSF list of the zero-order space",
    "各块的 ICCUT 取此列表各块的 CSF 数 (运行时读取)": "the ICCUT of each block is the number of CSFs of that block in this list (read when run)",
    "零阶 / 一阶划分的第一步：把 CSF 列表各块中属于零阶空间的 CSF 排到最前面，输出 rcsf.out。之后在 rangular 和 rci 中勾选“零阶 / 一阶划分”并给出同一个零阶空间列表：各块前 ICCUT 个 CSF 按变分处理，其余按一阶微扰处理 (只算它们与零阶空间的相互作用和对角元)，大规模展开时省时、省内存。": "First step of the zero/first-order partition: moves the CSFs of the zero-order space to the top of each block of a CSF list, writing rcsf.out. Then tick \"Zero/first-order partition\" in rangular and rci with the same zero-order list: the first ICCUT CSFs of each block are treated variationally, the rest as first-order perturbations (only their interaction with the zero-order space and their diagonal elements are computed), which saves time and memory for large expansions.",
    "零阶空间的 CSF 列表": "CSF list of the zero-order space",
    "如多参考列表；对称块须与要划分的列表一一对应": "e.g. the multireference list; its symmetry blocks must match those of the list to partition",
    "要划分的 CSF 列表": "CSF list to partition",
    "与要划分的列表同名时覆盖原列表；留空则不复制": "the same name as the list to partition overwrites it; empty = no copy",
    "无法读取零阶空间的 CSF 列表 {z} 或 {f}：{e}": "Cannot read the zero-order CSF list {z} or {f}: {e}",
    "零阶空间的 CSF 列表 {z} 与 {f} 的对称块不同 ({a} / {b})：两者的 J 和宇称块必须一一对应。": "The zero-order CSF list {z} and {f} have different symmetry blocks ({a} / {b}): their J and parity blocks must match one to one.",
    "块 {s}：零阶空间有 {n} 个 CSF，{f} 有 {ncf} 个；rangular_mpi 要求 1 < ICCUT < CSF 数。": "Block {s}: the zero-order space has {n} CSFs, {f} has {ncf}; rangular_mpi requires 1 < ICCUT < number of CSFs.",
    "块 {s}：零阶空间有 {n} 个 CSF，比 {f} 的 {ncf} 个还多。": "Block {s}: the zero-order space has {n} CSFs, more than the {ncf} of {f}.",
    "零阶 CSFG 列表必须在每个对称块中至少包含一个 CSFG。": "The zero-order CSFG list must contain at least one CSFG in each symmetry block.",
    "把零阶空间的 CSFG 排在各块最前面，输出 rcsfg.out 和 icut。两个 .g 文件旁必须有内容相同的同名 .l 文件。": "Move zero-order CSFGs to the beginning of each block, writing rcsfg.out and icut. Both .g files need matching .l files with identical contents.",
    "零阶空间的 CSFG 列表": "CSFG list of the zero-order space",
    "完整文件名，包含 .g": "Full file name, including .g",
    "要划分的 CSFG 列表": "CSFG list to partition",
    "结果 rcsfg.out 保存为": "Save the result rcsfg.out as",
    "与要划分的列表同名时覆盖原列表；同时复制同名 .l 和 .icut；留空则不复制": "Overwrites the list to partition if the names match; also copies the matching .l and .icut; leave blank to skip copying",
    "零阶 / 一阶划分：零阶空间以外的 CSFG 按一阶微扰处理": "Zero/first-order partition: CSFGs outside the zero-order space treated as first-order perturbations",
    "先用 rcsfgzerofirst_csfg 把零阶空间排在各块最前面": "First use rcsfgzerofirst_csfg to move the zero-order space to the beginning of each block",
    "各块 ICCUT 从此 .g 列表统计 CSFG 数，不是展开后的 CSF 数；每块至少一个": "ICCUT counts CSFGs in each block of this .g list, not expanded CSFs; at least one per block",
    "块 {s}：零阶空间有 {n} 个 CSFG，比 {f} 的 {ncf} 个还多。": "Block {s}: the zero-order space has {n} CSFGs, more than the {ncf} of {f}.",
}


def T(_text, /, **kw):
    """翻译界面文字；kw 用于格式化占位符 (第一个参数只能按位置传入，占位符可以用任何名字)。"""
    text = EN.get(_text, _text) if LANG == "en" else _text
    return text.format(**kw) if kw else text


def set_lang(lang):
    global LANG
    LANG = lang if lang in ("zh", "en") else "zh"


def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_csv(path, header, rows):
    """导出 CSV：UTF-8 带 BOM (Excel 能正确识别中文和 ⁺⁻ 等字符；pandas、Origin 等也能读)，逗号分隔，
    数值用点作小数点、不加千位分隔符。"""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def save_config(**kw):
    cfg = load_config()
    cfg.update(kw)
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f)
    except OSError:
        pass


def default_bindir():
    """GRASP 程序目录：$GRASP_BINDIR → 用户在界面中设置的目录 (存在即采用) → PATH 中 rmcdhf 所在目录
    → 本程序上一级目录的 bin (放在 GRASP 源码树中时)。"""
    saved = load_config().get("bindir", "")
    if not os.environ.get("GRASP_BINDIR") and saved and os.path.isdir(saved):
        return saved
    candidates = [os.environ.get("GRASP_BINDIR", ""), saved]
    p = shutil.which("rmcdhf")
    if p:
        candidates.append(os.path.dirname(p))
    candidates.append(os.path.join(REPO_DIR, "bin"))
    for d in candidates:
        if d and os.path.isfile(os.path.join(d, "rmcdhf")):
            return d
    return os.environ.get("GRASP_BINDIR") or os.path.join(REPO_DIR, "bin")


def yn(flag):
    return "y" if flag else "n"


def nonblank_lines(s):
    return [ln.strip() for ln in s.splitlines() if ln.strip()]


def count_levels(lines):
    """各块能级号 (如 1,2,3 或 1-3) 的总个数；无法解析的项按 1 个计。"""
    n = 0
    for ln in lines:
        for item in re.split(r"[\s,]+", ln):
            a, _, b = item.partition("-")
            try:
                n += int(b) - int(a) + 1 if b else 1
            except ValueError:
                n += 1 if item else 0
    return n


# ---------------------------------------------------------------------------
#  参数字段
#
#  界面根据 Step.fields 生成表单。各字段：
#    Note(text)                      说明文字
#    Entry(key, label, default, hint)
#    Check(key, label, default, hint)
#    Choice(key, label, options, hint)   options: [(值, 说明)], 值是传给程序的回答
#    Text(key, label, default, hint, lines)
#    Program(key, label, default)        可编辑的程序名下拉框 (bin 目录中的程序)
#    CsfLists(key)                       rcsfgenerate 的多个 CSF 列表
#
#  参数值 (vals) 是 {key: 值} 的 dict：Entry/Text/Choice/Program 为字符串，
#  Check 为 bool，CsfLists 为 [dict(configs, active, jmin, jmax, nexc)]。
# ---------------------------------------------------------------------------

class Note:
    def __init__(self, text):
        self.text = text


class Field:
    def __init__(self, key, label, default="", hint="", **kw):
        self.key, self.label, self.default, self.hint = key, label, default, hint
        self.__dict__.update(kw)


class Entry(Field):
    pass


class Check(Field):
    pass


class Text(Field):
    def __init__(self, key, label, default="", hint="", lines=4, **kw):
        super().__init__(key, label, default, hint, lines=lines, **kw)


class Choice(Field):
    def __init__(self, key, label, options, hint="", **kw):
        super().__init__(key, label, options[0][0], hint, options=options, **kw)


class Program(Field):
    pass


class CsfLists(Field):
    LIST_FIELDS = [("active", "活跃轨道集", "各 l 的最高 n，如 5s,4p,3d"),
                   ("jmin", "2J 最小值", ""),
                   ("jmax", "2J 最大值", ""),
                   ("nexc", "激发数", "负数: 关联轨道总是双占据")]
    EMPTY = dict(configs="", active="", jmin="", jmax="", nexc="2")

    def __init__(self, key, default):
        super().__init__(key, "", default)


def normalize(field, value):
    """把保存的参数值转换为字段需要的类型（兼容旧版本保存的 "5 标准 (EOL)" 这类下拉框值）。"""
    if isinstance(field, Check):
        return bool(value)
    if isinstance(field, Choice):
        code = str(value).split()[0] if str(value).split() else ""
        return code if code in [o[0] for o in field.options] else field.default
    if isinstance(field, CsfLists):
        return [dict(CsfLists.EMPTY, **d) for d in value] or [dict(CsfLists.EMPTY)]
    return str(value)


# ---------------------------------------------------------------------------
#  各程序
#
#  一个"任务" (job) 是一个 dict:
#    title, prog, args, stdin, log, mpi,
#    pre / post: 动作列表, ("cp", src, dst) 或 ("run", [prog, args...])
# ---------------------------------------------------------------------------

class Step:
    key = ""         # 保存参数时使用的名称 (与语言无关)
    name = ""        # 标签页标题
    prog = ""        # 程序名
    mpi = False      # 是否有 <prog>_mpi 并行版本
    fields = []

    @classmethod
    def defaults(cls):
        return {f.key: f.default for f in cls.fields if isinstance(f, Field)}

    def validate(self, v):
        """检查表单参数；不合法时由程序页抛出 ValueError。"""
        pass

    def stdin(self, v):
        return ""

    def args(self, v):
        return []

    def pre(self, v):
        return []

    def post(self, v):
        return []

    def log(self, v):
        return v.get("log", "").strip()

    def title(self, v):
        return self.prog

    def program(self, v):
        return self.prog

    def job(self, v):
        """流程中的任务；step 和 vals 记录来自哪个程序页和表单参数 (导出共享配置时使用)。"""
        return dict(title=self.title(v), prog=self.program(v), args=self.args(v), stdin=self.stdin(v),
                    log=self.log(v), pre=self.pre(v), post=self.post(v), mpi=self.mpi, step=self.key, vals=dict(v))


def fortran_zero(s):
    """Fortran 表控读入的数是否为 0 (支持 9.6D1 这类写法；无法解析时视为非零)。"""
    try:
        return float(s.strip().lower().replace("d", "e") or "0") == 0
    except ValueError:
        return False


def answers(lines):
    return "\n".join(lines) + "\n"


class RNucleus(Step):
    key = name = prog = "rnucleus"
    fields = [
        Note("生成核数据文件 isodata。"),
        Entry("z", "原子序数 Z", "42"),
        Entry("a", "质量数 A", "96", "0 = 点核模型"),
        Check("revise", "修改默认的核半径 / 表面厚度", False),
        Entry("rms", "  均方根半径 (fm)", ""),
        Entry("skin", "  表面厚度 (fm)", "2.3"),
        Entry("mass", "中性原子质量 (amu)", "96", "0 = 静止核"),
        Entry("spin", "核自旋 I", "1"),
        Entry("mu", "磁偶极矩 (核磁子)", "1"),
        Entry("q", "电四极矩 (barn)", "1"),
    ]

    def stdin(self, v):
        lines = [v["z"], v["a"]]
        if not fortran_zero(v["a"]):        # 点核 (A = 0) 时不询问核半径
            lines.append(yn(v["revise"]))
            if v["revise"]:
                lines += [v["rms"], v["skin"]]
        return answers(lines + [v["mass"], v["spin"], v["mu"], v["q"]])


class RcsfGenerate(Step):
    key = name = prog = "rcsfgenerate"
    fields = [
        Note("生成 CSF 列表 rcsf.out。组态写法如 1s(2,i)2s(2,*)2p(1,*)：c=闭壳, i=不激发, *=活跃, "
             "数字=最小占据。可添加多个列表 (Generate more lists)。"),
        Choice("order", "轨道排序", [("*", "默认"), ("r", "反序"), ("s", "按对称性")]),
        Choice("core", "闭壳芯", [("0", "无芯"), ("1", "He"), ("2", "Ne"), ("3", "Ar"),
                                  ("4", "Kr"), ("5", "Xe"), ("6", "Rn")]),
        CsfLists("lists", [dict(configs="1s(2,i)2s(2,i)2p(1,i)\n1s(2,i)2p(3,i)",
                                active="2s,2p", jmin="1", jmax="3", nexc="0")]),
        Entry("saveas", "保存 rcsf.out 为", "odd2.c", "留空则不复制"),
    ]

    def stdin(self, v):
        return answers([v["order"]] + self.list_answers(v))

    @staticmethod
    def list_answers(v):
        """闭壳芯和各个 CSF 列表的回答 (rcsfggenerate_csfg 相同，只是没有开头的排序问题)。"""
        lines = [v["core"]]
        for k, lst in enumerate(v["lists"]):
            lines += nonblank_lines(lst["configs"])
            lines += ["", lst["active"].strip(), f'{lst["jmin"].strip()},{lst["jmax"].strip()}',
                      lst["nexc"].strip(), "y" if k < len(v["lists"]) - 1 else "n"]
        return lines

    def post(self, v):
        s = v["saveas"].strip()
        return [("cp", "rcsf.out", s)] if s else []

    def title(self, v):
        s = v["saveas"].strip()
        return f"rcsfgenerate -> {s}" if s else "rcsfgenerate"


class RcsfInteract(Step):
    key = name = prog = "rcsfinteract"
    fields = [
        Note("从完整 CSF 列表中只保留与多参考组态有相互作用的 CSF。读取 rcsfmr.inp (多参考) 和 rcsf.inp (完整列表)，"
             "输出 rcsf.out。"),
        Entry("mr", "多参考 CSF 文件 → rcsfmr.inp", "odd2.c", "留空则直接用现有 rcsfmr.inp"),
        Entry("full", "完整 CSF 文件 → rcsf.inp", "odd.c", "留空则直接用现有 rcsf.inp"),
        Choice("ham", "按哪种哈密顿量判断相互作用", [("1", "Dirac-Coulomb"), ("2", "Dirac-Coulomb-Breit")]),
        Entry("saveas", "结果 rcsf.out 保存为", "", "如 odd.c；与完整列表同名时覆盖原列表；留空则不复制"),
    ]

    def stdin(self, v):
        return answers([v["ham"]])

    def pre(self, v):
        acts = []
        if v["mr"].strip():
            acts.append(("cp", v["mr"].strip(), "rcsfmr.inp"))
        if v["full"].strip():
            acts.append(("cp", v["full"].strip(), "rcsf.inp"))
        return acts

    def post(self, v):
        s = v["saveas"].strip()
        return [("cp", "rcsf.out", s)] if s else []

    def title(self, v):
        s = v["saveas"].strip()
        return f"rcsfinteract -> {s}" if s else "rcsfinteract"


class RcsfSplit(Step):
    key = name = prog = "rcsfsplit"
    fields = [
        Note("按轨道集把 name.c 拆分为 name<标签>.c 等多个文件。"),
        Entry("state", "CSF 文件名 (不含 .c)", "odd"),
        Text("splits", "拆分 (每行: 轨道集  标签)",
             "3s,3p,3d 3\n4s,4p,4d,4f 4\n5s,5p,5d,5f,5g 5\n6s,6p,6d,6f,6g,6h 6", lines=6),
    ]

    def stdin(self, v):
        rows = [ln.rsplit(None, 1) for ln in nonblank_lines(v["splits"])]
        lines = [v["state"], str(len(rows))]
        for orbs, label in rows:
            lines += [orbs, label]
        return answers(lines)

    def title(self, v):
        return f"rcsfsplit {v['state']}"


# 零阶 / 一阶划分 (rcsfzerofirst 把零阶空间的 CSF 排在各块最前面)：rangular、rci 的非默认设置按块询问 ICCUT，
# 即各块前 ICCUT 个 CSF 按变分处理，其余按一阶微扰处理 (只保留它们与零阶空间的相互作用和对角元)。ICCUT 取零阶空间
# CSF 列表各块的 CSF 数；该列表在加入流程时可能还不存在，所以输入中先写占位行 "@ICCUT 零阶列表 CSF 列表"，
# 运行时由 expand_iccut 换成各块的数 (导出的脚本中用 awk 计算，见 describe)。
ICCUT = "@ICCUT"
ICCUT_AWK = "f&&/^ *[*]/{print int(n/3);n=0;next} f&&NF{n++} /^CSF[(]s[)]:/{f=1} END{print int(n/3)}"

# 与 read_csfs 一样按三行记录计数，保留块的 J/宇称，避免把空的耦合行漏掉。
CSFG_ICCUT_AWK = r"""
function block() {
    if (row || n < 1) bad=1
    count[file,++blk]=n; symmetry[file,blk]=sym; n=0; row=0; sym=""
}
FNR==1 {
    if (file) {block(); blocks[file]=blk; if (!header) bad=1}
    file++; blk=0; n=0; row=0; active=0; header=0
}
/^CSF[(]s[)]:/ {active=1; header=1; next}
!active {next}
row==0 && /^ *[*] *$/ {block(); next}
row==0 && !NF {next}
{row++; if (row==3) {n++; if (n==1) sym=$NF; row=0}}
END {
    block(); blocks[file]=blk
    if (!header || file!=2 || blocks[1]!=blocks[2]) bad=1
    for (i=1;i<=blocks[1];i++)
        if (symmetry[1,i]!=symmetry[2,i] || count[1,i]>count[2,i]) bad=1
    if (bad) {print "Invalid CSFG ICCUT: check block symmetries and 1 <= zero count <= full count" > "/dev/stderr"; exit 1}
    for (i=1;i<=blocks[1];i++) print count[1,i]
}
"""


def zero_first_fields(default_zero):
    # added="2.2.0"：该版本新增的字段，旧版本导出的配置中没有时直接用默认值 (不提示)
    return [Check("zf", "零阶 / 一阶划分：零阶空间以外的 CSF 按一阶微扰处理", False,
                  "先用 rcsfzerofirst 把零阶空间的 CSF 排在各块最前面", added="2.2.0"),
            Entry("zero", "  零阶空间的 CSF 列表", default_zero, "各块的 ICCUT 取此列表各块的 CSF 数 (运行时读取)",
                  added="2.2.0")]


def csf_blocks(path):
    """CSF 列表各块的 (CSF 数, J 和宇称，如 3/2-)。"""
    import grasp_index
    return [(b["ncf"], b["label"]) for b in grasp_index.csf_index(path)["blocks"]]


def expand_iccut(stdin, cwd, strict=False):
    """把输入中的 ICCUT 占位行换成各块的 ICCUT；零阶列表与 CSF 列表的块不对应时抛出 RuntimeError。
    strict：rangular_mpi 要求 1 < ICCUT < 块的 CSF 数 (否则反复要求重新输入)。"""
    out = []
    for line in stdin.split("\n"):
        if not line.startswith(ICCUT + " "):
            out.append(line)
            continue
        parts = shlex.split(line)
        zero, full = parts[1:3]
        csfg = len(parts) > 3 and parts[3] == "csfg"
        try:
            zb, fb = csf_blocks(os.path.join(cwd, zero)), csf_blocks(os.path.join(cwd, full))
        except (OSError, StopIteration, IndexError) as e:
            raise RuntimeError(T("无法读取零阶空间的 CSF 列表 {z} 或 {f}：{e}", z=zero, f=full, e=e))
        if [s for _, s in zb] != [s for _, s in fb]:
            raise RuntimeError(T("零阶空间的 CSF 列表 {z} 与 {f} 的对称块不同 ({a} / {b})：两者的 J 和宇称块必须一一对应。",
                                 z=zero, f=full, a=" ".join(s for _, s in zb), b=" ".join(s for _, s in fb)))
        if csfg and (not zb or any(n < 1 for n, _ in zb)):
            raise RuntimeError(T("零阶 CSFG 列表必须在每个对称块中至少包含一个 CSFG。"))
        for (n, sym), (ncf, _) in zip(zb, fb):
            if csfg and n > ncf:
                raise RuntimeError(T("块 {s}：零阶空间有 {n} 个 CSFG，比 {f} 的 {ncf} 个还多。",
                                     s=sym, n=n, f=full, ncf=ncf))
            if n > ncf or (strict and not 1 < n < ncf):
                raise RuntimeError(T("块 {s}：零阶空间有 {n} 个 CSF，{f} 有 {ncf} 个；rangular_mpi 要求 1 < ICCUT < CSF 数。"
                                     if strict else "块 {s}：零阶空间有 {n} 个 CSF，比 {f} 的 {ncf} 个还多。",
                                     s=sym, n=n, f=full, ncf=ncf))
            out.append(str(n))
    return "\n".join(out)


class RcsfZeroFirst(Step):
    key = name = prog = "rcsfzerofirst"
    fields = [
        Note("零阶 / 一阶划分的第一步：把 CSF 列表各块中属于零阶空间的 CSF 排到最前面，输出 rcsf.out。之后在 rangular 和 rci "
             "中勾选“零阶 / 一阶划分”并给出同一个零阶空间列表：各块前 ICCUT 个 CSF 按变分处理，其余按一阶微扰处理 "
             "(只算它们与零阶空间的相互作用和对角元)，大规模展开时省时、省内存。"),
        Entry("zero", "零阶空间的 CSF 列表", "odd2.c", "如多参考列表；对称块须与要划分的列表一一对应"),
        Entry("full", "要划分的 CSF 列表", "odd3.c"),
        Entry("saveas", "结果 rcsf.out 保存为", "odd3.c", "与要划分的列表同名时覆盖原列表；留空则不复制"),
    ]

    def stdin(self, v):
        return answers([v["zero"].strip(), v["full"].strip()])

    def post(self, v):
        s = v["saveas"].strip()
        return [("cp", "rcsf.out", s)] if s else []

    def title(self, v):
        s = v["saveas"].strip()
        return f"rcsfzerofirst -> {s}" if s else "rcsfzerofirst"


class RAngular(Step):
    key = name = prog = "rangular"
    mpi = True
    fields = [
        Note("计算角系数 (mcp.*)。读取 rcsf.inp。"),
        Entry("csf", "复制此 CSF 文件为 rcsf.inp", "odd2.c", "留空则直接用现有 rcsf.inp"),
    ] + zero_first_fields("odd2.c")

    def stdin(self, v):
        if v["zf"]:
            return answers(["n", f"{ICCUT} {v['zero'].strip()} rcsf.inp"])
        return "y\n"

    def pre(self, v):
        s = v["csf"].strip()
        return [("cp", s, "rcsf.inp")] if s else []

    def title(self, v):
        s = v["csf"].strip()
        return f"rangular ({s})" if s else "rangular"


class RWfnEstimate(Step):
    key = name = prog = "rwfnestimate"
    fields = [
        Note("生成初始径向波函数 rwfn.inp。可先从已有 .w 文件读取部分轨道，其余用 Thomas-Fermi 或屏蔽类氢估计。"),
        Check("use_file", "先从已有波函数文件读取 (GRASP92 File)", False),
        Entry("wfile", "  波函数文件", "odd2.w", "如 odd2.w，空则为 rwfn.out"),
        Entry("forbs", "  读取的轨道", "*", "* = 全部"),
        Choice("method", "其余轨道的估计方法", [("3", "屏蔽类氢 (Screened Hydrogenic)"), ("2", "Thomas-Fermi"),
                                               ("4", "屏蔽类氢，关联轨道增加 Z (custom Z)")]),
        Entry("orbs", "  轨道", "*"),
        Entry("delz", "  关联轨道的 Z 增加量", "0", "仅方法 4"),
    ]

    def stdin(self, v):
        lines = ["y"]
        if v["use_file"]:
            lines += ["1", v["wfile"].strip(), v["forbs"]]
        lines += [v["method"], v["orbs"]]
        if v["method"] == "4":              # 方法 4 在轨道列表之后询问 Z 的增加量
            lines.append(v["delz"])
        return answers(lines)


class RMcdhf(Step):
    key = name = prog = "rmcdhf"
    mpi = True
    fields = [
        Note("自洽场计算 (读取 rcsf.inp, rwfn.inp, mcp.*；输出 rwfn.out, rmix.out)。"
             "每个 J 对称块一行能级号，例如 1,2,3 或 1-3。"
             "NSIC 是首轮 SCF 额外改进自洽性最差的轨道数。默认：EOL 串行为 (NW-NFIX)/4、MPI 为 4+(NW-NFIX)/4，"
             "含变分关联轨道时为 0；AL 为 2+(NW-NFIX)/4 (整数除法)。"),
        Check("mem", "使用内存版本 rmcdhf_mem", False, "角系数一次读入内存：更快，但更占内存"),
        Text("levels", "各块要优化的能级", "1\n1", "每块一行", lines=3),
        Choice("weights", "能级权重", [("5", "标准 (EOL)"), ("1", "等权重"), ("9", "用户指定")]),
        Entry("uweights", "  用户权重", "", "选 9 时填写, 如 1,1,2"),
        Entry("vary", "变分轨道", "*", "如 *  或 3*  或 2s,2p*"),
        Entry("spec", "谱学轨道 (非关联轨道)", "*", "空 = 无"),
        Entry("nscf", "最大 SCF 迭代次数", "100"),
        Check("modify_nsic", "修改 NSIC", False,
              "勾选后修改 NSIC；不勾选时用程序的默认值",
              added="2.4.0"),
        Entry("nsic", "  NSIC", "0", "非负整数；仅勾选修改 NSIC 时使用", added="2.4.0", enabled_by="modify_nsic"),
        Entry("log", "输出日志文件", "outodd_rmcdhf_initial", "空 = 只显示在窗口"),
        Entry("save", "结束后 rsave 保存为", "odd2", "空 = 不保存"),
    ]

    def stdin(self, v):
        self.validate(v)
        levels = nonblank_lines(v["levels"])
        # 非默认模式先关闭调试输出，并保留光速 / 网格、ACCY 的默认值。
        lines = (["n", "n", "n", "n"] if v.get("modify_nsic") else ["y"]) + levels
        if count_levels(levels) > 1:        # 只优化一个能级时 rmcdhf 不询问权重
            lines.append(v["weights"])
            if v["weights"] == "9":
                lines.append(v["uweights"])
        return answers(lines + [v["vary"], v["spec"], v["nscf"]] + self.other_defaults(v))

    def validate(self, v):
        if v.get("modify_nsic") and not re.fullmatch(r"[0-9]+", str(v.get("nsic", "0")).strip()):
            raise ValueError(T("NSIC 必须是非负整数"))

    def other_defaults(self, v):
        """只修改 NSIC；最后的 1 保留 SCF 的默认正交化顺序。"""
        if not v.get("modify_nsic"):
            return []
        nsic = str(v.get("nsic", "0")).strip()
        # Modify other defaults；节点阈值、积分、首振荡符号、ODAMP、CDAMP；NSIC；NSOLV、正交化。
        return ["y", "n", "n", "n", "n", "n", "y", nsic, "n", "n", "1"]

    def program(self, v):
        # MPI 时在此基础上加 _mpi：rmcdhf_mpi / rmcdhf_mem_mpi
        return "rmcdhf_mem" if v["mem"] else "rmcdhf"

    def post(self, v):
        s = v["save"].strip()
        return [("run", ["rsave", s])] if s else []

    def title(self, v):
        s = v["save"].strip()
        return f"{self.program(v)} -> {s}" if s else self.program(v)


class RSave(Step):
    key = name = prog = "rsave"
    fields = [
        Note("把 rcsf.inp / rwfn.out / rmix.out 等保存为 name.c / name.w / name.m ..."),
        Entry("state", "态名称", "odd2"),
    ]

    def args(self, v):
        return [v["state"].strip()]

    def title(self, v):
        return f"rsave {v['state']}"


class RCi(Step):
    key = name = prog = "rci"
    mpi = True
    fields = [
        Note("组态相互作用计算 (可含 Breit 与 QED)。读取 name.c, name.w，输出 name.cm 等。"),
        Entry("state", "态名称", "oddCI6"),
        Entry("copyfrom", "先从此态复制 .c/.w", "odd6", "空 = 不复制"),
        Check("trans", "包含横向 (Breit) 相互作用", True),
        Check("modfreq", "修改横向光子频率", True),
        Entry("factor", "  频率缩放因子", "1.d-6"),
        Check("vp", "真空极化 (Vacuum Polarisation)", True),
        Check("nms", "正常质量移动 (NMS)", False),
        Check("sms", "特殊质量移动 (SMS)", False),
        Check("se", "估计自能 (Self-energy)", True),
        Entry("nqed", "  自能计算的最大 n (≤8)", "4"),
        Text("levels", "各块计算的能级", "1\n1", "每块一行", lines=3),
    ] + zero_first_fields("odd2.c") + [
        Entry("log", "输出日志文件", "outodd_rci"),
    ]

    def stdin(self, v):
        state = v["state"].strip()
        if v.get("zf"):
            # 非默认设置：不重启、不改光速、按一阶微扰处理部分 CSF，再逐块给出 ICCUT
            lines = ["n", state, "n", "n", "y", f"{ICCUT} {v['zero'].strip()} {state}.c"]
        else:
            lines = ["y", state]
        lines += [yn(v["trans"]), yn(v["modfreq"])]
        if v["modfreq"]:
            lines.append(v["factor"])
        lines += [yn(v["vp"]), yn(v["nms"]), yn(v["sms"]), yn(v["se"])]
        if v["se"]:
            lines.append(v["nqed"])
        return answers(lines + nonblank_lines(v["levels"]))

    def pre(self, v):
        src, dst = v["copyfrom"].strip(), v["state"].strip()
        if not src:
            return []
        return [("cp", f"{src}.c", f"{dst}.c"), ("cp", f"{src}.w", f"{dst}.w")]

    def title(self, v):
        return f"rci {v['state']}"


class Jj2lsj(Step):
    key = name = prog = "jj2lsj"
    fields = [
        Note("把 jj 耦合的混合系数变换到 LSJ 耦合，给出 LSJ 标记。"),
        Entry("state", "态名称", "odd2"),
        Check("ci", "混合系数来自 CI 计算 (.cm)", False, "否则读 .m"),
        Check("unique", "需要唯一标记 (unique labeling)", True),
        Check("coupling_files", "写出 Coupling 需要的文件", False, added="2.5.0"),
        Check("coupling_backup", "生成 Coupling 文件前备份 LSJ 标记", False,
              "备份至 name.lsj.lbl_SAVE；须先运行默认 jj2lsj", added="2.5.0", enabled_by="coupling_files"),
        Entry("coupling_threshold", "Coupling 输出的成分打印下限", "0.001", added="2.5.0", enabled_by="coupling_files"),
        Text("coupling_levels", "Coupling 能级选择", "", "空 = 全部；否则每行：块号: 能级号，如 2: 1-3", lines=3,
             added="2.5.0", enabled_by="coupling_files"),
        Entry("log", "输出日志文件", ""),
    ]

    def validate(self, v):
        if not v.get("coupling_files"):
            return
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,22}", v["state"].strip()):
            raise ValueError(T("Coupling 态名称须为不含空格的 ASCII 基名，最多 23 个字符"))
        value = v.get("coupling_threshold", "0.001").strip()
        if not re.fullmatch(r"[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eEdD][+-]?[0-9]+)?", value) or not 0 <= float(value.lower().replace("d", "e")) <= 1:
            raise ValueError(T("成分打印下限必须是 0–1 的数"))
        for row in nonblank_lines(v.get("coupling_levels", "")):
            block, sep, levels = row.partition(":")
            if not sep or not re.fullmatch(r"[1-9][0-9]?", block.strip()) or not re.fullmatch(
                    r"[1-9][0-9]*(?: *- *[1-9][0-9]*)?(?:[ ,]+[1-9][0-9]*(?: *- *[1-9][0-9]*)?)*", levels.strip()):
                raise ValueError(T("Coupling 能级选择须为每行块号: 能级号"))
            if any(int(a) > int(b) for a, b in re.findall(r"([0-9]+) *- *([0-9]+)", levels)):
                raise ValueError(T("Coupling 能级选择须为每行块号: 能级号"))

    def stdin(self, v):
        self.validate(v)
        lines = [v["state"].strip(), yn(v["ci"]), yn(v["unique"])]
        if not v.get("coupling_files"):
            return answers(lines + ["y"])
        rows = nonblank_lines(v.get("coupling_levels", ""))
        lines += ["n", yn(not rows)]
        for i, row in enumerate(rows):
            block, _, levels = row.partition(":")
            lines += [block.strip(), levels.strip(), yn(i + 1 < len(rows))]
        # MINCOMP=0 自动写 .lsj.c/.lsj.j；不写、不复用 .lsj.T；Nielson-Koster 标记与默认一致。
        return answers(lines + ["0", v.get("coupling_threshold", "0.001"), "n", "n", "1"])

    def title(self, v):
        return f"jj2lsj {v['state']}"

    def pre(self, v):
        if v.get("coupling_files") and v.get("coupling_backup"):
            state = v["state"].strip()
            return [("cp", state + ".lsj.lbl", state + ".lsj.lbl_SAVE")]
        return []


class Coupling(Step):
    key = name = "coupling"
    prog = "Coupling"
    source_url = "https://github.com/compas/coupling"
    SCHEMES = {"1": ("LS", "jj"), "2": ("LS", "JJ", "LK", "JK", "LScjj", "jj"),
               "3": ("LS", "LS3", "LSJ3", "LK3", "JK3", "cLSJ3", "jj")}
    fields = [
        Note("外部程序 https://github.com/compas/coupling。先在 jj2lsj 勾选写出 Coupling 文件；"
             "读取 name.lsj.c、name.lsj.j，输出 name.coupN.*.lbl 和 name.coupN.sum。"),
        Entry("state", "态名称", "2s2p_DF", added="2.5.0"),
        Check("verbose", "详细输出", False, added="2.5.0"),
        Choice("shells", "耦合壳层数", [("3", "三壳层"), ("2", "两壳层"), ("1", "单壳层")], added="2.5.0"),
        Entry("threshold", "成分打印下限", "0", added="2.5.0"),
        Entry("orbitals", "重耦合轨道", "1s,2s,2p", "逗号分隔，最多 12 个", added="2.5.0"),
        Check("use_label", "结束后采用指定方案的标记", False, added="2.5.0"),
        Choice("scheme", "标记方案", [(s, s) for s in ("LK3", "JK3", "LS3", "LSJ3", "cLSJ3", "LS", "JJ", "LK", "JK", "LScjj", "jj")],
               added="2.5.0", enabled_by="use_label"),
        Check("backup", "先备份 name.lsj.lbl 为 name.lsj.lbl_LSJ", True,
              "采用方案标记前保存当前 LSJ 结果，不覆盖 .lsj.lbl_SAVE", added="2.5.0", enabled_by="use_label"),
    ]

    def validate(self, v):
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,22}", v["state"].strip()):
            raise ValueError(T("Coupling 态名称须为不含空格的 ASCII 基名，最多 23 个字符"))
        if v["shells"] not in self.SCHEMES:
            raise ValueError(T("耦合壳层数必须为 1、2 或 3"))
        if not re.fullmatch(r"[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eEdD][+-]?[0-9]+)?", v["threshold"].strip()):
            raise ValueError(T("成分打印下限必须是 0–1 的数"))
        if not 0 <= float(v["threshold"].lower().replace("d", "e")) <= 1:
            raise ValueError(T("成分打印下限必须是 0–1 的数"))
        orbitals = v["orbitals"].strip().split(",")
        if not 1 <= len(orbitals) <= 12 or len(v["orbitals"].strip()) > 56 or any(
                not re.fullmatch(r"[1-9][0-9]*[spdfghiklmno]", o.strip()) for o in orbitals):
            raise ValueError(T("请给出最多 12 个重耦合轨道，总长不超过 56 个字符"))
        if v["use_label"] and v["scheme"] not in self.SCHEMES[v["shells"]]:
            raise ValueError(T("所选标记方案不适用于该耦合壳层数"))

    def stdin(self, v):
        self.validate(v)
        # 轨道列表只在首次读到非空 CSF 时读入；若没有 CSF，末尾多给的一行不再被读取。
        return answers([v["state"].strip(), yn(not v["verbose"]), v["shells"], v["threshold"], v["orbitals"].strip()])

    def post(self, v):
        if not v["use_label"]:
            return []
        state = v["state"].strip()
        out = [("cp", state + ".lsj.lbl", state + ".lsj.lbl_LSJ")] if v["backup"] else []
        return out + [("cp", f"{state}.coup{v['shells']}.{v['scheme']}.lbl", state + ".lsj.lbl")]


class RHfs(Step):
    key = name = prog = "rhfs"
    fields = [
        Note("计算超精细结构常数 A、B 和朗德 g 因子。读取 isodata (核自旋 I、磁偶极矩、电四极矩在 rnucleus 中设置)、"
             "name.c、name.w、name.(c)m；输出 name.(c)h、name.(c)hoffd。"),
        Entry("state", "态名称", "oddCI6"),
        Check("ci", "混合系数来自 CI 计算", True, "否则读 .m"),
        Check("lsj", "结束后运行 rhfs_lsj，用 LSJ 标记输出结果", False, "需先运行 jj2lsj；输出 name.(c)hlsj"),
        Check("sort", "  按能量排序", True),
        Entry("log", "输出日志文件", ""),
    ]

    def stdin(self, v):
        return answers(["y", v["state"].strip(), yn(v["ci"])])

    def post(self, v):
        if not v["lsj"]:
            return []
        return [("run", ["rhfs_lsj"], answers([v["state"].strip(), yn(v["ci"]), yn(v["sort"])]))]

    def title(self, v):
        return f"rhfs {v['state']}"


class RHfsZeeman95(Step):
    key = name = prog = "rhfszeeman95"
    fields = [
        Note("默认设置下计算超精细和塞曼矩阵元。读取 isodata、name.c、name.w、name.(c)m；"
             "输出 name.(c)h，勾选非对角时另写 name.(c)gjhfs。不同 J 块须在同一个态文件中，使用同一组轨道；不做双正交变换。"),
        Note("与 rhfs 共用 name.(c)h，格式不同，后运行的会覆盖先运行的。另存只复制本次常数结果，不备份已有文件。"),
        Entry("state", "态名称", "oddCI6", added="2.5.0"),
        Check("ci", "混合系数来自 CI 计算", True, "否则读 .m", added="2.5.0"),
        Check("offdiag", "计算非对角矩阵元", True, added="2.5.0"),
        Entry("saveas", "常数结果 name.(c)h 另存为", "", "空 = 不复制", added="2.5.0"),
        Entry("log", "输出日志文件", "", added="2.5.0"),
    ]

    def validate(self, v):
        # NAME 长度为 24，程序用首个空格截断态名，因此至少留一位空格。
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,22}", v["state"].strip()):
            raise ValueError(T("rhfszeeman95 态名称须为不含空格的 ASCII 基名，最多 23 个字符"))

    def stdin(self, v):
        self.validate(v)
        return answers(["y", v["state"].strip(), yn(v["ci"]), yn(v["offdiag"])])

    def post(self, v):
        source = v["state"].strip() + (".ch" if v["ci"] else ".h")
        return [("cp", source, v["saveas"].strip())] if v["saveas"].strip() else []

    def title(self, v):
        return f"rhfszeeman95 {v['state']}"


class RIs4(Step):
    key = name = prog = "ris4"
    fields = [
        Note("计算同位素移位：正常 / 特殊质量移位参数和场移电子因子。读取 isodata、name.c、name.w、name.(c)m；"
             "输出 name.(c)i。"),
        Entry("state", "态名称", "oddCI6"),
        Check("ci", "混合系数来自 CI 计算", True, "否则读 .m"),
        Check("higher", "计算高阶场移电子因子", True),
        Check("save", "保存角系数 (name.IOB / name.ITB)，以后重复计算时直接读取", True,
              "这两个文件已存在时程序不询问"),
        Entry("log", "输出日志文件", ""),
    ]

    def stdin(self, v):
        # 最后一问只在角系数文件不存在时出现；多给的一行不会被读取
        return answers(["y", v["state"].strip(), yn(v["ci"]), yn(v["higher"]), yn(v["save"])])

    def title(self, v):
        return f"ris4 {v['state']}"


def tool_state(value):
    """工具按态名拼接文件名；rasfsplit 还把它传给未加引号的 cp。"""
    state = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}", state):
        raise ValueError(T("态名称须为 1–80 个英文字母、数字、下划线、点或短横线，且不能以点或短横线开头"))
    return state


def tool_fraction(value):
    """仅接受程序能读入的有限实数，避免 NaN 绕过范围判断。"""
    try:
        if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eEdD][+-]?[0-9]+)?", value.strip()):
            raise ValueError
        number = float(value.strip().lower().replace("d", "e"))
        if not 0 <= number <= 1:
            raise ValueError
    except ValueError:
        raise ValueError(T("截断值必须是 0 到 1 之间的数")) from None


class RMixExtract(Step):
    key = name = prog = "rmixextract"
    fields = [
        Note("按混合系数绝对值提取 CSF。读取 name.c、name.(c)m；输出 rcsf.out 和 name.rmx。"),
        Entry("state", "态名称", "oddCI6", added="2.5.0"),
        Check("ci", "混合系数来自 CI 计算", True, "否则读 .m", added="2.5.0"),
        Choice("scope", "提取方式", [("0", "各能级分别提取"), ("1", "所有能级联合提取")], added="2.5.0"),
        Entry("cutoff", "混合系数绝对值下限", "0.01", added="2.5.0"),
        Check("sort", "按混合系数排序 CSF", True, added="2.5.0"),
        Entry("saveas", "结果 rcsf.out 保存为", "", "空 = 不复制", added="2.5.0"),
    ]

    def validate(self, v):
        tool_state(v["state"])
        tool_fraction(v["cutoff"])
        if v["scope"] not in ("0", "1"):
            raise ValueError(T("提取方式必须为 0 或 1"))

    def stdin(self, v):
        self.validate(v)
        return answers([v["scope"], v["state"].strip(), yn(v["ci"]), v["cutoff"], yn(v["sort"])])

    def post(self, v):
        return [("cp", "rcsf.out", v["saveas"].strip())] if v["saveas"].strip() else []


class RMixAccumulate(Step):
    key = name = prog = "rmixaccumulate"
    fields = [
        Note("按混合系数平方的累积权重压缩 CSF。读取 name.c、name.(c)m；输出 rcsf.out。"),
        Entry("state", "态名称", "oddCI6", added="2.5.0"),
        Check("ci", "混合系数来自 CI 计算", True, "否则读 .m", added="2.5.0"),
        Entry("fraction", "保留的累积权重 (0–1)", "0.9999", added="2.5.0"),
        Check("sort", "按混合系数排序 CSF", True, added="2.5.0"),
        Entry("saveas", "结果 rcsf.out 保存为", "", "空 = 不复制", added="2.5.0"),
    ]

    def validate(self, v):
        tool_state(v["state"])
        tool_fraction(v["fraction"])

    def stdin(self, v):
        self.validate(v)
        return answers([v["state"].strip(), yn(v["ci"]), v["fraction"], yn(v["sort"])])

    def post(self, v):
        return [("cp", "rcsf.out", v["saveas"].strip())] if v["saveas"].strip() else []


class RTabHfs(Step):
    key = name = prog = "rtabhfs"
    fields = [
        Note("读取 rhfs_lsj 生成的 name.(c)hlsj，输出超精细常数表 hfs.tex。此版本只支持 HFS 数据。"),
        Text("files", "超精细常数文件", "oddCI6.chlsj\nevenCI6.chlsj", "每行一个，最多 20 个", lines=3, added="2.5.0"),
        Entry("nskip", "标签开头跳过的字符数", "0", added="2.5.0"),
    ]

    def validate(self, v):
        files = nonblank_lines(v["files"])
        if not 1 <= len(files) <= 20 or any(len(f) > 80 for f in files):
            raise ValueError(T("请给出 1–20 个文件名，每个不超过 80 个字符"))
        if not re.fullmatch(r"[0-9]+", v["nskip"].strip()) or not 0 <= int(v["nskip"]) < 64:
            raise ValueError(T("跳过字符数必须是 0–63 的整数"))

    def stdin(self, v):
        self.validate(v)
        files = nonblank_lines(v["files"])
        return answers([str(len(files))] + files + [v["nskip"]])


class RAsfSplit(Step):
    key = name = prog = "rasfsplit"
    fields = [
        Note("按 J 和宇称拆分 name.c、可用的 name.m / name.cm，并复制 name.w，输出 name_even1.* / name_odd1.* 等。"
             "前提：各块必须使用同一轨道集，否则拆出的 .w 没有意义。"),
        Entry("state", "态名称", "oddCI6", added="2.5.0"),
    ]

    def validate(self, v):
        tool_state(v["state"])

    def stdin(self, v):
        self.validate(v)
        return answers([v["state"].strip(), "y"])


class RLevels(Step):
    key = name = prog = "rlevels"
    fields = [
        Note("列出能级。可给多个 .m / .cm 文件。"),
        Entry("files", "混合系数文件", "odd2.m even2.m", "空格分隔"),
        Entry("log", "输出日志文件", ""),
    ]

    def args(self, v):
        return shlex.split(v["files"])


class RTableLevels(Step):
    key = name = prog = "rtablevels"
    fields = [
        Note("把 rlevels 生成的能级文件整理成 LaTeX 和 ASCII 能级表 (energytablelatex.tex、energytableascii.txt)。"
             "每个文件占表中一列，能级标记和顺序以最后一个文件为准。"),
        Entry("nskip", "标签开头跳过的字符数", "0", "如标签以闭壳芯 1s(2). 开头，跳过 6 个字符"),
        Text("files", "能级文件", "energy3", "每行一个，按活跃集从小到大；在 rlevels 页用“输出日志文件”生成", lines=4),
    ]

    def stdin(self, v):
        files = nonblank_lines(v["files"])
        return answers([v["nskip"], str(len(files))] + files)


class RTabTrans1(Step):
    key = name = prog = "rtabtrans1"
    fields = [
        Note("为 rtabtrans2 生成能级标记文件 energylabel.latex / energylabel.ascii。读取各混合系数文件及其 jj2lsj 标记 "
             "(name.lsj.lbl)。"),
        Text("files", "混合系数文件 (.m / .cm)", "oddCI6.cm\nevenCI6.cm", "每行一个，与计算跃迁的波函数对应；需先运行 jj2lsj",
             lines=3),
        Entry("nskip", "标签开头跳过的字符数", "0", "如标签以闭壳芯 1s(2). 开头，跳过 6 个字符"),
        Choice("fmt", "输出格式", [("0", "LaTeX"), ("1", "ASCII")]),
    ]

    def stdin(self, v):
        # 文件名逐行给出，空行结束
        return answers(nonblank_lines(v["files"]) + ["", v["nskip"], v["fmt"]])


class RTabTrans2(Step):
    key = name = prog = "rtabtrans2"
    fields = [
        Note("由 rtransition 的跃迁数据和 rtabtrans1 的能级标记生成跃迁表、寿命表 (transitiontable、lifetimetable 的 .tex / .txt) "
             "和 scatterplot.m。"),
        Note("注意：跃迁文件里每种跃迁类型都要至少有一条跃迁，否则 rtabtrans2 会报 End of file 错误 "
             "(例如 odd–even 之间没有 M1 / E2 跃迁时，rtransition 只算 E1)。"),
        Entry("tfile", "跃迁数据文件", "oddCI6.evenCI6.ct", "rtransition 的 .t / .ct，可把多个文件合并成一个"),
        Choice("fmt", "能级标记文件格式", [("0", "LaTeX"), ("1", "ASCII")], "与 rtabtrans1 的输出格式一致"),
        Entry("ion", "  离子名称 (表格左列)", "", "仅 ASCII 格式，如 Fe X"),
        Entry("cutoff", "A 值打印下限", "0"),
        Entry("frac", "跃迁 A 值占上能级总 A 值的最小比例", "0"),
        Check("sorted", "跃迁数据已按波长排序", True),
        Entry("digits", "波长小数位数 (1–6)", "3"),
    ]

    def stdin(self, v):
        lines = [v["tfile"].strip(), v["fmt"]]
        if v["fmt"] == "1":                 # ASCII 时询问表格左列的离子名称
            lines.append(v["ion"])
        return answers(lines + [v["cutoff"], v["frac"], yn(v["sorted"]), v["digits"]])


class RBioTransform(Step):
    key = name = prog = "rbiotransform"
    mpi = True
    fields = [
        Note("跃迁计算前的双正交变换。"),
        Check("ci", "混合系数来自 CI 计算", True),
        Entry("init", "初态名称", "oddCI6"),
        Entry("final", "末态名称", "evenCI6"),
        Entry("log", "输出日志文件", "out_rbiotransform"),
    ]

    def stdin(self, v):
        return answers(["y", yn(v["ci"]), v["init"], v["final"], "y"])


class RTransition(Step):
    key = name = prog = "rtransition"
    mpi = True
    fields = [
        Note("计算跃迁参数 (需先运行 rbiotransform)。"),
        Check("ci", "混合系数来自 CI 计算", True),
        Entry("init", "初态名称", "oddCI6"),
        Entry("final", "末态名称", "evenCI6"),
        Entry("kind", "跃迁类型", "E1", "如 E1  或 E1,M2"),
        Check("m1_diffj", "M1 跃迁只计算不同 J 的能级之间", False, "跃迁类型含 M1 时询问"),
        Check("e2_diffj", "E2 跃迁只计算不同 J 的能级之间", False, "跃迁类型含 E2 时询问"),
        Entry("log", "输出日志文件", "out_transition"),
    ]

    def stdin(self, v):
        kind = v["kind"].strip().upper()        # rtransition 只认大写的 E / M
        kinds = [k for k in re.split(r"[\s,;]+", kind) if k]
        lines = ["y", yn(v["ci"]), v["init"], v["final"], kind]
        # 按书写顺序，每个 M1 / E2 各追问一次 (rtransition 的 getrmp)
        for k in kinds:
            if k == "M1":
                lines.append(yn(v["m1_diffj"]))
            elif k == "E2":
                lines.append(yn(v["e2_diffj"]))
        return answers(lines)


class Custom(Step):
    key = "custom"
    name = "自定义"
    fields = [
        Note("运行任意 GRASP 程序，手动填写交互输入（每行回答一个提问）。"),
        Program("prog", "程序", "rhfs"),
        Entry("args", "命令行参数", ""),
        Text("stdin", "输入 (stdin)", "", lines=10),
        Entry("log", "输出日志文件", ""),
    ]

    def program(self, v):
        return v["prog"].strip()

    def title(self, v):
        return v["prog"].strip()

    def args(self, v):
        return shlex.split(v["args"])

    def stdin(self, v):
        s = v["stdin"]
        return s if s.endswith("\n") or not s else s + "\n"


# ---------------------------------------------------------------------------
#  GRASPG：GRASP2018 基于组态函数生成器 (CSFG) 的扩展，程序名带 _csfg，装在同一个 bin 目录中
#  (https://github.com/compas/graspg)。组态列表是 CSFG 列表 name.g 加标记轨道文件 name.l，
#  程序读 rcsfg.inp 和 rlabel.inp。rangular、rmcdhf、rci 只有 MPI 版本 (mpi = "only")，
#  MPI 进程数为 1 时直接启动；角系数总是写在 MPI_TMP 中。其他程序 (rnucleus、rhfs、ris4、
#  rbiotransform、rtransition、表格程序) 沿用 GRASP2018：rsave_csfg 和 rci_csfg 照常写出展开的 name.c。
# ---------------------------------------------------------------------------

def csfg_inputs(name):
    """把 name.g / name.l 复制为 rcsfg.inp / rlabel.inp。"""
    return [("cp", f"{name}.g", "rcsfg.inp"), ("cp", f"{name}.l", "rlabel.inp")] if name else []


class RcsfgGenerate(RcsfGenerate):
    key = name = prog = "rcsfggenerate_csfg"
    fields = [
        Note("生成 CSFG 列表 rcsfg.out 和标记轨道文件 rlabel.out。组态写法与 rcsfgenerate 相同。"),
        Note("先给出激发数为 0 的列表 (多参考组态)：它们的活跃轨道是“标记轨道”。再添加激发列表 (见作者的示例 graspgtest)。"
             "标记轨道以外的关联轨道由 CSFG 合并处理，GRASPG 因此省时；没有激发数为 0 的列表时所有轨道都是标记轨道，"
             "CSFG 列表并不比 CSF 列表短。"),
        RcsfGenerate.fields[2],             # 闭壳芯
        CsfLists("lists", [dict(configs="1s(2,i)2s(2,i)2p(1,i)\n1s(2,i)2p(3,i)",
                                active="2s,2p", jmin="1", jmax="3", nexc="0")]),
        Entry("saveas", "保存为 (不含扩展名)", "odd2", "写出 name.g 和 name.l；留空则不复制"),
    ]

    def stdin(self, v):
        return answers(self.list_answers(v))

    def post(self, v):
        s = v["saveas"].strip()
        return [("cp", "rcsfg.out", f"{s}.g"), ("cp", "rlabel.out", f"{s}.l")] if s else []

    def title(self, v):
        s = v["saveas"].strip()
        return f"{self.prog} -> {s}.g" if s else self.prog


class RcsfgInteract(Step):
    key = name = prog = "rcsfginteract_csfg"
    fields = [
        Note("从完整 CSFG 列表中只保留与多参考组态有相互作用的 CSFG。读取 rcsfgmr.inp、rcsfgmr.l (多参考) 和 rcsfg.inp、"
             "rlabel.inp (完整列表)，输出 rcsfg.out。两个列表的标记轨道必须相同。"),
        Entry("mr", "多参考列表 (不含扩展名)", "odd2", "复制 name.g / name.l 为 rcsfgmr.inp / rcsfgmr.l；留空则用现有文件"),
        Entry("full", "完整列表 (不含扩展名)", "odd", "复制 name.g / name.l 为 rcsfg.inp / rlabel.inp；留空则用现有文件"),
        Choice("ham", "按哪种哈密顿量判断相互作用", [("1", "Dirac-Coulomb"), ("2", "Dirac-Coulomb-Breit")]),
        Entry("saveas", "结果保存为 (不含扩展名)", "", "写出 name.g 和 name.l；与完整列表同名时覆盖原列表；留空则不复制"),
    ]

    def stdin(self, v):
        return answers([v["ham"]])

    def pre(self, v):
        mr, full = v["mr"].strip(), v["full"].strip()
        acts = [("cp", f"{mr}.g", "rcsfgmr.inp"), ("cp", f"{mr}.l", "rcsfgmr.l")] if mr else []
        return acts + csfg_inputs(full)

    def post(self, v):
        s = v["saveas"].strip()
        return [("cp", "rcsfg.out", f"{s}.g"), ("cp", "rlabel.inp", f"{s}.l")] if s else []

    def title(self, v):
        s = v["saveas"].strip()
        return f"{self.prog} -> {s}.g" if s else self.prog


class RcsfgSplit(RcsfSplit):
    key = name = prog = "rcsfgsplit_csfg"
    fields = [
        Note("按轨道集把 name.g 拆分为 name<标签>.g 等多个文件，并为每个文件复制一份标记轨道文件 name<标签>.l。"
             "读取 name.g 和 rlabel.inp。"),
        Entry("state", "CSFG 列表名 (不含 .g)", "odd"),
        Entry("label", "复制此标记轨道文件为 rlabel.inp", "odd.l", "留空则直接用现有 rlabel.inp"),
        RcsfSplit.fields[2],                # 拆分
    ]

    def pre(self, v):
        s = v["label"].strip()
        return [("cp", s, "rlabel.inp")] if s else []

    def title(self, v):
        return f"{self.prog} {v['state']}"


class RcsfgZeroFirst(RcsfZeroFirst):
    key = name = prog = "rcsfgzerofirst_csfg"
    fields = [
        Note("把零阶空间的 CSFG 排在各块最前面，输出 rcsfg.out 和 icut。两个 .g 文件旁必须有内容相同的同名 .l 文件。"),
        Entry("zero", "零阶空间的 CSFG 列表", "odd2.g", "完整文件名，包含 .g"),
        Entry("full", "要划分的 CSFG 列表", "odd3.g", "完整文件名，包含 .g"),
        Entry("saveas", "结果 rcsfg.out 保存为", "odd3.g", "与要划分的列表同名时覆盖原列表；同时复制同名 .l 和 .icut；留空则不复制"),
    ]

    def post(self, v):
        s = v["saveas"].strip()
        if not s:
            return []
        src = os.path.splitext(v["full"].strip())[0] + ".l"
        base = os.path.splitext(s)[0]
        acts = [("cp", "rcsfg.out", s), ("cp", "icut", base + ".icut")]
        if src != base + ".l":
            acts.append(("cp", src, base + ".l"))
        return acts

    def title(self, v):
        s = v["saveas"].strip()
        return f"{self.prog} -> {s}" if s else self.prog


def csfg_zero_first_fields():
    return [Check("zf", "零阶 / 一阶划分：零阶空间以外的 CSFG 按一阶微扰处理", False,
                  "先用 rcsfgzerofirst_csfg 把零阶空间排在各块最前面", added="3.0.0"),
            Entry("zero", "零阶空间的 CSFG 列表", "odd2.g",
                  "各块 ICCUT 从此 .g 列表统计 CSFG 数，不是展开后的 CSF 数；每块至少一个", added="3.0.0")]


def csfg_iccut(zero, full):
    return f"{ICCUT} {shlex.quote(zero.strip())} {shlex.quote(full)} csfg"


class RAngularCsfg(RAngular):
    key = name = "rangular_csfg"
    prog = "rangular_csfg_mpi"
    mpi = "only"
    fields = [
        Note("计算角系数 (写在 MPI 临时目录中，rmcdhf_csfg 必须使用相同的 MPI 进程数)。读取 rcsfg.inp、rlabel.inp，"
             "并写出展开的 CSF 列表 rcsf.out。"),
        Entry("csf", "CSFG 列表 (不含扩展名)", "odd2", "复制 name.g / name.l 为 rcsfg.inp / rlabel.inp；留空则用现有文件"),
    ] + csfg_zero_first_fields()

    def stdin(self, v):
        if v.get("zf"):
            return answers(["n", "n", csfg_iccut(v["zero"], "rcsfg.inp")])
        return "y\n"

    def pre(self, v):
        return csfg_inputs(v["csf"].strip())

    def title(self, v):
        s = v["csf"].strip()
        return f"{self.key} ({s})" if s else self.key


class RWfnEstimateCsfg(RWfnEstimate):
    key = name = prog = "rwfnestimate_csfg"
    fields = [Note("生成初始径向波函数 rwfn.inp (读取 rcsfg.inp)。可先从已有 .w 文件读取部分轨道，其余用 Thomas-Fermi "
                   "或屏蔽类氢估计。")] + RWfnEstimate.fields[1:]


class RMcdhfCsfg(RMcdhf):
    key = name = "rmcdhf_csfg"
    prog = "rmcdhf_csfg_mpi"
    mpi = "only"
    fields = [Note("自洽场计算 (读取 rcsfg.inp, rlabel.inp, rwfn.inp 和 rangular_csfg 的角系数；输出 rwfn.out, rmix.out)。"
                   "每个 J 对称块一行能级号，例如 1,2,3 或 1-3。"
                   "NSIC 是首轮 SCF 额外改进自洽性最差的轨道数。默认 (只有 MPI 版本)：EOL 为 4+(NW-NFIX)/4，含变分关联轨道时为 0。")] + \
             [f for f in RMcdhf.fields[1:] if getattr(f, "key", "") != "mem"]

    def program(self, v):
        return self.prog

    def post(self, v):
        s = v["save"].strip()
        return [("run", ["rsave_csfg", s])] if s else []

    def title(self, v):
        s = v["save"].strip()
        return f"{self.key} -> {s}" if s else self.key


class RSaveCsfg(RSave):
    key = name = prog = "rsave_csfg"
    fields = [
        Note("把 rwfn.out / rcsf.out / rmix.out / rcsfg.inp / rlabel.inp 等保存为 name.w / name.c / name.m / name.g / "
             "name.l ..."),
        Entry("state", "态名称", "odd2"),
    ]

    def title(self, v):
        return f"{self.prog} {v['state']}"


class RCiCsfg(RCi):
    key = name = "rci_csfg"
    prog = "rci_csfg_mpi"
    mpi = "only"
    fields = [
        Note("组态相互作用计算 (可含 Breit 与 QED)。读取 name.g、name.l、name.w，输出 name.cm 和展开的 CSF 列表 name.c 等。"),
        Entry("state", "态名称", "oddCI6"),
        Entry("copyfrom", "先从此态复制 .g/.l/.w", "odd6", "空 = 不复制"),
        Check("trans", "包含横向 (Breit) 相互作用", True),
        Check("modfreq", "修改横向光子频率", True),
        Entry("factor", "  频率缩放因子", "1.d-6"),
        Check("limn", "按 n 限制 Breit 相互作用", False),
        Entry("maxn", "  略去 n 大于此值的轨道的 Breit 相互作用", "15", "不勾选时程序也读入此值"),
        Check("liml", "按 l 限制 Breit 相互作用", False),
        Entry("maxl", "  略去 l 大于此值的轨道的 Breit 相互作用", "14", "不勾选时程序也读入此值"),
        Check("vp", "真空极化 (Vacuum Polarisation)", True),
        Check("nms", "正常质量移动 (NMS)", False),
        Check("sms", "特殊质量移动 (SMS)", False),
        Check("se", "估计自能 (Self-energy)", True),
        Entry("nqed", "谱学轨道的最大 n (NQEDMAX)", "2", "自能只算到此 n，频率相关的 Breit 也用它；≤ 8，且不超过列表中的最大 n"),
        Entry("mem", "每个进程的内存上限 (GB，整数)", "8"),
        Text("levels", "各块计算的能级", "1\n1", "每块一行", lines=3),
        Entry("log", "输出日志文件", "outodd_rci"),
    ] + csfg_zero_first_fields()

    def stdin(self, v):
        state = v["state"].strip()
        lines = (["n", state, "n", "n", "y", "n", csfg_iccut(v["zero"], state + ".g")]
                 if v.get("zf") else ["y", state])
        lines += [yn(v["trans"]), yn(v["modfreq"])]
        if v["modfreq"]:
            lines.append(v["factor"])
        lines += [yn(v["limn"]), v["maxn"], yn(v["liml"]), v["maxl"]]
        lines += [yn(v["vp"]), yn(v["nms"]), yn(v["sms"]), yn(v["se"]), v["nqed"], v["mem"]]
        return answers(lines + nonblank_lines(v["levels"]))

    def pre(self, v):
        src, dst = v["copyfrom"].strip(), v["state"].strip()
        if not src:
            return []
        return [("cp", f"{src}.{ext}", f"{dst}.{ext}") for ext in ("g", "l", "w")]

    def title(self, v):
        return f"{self.key} {v['state']}"


class RCiBlockCsfg(RCiCsfg):
    key = name = "rci_block_csfg"
    prog = "rci_block_csfg_mpi"
    fields = [
        Note("逐块的 CI：每个 J/宇称块单独计算 (先用 rcsfgblocksplit_csfg 拆分，如 oddCI6 → oddCI6_odd1 …)，大规模计算时节省内存。"
             "可以一次完成，也可以先只构造并保存 H 矩阵 (在 MPI 临时目录中)，再“重启”求能级；两步之间可用 rdistHmatrix_csfg "
             "改变进程数。输出 name.cm、name.c 等。"),
        Choice("mode", "运行方式", [("1", "一次完成：构造 H 矩阵并求能级"), ("2", "只构造并保存 H 矩阵"),
                                    ("3", "重启：读入保存的 H 矩阵求能级")]),
        Entry("state", "态名称 (一个块)", "oddCI6_odd1"),
        Entry("wfrom", "先复制此波函数文件为 name.w", "oddCI6.w", "空 = 不复制"),
    ] + RCiCsfg.fields[3:15] + [         # 横向相互作用 … NQEDMAX
        Check("crite", "降低本征值精度以加快计算", False, "仅重启；如只为 rmixaccumulate_csfg 计算时"),
        RCiCsfg.fields[15],                 # 内存上限
        Text("levels", "计算的能级", "1", "如 1-3；只构造 H 矩阵时不用", lines=2),
        Entry("log", "输出日志文件", "out_rci_oddCI6_odd1"),
    ]

    def stdin(self, v):
        state, levels = v["state"].strip(), nonblank_lines(v["levels"])
        if v["mode"] == "3":
            return answers(["n", state, "y", yn(v["se"]), yn(v["crite"]), v["mem"]] + levels)
        lines = RCiCsfg.stdin(self, dict(v, levels=""))
        return lines + answers(levels if v["mode"] == "1" else [" "])    # 空的能级行：只构造 H 矩阵

    def pre(self, v):
        src, dst = v["wfrom"].strip(), v["state"].strip()
        return [("cp", src, f"{dst}.w")] if src and v["mode"] != "3" else []

    def job(self, v):
        job = super().job(v)
        if v["mode"] == "3":
            job["check"] = "hmatrix"        # 运行前检查保存的 H 矩阵与进程数 (check_hmatrix)
        return job

    def title(self, v):
        mode = {"1": "", "2": " (H)", "3": " (restart)"}[v["mode"]]
        return f"{self.key} {v['state']}{mode}"


class RDistHMatrixCsfg(Step):
    key = name = prog = "rdistHmatrix_csfg"
    fields = [
        Note("把 rci_block_csfg 保存的 H 矩阵重新分配给另一个 MPI 进程数 (例如用多个进程构造、用较少的进程求能级以节省内存)。"
             "先把 MPI 临时目录改名为来源目录，再写出新的 MPI 临时目录；之后把运行设置中的进程数改为目标进程数再“重启”。"),
        Entry("src", "来源目录 (原 MPI 临时目录改名为)", "HMatrix_save", "此目录必须不存在或为空"),
        Entry("tmp", "MPI 临时目录", "tmp_mpi", "与运行设置中的一致"),
        Entry("nsrc", "原进程数", "4"),
        Entry("ntgt", "目标进程数", "1"),
    ]

    def stdin(self, v):
        return answers([v["src"].strip(), v["tmp"].strip(), v["nsrc"].strip(), v["ntgt"].strip()])

    def pre(self, v):
        tmp = v["tmp"].strip()
        return [("run", ["mv", "-T", tmp, v["src"].strip()]), ("run", ["mkdir", "-p", tmp])]

    def title(self, v):
        return f"{self.prog} {v['nsrc'].strip()} -> {v['ntgt'].strip()}"


class Jj2lsjCsfg(Jj2lsj):
    key = name = prog = "jj2lsj_csfg"
    # Coupling 的非默认输入只按 GRASP2018 的 jj2lsj 核对过 (#14)，jj2lsj_csfg 不提供
    fields = [f for f in Jj2lsj.fields if not getattr(f, "key", "").startswith("coupling_")]

    def title(self, v):
        return f"{self.prog} {v['state']}"


class RLevelsCsfg(RLevels):
    key = name = prog = "rlevels_csfg"
    fields = [
        Note("列出能级。可给多个 .m / .cm 文件；也能读 rci_block_csfg 逐块写出、尚未写完的文件。"),
    ] + RLevels.fields[1:]


class RcsfgExpand(Step):
    key = name = prog = "rcsfgexpand_csfg"
    fields = [
        Note("把 CSFG 列表展开为普通的 CSF 列表 rcsf.out (GRASP2018 的程序可以使用)。读取 rcsfg.inp、rlabel.inp。"),
        Entry("csf", "CSFG 列表 (不含扩展名)", "odd", "复制 name.g / name.l 为 rcsfg.inp / rlabel.inp；留空则用现有文件"),
        Entry("saveas", "结果 rcsf.out 保存为", "odd.c", "留空则不复制"),
    ]

    def pre(self, v):
        return csfg_inputs(v["csf"].strip())

    def post(self, v):
        s = v["saveas"].strip()
        return [("cp", "rcsf.out", s)] if s else []

    def title(self, v):
        s = v["saveas"].strip()
        return f"{self.prog} -> {s}" if s else self.prog


class RcsfgExtend(RcsfSplit):
    key = name = prog = "rcsfgextend_csfg"
    fields = [
        Note("把 name.g 中的 CSFG 扩展到更大的轨道集 (各 l 的最高 n 加大)，每个轨道集写出 name<标签>.g 和 name<标签>.l。"
             "读取 name.g、name.l。新的轨道集不能比原来的小。"),
        Entry("state", "CSFG 列表名 (不含 .g)", "odd6"),
        Text("splits", "扩展 (每行: 轨道集  标签)", "7s,7p,7d,7f,7g,7h _n7", lines=4),
    ]

    def title(self, v):
        return f"{self.prog} {v['state']}"


class RcsfgBlockSplit(Step):
    key = name = prog = "rcsfgblocksplit_csfg"
    fields = [
        Note("把 name.g 按 J 和宇称拆分为每块一个文件 name_odd1.g、name_even1.g …，并各复制一份 name.l。"
             "用于逐块的 CI 计算。各块必须由同一组轨道构成 (到同一 n 的单双激发一般满足，多参考列表不一定)。"),
        Entry("state", "CSFG 列表名 (不含 .g)", "odd6"),
    ]

    def stdin(self, v):
        return answers([v["state"].strip(), "y"])

    def title(self, v):
        return f"{self.prog} {v['state']}"


class RMixAccumulateCsfg(Step):
    key = name = prog = "rmixaccumulate_csfg"
    fields = [
        Note("按混合系数的平方累计，只保留占波函数一定比例的 CSFG，得到压缩的列表 (可再把最高轨道换成更大的轨道集，"
             "用于更大的 CI)。读取 name.(c)m、name.g、name.l；输出 rcsfg.out (换轨道集时为 rcsfg.nmax)、rcsf.out 等。"),
        Entry("state", "态名称", "oddCI6"),
        Check("ci", "混合系数来自 CI 计算 (.cm)", True, "否则读 .m"),
        Entry("frac", "保留的波函数比例 (0–1)", "0.9999999"),
        Check("sort", "按混合系数排序", True),
        Check("redefine", "把各 l 的最高轨道换成更大的轨道集", False),
        Entry("orbs", "  新的轨道集", "7s,7p,7d,7f,7g,7h", "各 l 的最高 n"),
        Entry("saveas", "结果保存为 (不含扩展名)", "oddRAC6", "写出 name.g，并复制 state.l 为 name.l；留空则不复制"),
    ]

    def stdin(self, v):
        lines = [v["state"].strip(), yn(v["ci"]), v["frac"], yn(v["sort"]), yn(v["redefine"])]
        if v["redefine"]:
            lines.append(v["orbs"].strip())
        return answers(lines)

    def post(self, v):
        s = v["saveas"].strip()
        if not s:
            return []
        out = "rcsfg.nmax" if v["redefine"] else "rcsfg.out"
        return [("cp", out, f"{s}.g"), ("cp", f"{v['state'].strip()}.l", f"{s}.l")]

    def title(self, v):
        s = v["saveas"].strip()
        return f"{self.prog} {v['state']} -> {s}.g" if s else f"{self.prog} {v['state']}"


STEPS = [RNucleus(), RcsfGenerate(), RcsfInteract(), RcsfSplit(), RcsfZeroFirst(), RAngular(), RWfnEstimate(), RMcdhf(), RSave(),
         RCi(), Jj2lsj(), Coupling(), RHfs(), RHfsZeeman95(), RIs4(), RMixExtract(), RMixAccumulate(), RTabHfs(),
         RAsfSplit(), RLevels(),
         RTableLevels(), RTabTrans1(), RTabTrans2(),
         RBioTransform(), RTransition(), Custom(),
         RcsfgGenerate(), RcsfgInteract(), RcsfgSplit(), RcsfgZeroFirst(), RAngularCsfg(), RWfnEstimateCsfg(), RMcdhfCsfg(), RSaveCsfg(),
         RCiCsfg(), Jj2lsjCsfg(), RLevelsCsfg(), RcsfgExpand(), RcsfgExtend(), RcsfgBlockSplit(),
         RMixAccumulateCsfg(), RCiBlockCsfg(), RDistHMatrixCsfg()]

# 程序包 (计算页右上角选择)：GRASPG 的程序装在 GRASP2018 的 bin 目录中，以此程序判断是否已安装
PACKAGES = ("GRASP2018", "GRASPG")
PACKAGE_PROBE = {"GRASPG": "rcsfggenerate_csfg"}

# 界面左侧程序列表的功能分组 (每个程序包中每个程序恰好出现一次)
STEP_GROUPS = {
    "GRASP2018": [
        ("准备", ["rnucleus", "rcsfgenerate", "rcsfinteract", "rcsfsplit", "rcsfzerofirst"]),
        ("自洽场与 CI", ["rangular", "rwfnestimate", "rmcdhf", "rsave", "rci"]),
        ("性质与跃迁", ["rhfs", "rhfszeeman95", "ris4", "rbiotransform", "rtransition"]),
        ("结果与工具", ["jj2lsj", "coupling", "rlevels", "rtablevels", "rtabtrans1", "rtabtrans2", "rmixextract",
                      "rmixaccumulate", "rtabhfs", "rasfsplit", "custom"]),
    ],
    "GRASPG": [
        ("准备", ["rnucleus", "rcsfggenerate_csfg", "rcsfginteract_csfg", "rcsfgsplit_csfg", "rcsfgzerofirst_csfg"]),
        ("自洽场与 CI", ["rangular_csfg", "rwfnestimate_csfg", "rmcdhf_csfg", "rsave_csfg", "rci_csfg", "rci_block_csfg"]),
        ("性质与跃迁", ["rhfs", "rhfszeeman95", "ris4", "rbiotransform", "rtransition"]),
        ("结果与工具", ["jj2lsj_csfg", "rlevels_csfg", "rtablevels", "rtabtrans1", "rtabtrans2", "custom"]),
        ("CSFG 工具", ["rcsfgexpand_csfg", "rcsfgextend_csfg", "rcsfgblocksplit_csfg", "rmixaccumulate_csfg",
                       "rdistHmatrix_csfg"]),
    ],
}


def package_keys(package):
    return [k for _, keys in STEP_GROUPS[package] for k in keys]


assert all(len(set(package_keys(p))) == len(package_keys(p)) for p in PACKAGES)
assert set().union(*(package_keys(p) for p in PACKAGES)) == {s.key for s in STEPS}
# 只属于某个扩展程序包的程序页 (导出配置时据此确定 package)
PACKAGE_ONLY = {p: set(package_keys(p)) - set(package_keys(PACKAGES[0])) for p in PACKAGES[1:]}


def package_allows(package, key):
    """扩展程序包装在 GRASP2018 上，GRASP2018 的程序页也可以使用。"""
    return key in package_keys(PACKAGES[0]) or key in package_keys(package)


def package_of(step_keys):
    """一组程序页所需的程序包 (只用到 GRASP2018 的程序页时为 GRASP2018)。"""
    keys = set(step_keys)
    return next((p for p, only in PACKAGE_ONLY.items() if keys & only), PACKAGES[0])


# ---------------------------------------------------------------------------
#  态组 (宇称)
#
#  odd 与 even 各有一套参数的程序页；其余程序页 (rnucleus, rlevels, rtablevels,
#  rbiotransform, rtransition, 自定义) 两组共用。文件名中的组名 (odd2.c, outodd_rci,
#  oddCI6 …) 在两组之间按名称替换对应。
# ---------------------------------------------------------------------------

GROUPS = ("odd", "even")
GROUP_STEPS = ("rcsfgenerate", "rcsfinteract", "rcsfsplit", "rcsfzerofirst", "rangular", "rwfnestimate", "rmcdhf", "rsave", "rci",
               "rmixextract", "rmixaccumulate", "rasfsplit", "jj2lsj", "coupling", "rhfs", "rhfszeeman95", "ris4",
               "rcsfggenerate_csfg", "rcsfginteract_csfg", "rcsfgsplit_csfg", "rcsfgzerofirst_csfg", "rangular_csfg", "rwfnestimate_csfg",
               "rmcdhf_csfg", "rsave_csfg", "rci_csfg", "jj2lsj_csfg", "rcsfgexpand_csfg", "rcsfgextend_csfg",
               "rcsfgblocksplit_csfg", "rmixaccumulate_csfg", "rci_block_csfg")

# even 组与 odd 组默认值中物理内容不同的部分 (grasptest/case1 的 2s2p(2))
EVEN_DEFAULTS = {
    "rcsfgenerate": dict(lists=[dict(configs="1s(2,i)2s(1,i)2p(2,i)", active="2s,2p",
                                     jmin="1", jmax="5", nexc="0")]),
    "rmcdhf": dict(levels="1,2,3\n1,2,3\n1,2"),
    "rci": dict(levels="1,2,3\n1,2,3\n1,2"),
    "rcsfggenerate_csfg": dict(lists=[dict(configs="1s(2,i)2s(1,i)2p(2,i)", active="2s,2p",
                                           jmin="1", jmax="5", nexc="0")]),
    "rmcdhf_csfg": dict(levels="1,2,3\n1,2,3\n1,2"),
    "rci_csfg": dict(levels="1,2,3\n1,2,3\n1,2"),
}


def rename_group(value, src, dst):
    """把参数值 (字符串 / dict / list) 中的组名 src 替换为 dst，如 odd2.c -> even2.c。"""
    if isinstance(value, str):
        return value.replace(src, dst)
    if isinstance(value, dict):
        return {k: rename_group(v, src, dst) for k, v in value.items()}
    if isinstance(value, list):
        return [rename_group(v, src, dst) for v in value]
    return value


def group_defaults(group):
    """某个态组各程序页的默认参数 {step key: vals}。"""
    steps = {s.key: s for s in STEPS}
    out = {}
    for key in GROUP_STEPS:
        vals = rename_group(steps[key].defaults(), GROUPS[0], group)
        if group == "even":
            vals.update(EVEN_DEFAULTS.get(key, {}))
        out[key] = vals
    return out


# ---------------------------------------------------------------------------
#  工作目录中的记录：打开目录时恢复上次的参数和流程
# ---------------------------------------------------------------------------

SESSION_FILE = ".grasp_gui.json"


def load_session(workdir):
    try:
        with open(os.path.join(workdir, SESSION_FILE)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_session(workdir, data):
    if not os.path.isdir(workdir):
        return False
    path = os.path.join(workdir, SESSION_FILE)
    try:
        with open(path + ".tmp", "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(path + ".tmp", path)     # 先写临时文件，避免中断时留下不完整的记录
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
#  脚本
# ---------------------------------------------------------------------------

def action_sh(a):
    """动作的 shell 写法：("cp", src, dst) 或 ("run", argv[, stdin])。"""
    if a[0] == "cp":
        return f"cp {shlex.quote(a[1])} {shlex.quote(a[2])}"
    cmd = " ".join(shlex.quote(x) for x in a[1])
    if len(a) > 2 and a[2]:
        return f"{cmd} <<'EOF'\n{a[2]}EOF"
    return cmd


def program_fallbacks(pipeline):
    """只为流程实际调用的程序生成 POSIX shell 替代函数。"""
    used = {p for job in pipeline for p in [job["prog"]] +
            [a[1][0] for a in job["pre"] + job["post"] if a[0] == "run"]}
    return [f'command -v {p} >/dev/null 2>&1 || {p}() {{ {aliases[0]} "$@"; }}'
            for p, aliases in PROGRAM_ALIASES.items() if p in used]


def describe(job, mpi_prefix="", fallback=True):
    """任务的 shell 等价写法，用于预览和导出脚本。

    mpi_prefix 非空 (如 "mpirun -np 4") 时，有并行版本的程序写成 "<mpi_prefix> <prog>_mpi"；
    只有 MPI 版本的程序 (mpi = "only"，程序名已含 _mpi) 写成 "<mpi_prefix> <prog>"，单进程时直接运行。
    """
    out = (program_fallbacks([job]) if fallback else []) + [action_sh(a) for a in job["pre"]]
    args = " ".join(shlex.quote(x) for x in job["args"])
    if mpi_prefix and job.get("mpi") == "only":
        cmd = f"{mpi_prefix} {shlex.quote(job['prog'])} {args}".rstrip()
    elif mpi_prefix and job.get("mpi"):
        cmd = f"{mpi_prefix} {job['prog']}_mpi {args}".rstrip()
    else:
        cmd = f"{shlex.quote(job['prog'])} {args}".rstrip()
    if job["log"]:
        cmd += f" > {shlex.quote(job['log'])}"
    csfg_lines = [ln for ln in job["stdin"].split("\n")
                  if ln.startswith(ICCUT + " ") and shlex.split(ln)[-1] == "csfg"]
    if csfg_lines:
        zero, full = shlex.split(csfg_lines[0])[1:3]
        out.append(f"grasp_iccut=$(awk {shlex.quote(CSFG_ICCUT_AWK)} {shlex.quote(zero)} "
                   f"{shlex.quote(full)}) || exit 1")
        lines = ["$grasp_iccut" if ln in csfg_lines else re.sub(r"([\\$`])", r"\\\1", ln)
                 for ln in job["stdin"].split("\n")]
        out.append(f"{cmd} <<EOF\n" + "\n".join(lines) + "EOF")
    elif any(ln.startswith(ICCUT + " ") for ln in job["stdin"].split("\n")):
        # 各块的 ICCUT 在运行时由零阶列表算出：不加引号的 here-document，其余各行转义
        lines = [f"$(awk '{ICCUT_AWK}' {shlex.quote(ln.split()[1])})" if ln.startswith(ICCUT + " ")
                 else re.sub(r"([\\$`])", r"\\\1", ln) for ln in job["stdin"].split("\n")]
        out.append(f"{cmd} <<EOF\n" + "\n".join(lines) + "EOF")
    elif job["stdin"].strip():
        out.append(f"{cmd} <<'EOF'\n{job['stdin']}EOF")
    else:
        out.append(cmd + " < /dev/null")
    out += [action_sh(a) for a in job["post"]]
    return "\n".join(out)


def shell_script(pipeline, bindir, workdir, mpi_prefix="", mpitmp="tmp_mpi"):
    body = ["#!/bin/sh", "set -x", "",
            f"export PATH={shlex.quote(bindir)}:$PATH", *program_fallbacks(pipeline),
            f"cd {shlex.quote(workdir)}", ""]
    if mpi_prefix or any(job.get("mpi") == "only" for job in pipeline):
        body += [f"export MPI_TMP=\"$PWD\"/{shlex.quote(mpitmp or 'tmp_mpi')}", 'mkdir -p "$MPI_TMP"',
                 "# hwloc's GL plugin makes mpirun hang under WSLg",
                 'export HWLOC_COMPONENTS="${HWLOC_COMPONENTS:--gl}"', ""]
    for i, job in enumerate(pipeline, 1):
        body += [f"# {i}. {job['title']}", describe(job, mpi_prefix, fallback=False), ""]
    return "\n".join(body)


def slurm_script(pipeline, s):
    """s: SLURM_DEFAULTS 中各项的取值。"""
    s = {k: str(s.get(k, d)).strip() for k, d in SLURM_DEFAULTS.items()}
    lines = ["#!/bin/bash", f"#SBATCH --job-name={s['job'] or 'grasp'}"]
    if s["partition"]:
        lines.append(f"#SBATCH --partition={s['partition']}")
    lines += [f"#SBATCH --nodes={s['nodes'] or 1}", f"#SBATCH --ntasks-per-node={s['ntasks'] or 1}"]
    if s["time"]:
        lines.append(f"#SBATCH --time={s['time']}")
    if s["mem"]:
        lines.append(f"#SBATCH --mem={s['mem']}")
    lines.append("#SBATCH --output=%x-%j.out")
    if s["mail"]:
        lines += [f"#SBATCH --mail-user={s['mail']}", "#SBATCH --mail-type=END,FAIL"]
    lines += [f"#SBATCH {x if x.startswith('-') else '--' + x}" for x in nonblank_lines(s["extra"])]
    lines += ["", "# Generated by the GRASP GUI (gui/grasp_gui.py).",
              "# Upload this script together with the input files (isodata, *.c, *.w ...)",
              "# to one directory on the server and submit it there with: sbatch <this script>",
              "", "set -e", ""]
    lines += nonblank_lines(s["modules"])
    lines += [f"export PATH={s['bindir']}:$PATH", *program_fallbacks(pipeline),
              'cd "$SLURM_SUBMIT_DIR"',
              f"export MPI_TMP={s['mpitmp'] or '$SLURM_SUBMIT_DIR/tmp_mpi'}",
              'mkdir -p "$MPI_TMP"',
              "",
              'echo "Job $SLURM_JOB_ID on $SLURM_JOB_NODELIST, $SLURM_NTASKS tasks, started $(date)"',
              "set -x", ""]
    for i, job in enumerate(pipeline, 1):
        lines += [f"# {i}. {job['title']}", describe(job, s["mpirun"], fallback=False), ""]
    lines += ["set +x", 'echo "Finished $(date)"', ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
#  运行
# ---------------------------------------------------------------------------

def check_mcp(cwd, np, mpitmp):
    """检查 rmcdhf 所需的 rangular 角系数文件，返回错误信息；没有问题时返回 None。

    串行 rangular 写 <cwd>/mcp.30 …；rangular_mpi 写 <MPI_TMP>/<rank>/mcp<rank>.30 …，
    rmcdhf(_mpi) 只认与自己相同的方式和进程数，否则会读不到角系数而异常退出。
    """
    serial = os.path.join(cwd, "mcp.30")
    tmp = os.path.join(cwd, mpitmp)

    def rank_file(i):
        return os.path.join(tmp, f"{i:03d}", f"mcp{i:03d}.30")

    k = 0                                   # rangular_mpi 用过的进程数
    while os.path.isfile(rank_file(k)):
        k += 1
    mismatch = T("角系数文件是 rangular 用 {k} 个 MPI 进程生成的 (在 {d} 中)，rmcdhf 必须使用相同的进程数："
                 "请把 MPI 进程数设为 {k}，或用 {np} 个进程重新运行 rangular。", k=k, d=tmp, np=np)
    missing = T("找不到 rangular 生成的角系数文件 (mcp.*)：请先运行 rangular。")
    if np <= 1:
        if not os.path.isfile(serial):
            return mismatch if k else missing
        mcp = serial
    else:
        if k != np:
            if k:
                return mismatch
            if os.path.isfile(serial):
                return T("角系数文件是串行 rangular 生成的 (工作目录中的 mcp.*)，rmcdhf 不能用 {np} 个 MPI 进程运行："
                         "请把 MPI 进程数设为 1，或用 {np} 个进程重新运行 rangular。", np=np)
            return missing
        mcp = rank_file(0)
    rcsf = os.path.join(cwd, "rcsf.inp")
    if os.path.isfile(rcsf) and os.path.getmtime(rcsf) > os.path.getmtime(mcp) + 1:
        return T("rcsf.inp 比角系数文件新：请先对当前的 rcsf.inp 运行 rangular。")
    return None


def check_mcp_csfg(cwd, np, mpitmp):
    """rmcdhf_csfg 的角系数检查：rangular_csfg 总是写 <MPI_TMP>/<rank>/csfg_mcp<rank>.30 … (单进程时也是)，
    rmcdhf_csfg 必须使用相同的进程数。"""
    tmp = os.path.join(cwd, mpitmp)
    np = max(np, 1)

    def rank_file(i):
        return os.path.join(tmp, f"{i:03d}", f"csfg_mcp{i:03d}.30")

    k = 0
    while os.path.isfile(rank_file(k)):
        k += 1
    if not k:
        return T("找不到 rangular_csfg 生成的角系数文件 (在 {d} 中)：请先运行 rangular_csfg。", d=tmp)
    if k != np:
        return T("角系数文件是 rangular_csfg 用 {k} 个 MPI 进程生成的 (在 {d} 中)，rmcdhf_csfg 必须使用相同的进程数："
                 "请把 MPI 进程数设为 {k}，或用 {np} 个进程重新运行 rangular_csfg。", k=k, d=tmp, np=np)
    rcsfg = os.path.join(cwd, "rcsfg.inp")
    if os.path.isfile(rcsfg) and os.path.getmtime(rcsfg) > os.path.getmtime(rank_file(0)) + 1:
        return T("rcsfg.inp 比角系数文件新：请先对当前的 rcsfg.inp 运行 rangular_csfg。")
    return None


def check_hmatrix(cwd, np, mpitmp):
    """rci_block_csfg 重启前：MPI 临时目录中保存的 H 矩阵 (<rank>/rci<rank>.res) 必须与进程数相同。"""
    tmp = os.path.join(cwd, mpitmp)
    np = max(np, 1)
    k = 0
    while os.path.isfile(os.path.join(tmp, f"{k:03d}", f"rci{k:03d}.res")):
        k += 1
    if not k:
        return T("找不到保存的 H 矩阵 (在 {d} 中)：请先用“只构造并保存 H 矩阵”运行 rci_block_csfg。", d=tmp)
    if k != np:
        return T("保存的 H 矩阵是 {k} 个 MPI 进程的 (在 {d} 中)，重启必须使用相同的进程数：请把 MPI 进程数设为 {k}，"
                 "或先用 rdistHmatrix_csfg 重新分配给 {np} 个进程。", k=k, d=tmp, np=np)
    return None


def csfg_failed(tail):
    """GRASPG 的程序出错时也以退出码 0 结束，只在输出中报告 (如 "rlabel.inp not exists, Stop Error!"、
    "Error!name.l is not found ..."、"Problems with opening the file name.g"，或 Fortran 的 STOP 'Error! …')。"""
    return bool(re.search(r"Stop Error|Error!|Problems with opening|is not found|not exists"
                          r"|^\s*STOP\b.*(error|unexpected|warning)", tail, re.I | re.M))
def child_env(**extra):
    """子进程 (GRASP、mpirun、后台运行进程) 的环境变量。"""
    env = dict(os.environ, **extra)
    if getattr(sys, "frozen", False):
        # PyInstaller 打包的程序把 LD_LIBRARY_PATH 指向自带的库；子进程用回原来的值
        orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if orig is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = orig
        # 界面自己设置的 fontconfig 配置 (见 grasp_gui.fix_fontconfig) 也不传给子进程
        own = env.pop("GRASP_GUI_FONTCONFIG", None)
        if own and env.get("FONTCONFIG_FILE") == own:
            env.pop("FONTCONFIG_FILE")
    return env


class Runner:
    """在后台线程中依次执行任务，输出通过 emit(text, tag) 送回界面（tag: "", head, info, err）。

    cfg: dict(bindir, cwd, np, mpirun, mpitmp, nice)
    """

    def __init__(self, emit):
        self.emit = emit
        self.proc = None
        self.stopped = False

    def resolve(self, bindir, prog):
        p = program_path(bindir, prog)
        if not p:
            raise FileNotFoundError(T("找不到程序 {p} (bin 目录: {d})", p=prog, d=bindir))
        return p

    def exec_cmd(self, argv, cfg, stdin="", log="", mpi=False):
        bindir, cwd = cfg["bindir"], cfg["cwd"]
        env = child_env(PATH=bindir + os.pathsep + os.environ.get("PATH", ""))
        if mpi and (cfg["np"] > 1 or mpi == "only"):
            mpitmp = os.path.join(cwd, cfg["mpitmp"])
            os.makedirs(mpitmp, exist_ok=True)
            env["MPI_TMP"] = mpitmp
            # hwloc 的 GL 插件会连接图形显示，在 WSLg 下使 mpirun 启动时卡住
            env.setdefault("HWLOC_COMPONENTS", "-gl")
            # 只有 MPI 版本的程序 (mpi = "only") 名称已含 _mpi，单进程时直接启动
            prog = self.resolve(bindir, argv[0] if mpi == "only" else argv[0] + "_mpi")
            if cfg["np"] > 1:
                argv = shlex.split(cfg["mpirun"]) + ["-np", str(cfg["np"]), prog] + list(argv[1:])
            else:
                argv = [prog] + list(argv[1:])
        else:
            argv = [self.resolve(bindir, argv[0])] + list(argv[1:])
        if cfg.get("nice") and shutil.which("nice"):
            argv = ["nice", "-n", "10"] + argv     # 计算占满 CPU 时界面仍保持流畅
        # 独立进程组：停止时连同 mpirun 启动的各个进程一起结束
        self.proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
                                     start_new_session=True)
        if self.stopped:                # stop() 在程序启动的同时到达 (当时 self.proc 还是 None)
            self._signal(self.proc, signal.SIGTERM)
        threading.Thread(target=self._feed, args=(self.proc, stdin.encode()), daemon=True).start()
        # 按块读取（而不是逐行），大量输出时显著减少界面刷新次数；
        # 也能显示不以换行结尾的提示
        fd = self.proc.stdout.fileno()
        dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        logf = open(os.path.join(cwd, log), "wb") if log else None
        self.tail = b""                     # 输出的最后一段，用于识别错误 (见 csfg_failed)
        try:
            while True:
                data = os.read(fd, 65536)
                if not data:
                    break
                if logf:
                    logf.write(data)
                self.tail = (self.tail + data)[-4096:]
                self.emit(dec.decode(data), "")
            self.emit(dec.decode(b"", final=True), "")
        finally:
            if logf:
                logf.close()
        rc = self.proc.wait()
        self.proc = None
        return rc

    @staticmethod
    def _feed(proc, data):
        try:
            proc.stdin.write(data)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    def run_job(self, job, cfg):
        self.emit(f"\n===== {job['title']} =====\n", "head")
        for a in job["pre"]:
            self.do_action(a, cfg)
        check = ({"hmatrix": check_hmatrix}.get(job.get("check")) or
                 {"rmcdhf": check_mcp, "rmcdhf_mem": check_mcp, "rmcdhf_csfg_mpi": check_mcp_csfg}.get(job["prog"]))
        if check:
            err = check(cfg["cwd"], cfg["np"], cfg["mpitmp"])
            if err:
                raise RuntimeError(err)
        stdin = job["stdin"]
        if ICCUT in stdin:
            stdin = expand_iccut(stdin, cfg["cwd"], strict=job["prog"] == "rangular" and cfg["np"] > 1)
        rc = self.exec_cmd([job["prog"]] + job["args"], cfg, stdin, job["log"], job.get("mpi"))
        if job["log"]:
            self.emit(T("[输出已写入 {f}]", f=job["log"]) + "\n", "info")
        if rc != 0 or self.stopped:
            raise RuntimeError(T("{p} 退出码 {rc}", p=job["prog"], rc=rc))
        if "_csfg" in job["prog"] and csfg_failed(self.tail.decode(errors="replace")):
            raise RuntimeError(T("{p} 报告了错误 (退出码为 0)，详见输出", p=job["prog"]))
        for a in job["post"]:
            self.do_action(a, cfg)

    def do_action(self, a, cfg):
        self.emit(f"$ {action_sh(a)}\n", "info")
        if a[0] == "cp":
            src, dst = os.path.join(cfg["cwd"], a[1]), os.path.join(cfg["cwd"], a[2])
            if os.path.abspath(src) != os.path.abspath(dst):     # 如 CSF 文件直接填成 rcsf.inp
                shutil.copyfile(src, dst)
        else:
            rc = self.exec_cmd(a[1], cfg, a[2] if len(a) > 2 else "")
            if rc != 0:
                raise RuntimeError(T("{p} 退出码 {rc}", p=a[1][0], rc=rc))

    def run_all(self, jobs, cfg, done, started=None):
        """依次执行 jobs，结束后调用 done(ok)；started(i, job)：开始第 i 步 (从 0 起) 时调用。
        开始前已调用 stop() 时一步也不执行；再次使用同一个 Runner 时由调用者先把 stopped 设回 False。"""
        # 绝对路径：MPI_TMP 由工作目录拼出，程序又在工作目录中启动，相对路径会被解析两次
        cfg = dict(cfg, cwd=os.path.abspath(cfg["cwd"]))
        ok = True
        try:
            for i, job in enumerate(jobs):
                if self.stopped:
                    break
                if started:
                    started(i, job)
                self.run_job(job, cfg)
        except Exception as e:  # noqa: BLE001 - 所有错误都报告给界面
            ok = False
            self.emit(T("\n*** 错误: {e}\n", e=e), "err")
        if self.stopped:
            ok = False
            self.emit(T("\n*** 已停止\n"), "err")
        done(ok)

    def stop(self):
        self.stopped = True
        proc = self.proc
        if proc:
            self._signal(proc, signal.SIGTERM)
            # mpirun 卡住时可能不响应 SIGTERM，5 秒后强制结束整个进程组
            threading.Timer(5, self._signal, args=(proc, signal.SIGKILL)).start()

    def kill(self, grace=2):
        """立即结束当前程序（退出界面时使用）：先 SIGTERM，grace 秒后 SIGKILL。"""
        self.stopped = True
        proc = self.proc
        if proc:
            self._signal(proc, signal.SIGTERM)
            try:
                proc.wait(grace)
            except subprocess.TimeoutExpired:
                self._signal(proc, signal.SIGKILL)

    @staticmethod
    def _signal(proc, sig):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
