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

VERSION = "2.9.0"         # 发布时改为发布的版本号 (写入导出的共享配置)
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
    "{p} 的态名称“{s}”须为英文字母、数字、下划线、点或短横线 (不以点或短横线开头)，最多 {n} 个字符": (
        "{p}: state name \"{s}\" must use letters, digits, underscores, dots or hyphens (not starting with a dot "
        "or hyphen), at most {n} characters"),
    "{p} 的文件名“{s}”不能含空白，最多 {n} 个字节": "{p}: file name \"{s}\" must not contain spaces, at most {n} bytes",
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
  grasp_gui mcp …                           MCP stdio 服务 (默认关闭)，见 grasp_gui mcp --help
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
  grasp_gui mcp …                           MCP stdio service (off by default), see grasp_gui mcp --help
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
    # 逐层计算的摘要和文件检查
    "流程预检发现以下问题：": "Pipeline preflight found the following issues:",
    "名称或参数不合法，不能运行": "Invalid names or parameters; cannot run",
    "没有发现缺失的输入文件或名称问题。": "No missing input files or name issues found.",
    "参数": "Parameters",
    "第 {n} 步 {p} 的 {field}：{why}": "Step {n}, {p}, field {field}: {why}",
    "第 {n} 步 {p} 的 {field} 写出 {file}，会使第 {next} 步 {consumer} 的 {target} 无法运行：{why}":
        "Step {n}, {p}, field {field} writes {file}, which prevents step {next}, {consumer}, "
        "field {target} from running: {why}",
    "检查流程中从选中开始的步骤；流程为空时检查当前表单。":
        "Check the pipeline from the selected step, or the current form if the pipeline is empty.",
    "[文件检查：{n} 个步骤未声明依赖，需人工核查]":
        "[File check: {n} steps have undeclared dependencies; check them manually]",
    "此步仍在运行，收敛尚无法确定": "This step is still running; convergence is not yet determined",
    "（未指定文件）": "(no file specified)",
    "当前完整配置": "Complete current configuration",
    "各步运行状态": "Step run status",
    "SCF 收敛情况": "SCF convergence",
    "各态的 CSF 数": "CSF counts by state",
    "能级与主要成分": "Levels and leading components",
    "能级随层的变化": "Energy changes between layers",
    "检查输入文件": "Check input files",
    "未声明文件依赖，无法检查": "File dependencies are undeclared; cannot check",
    "以下输入文件尚不存在，且未找到所选起点之后的已声明前序输出：": (
        "These input files are missing, with no declared preceding outputs after the selected "
        "start:"),
    "第 {n} 步 {p}：缺少 {f}；未找到已声明的前序输出，请核对文件名、运行起点或未检查的步骤。": (
        "Step {n}, {p}: missing {f}; no declared preceding output. Check the filename, start or unchecked steps."),
    "相近文件名：{names}": "Similar filenames: {names}",
    "第 {n} 步 {p}：{why}": "Step {n}, {p}: {why}",
    "没有可对应的运行记录": "No matching run record",
    "最近后台运行状态；按唯一连续标题序列对应，未保存参数指纹": (
        "Latest background state; matched by a unique title sequence, without saved parameter "
        "fingerprints"),
    "本次会话记录；按步骤参数指纹对应": "Current GUI session record; matched by step parameter fingerprints",
    "无日志，无法判断": "No log; cannot determine convergence",
    "无日志，迭代过程未知；最终自洽程度见 .sum": "No log, so the iterations are unknown; final self-consistency from .sum",
    "{f} 中没有轨道总结 (rmcdhf 可能没有正常结束)": "{f} has no orbital summary (rmcdhf may not have finished normally)",
    "该日志由后续步骤复用，无法判断此步的收敛情况": "A later step reuses this log; cannot determine convergence for this step",
    "每块能级数须为 1–200": "Levels per block must be between 1 and 200",
    "请至少选择一个摘要部分": "Select at least one summary section",
    "运行成功仅指流程结束；收敛按日志判断；层间按 J、宇称和序号对应，不保证物理态身份相同；同名输出只保留最后写入。": (
        "Run success means the pipeline ended. Convergence is assessed from logs. Layers match by J, "
        "parity and index, without guaranteeing the same physical state. Reused outputs retain only the "
        "last write."),
    "为满足大小上限，省略主要成分": "Leading components omitted to meet the size limit",
    "为满足大小上限，省略较早或未关联的结果": "Earlier or unassociated results omitted to meet the size limit",
    "摘要仍超过 {n} KB，请减少每块能级数或取消部分内容；完整配置可单独导出。": (
        "Summary still exceeds {n} KB. Request fewer levels or deselect sections; export the complete "
        "configuration separately."),
    "先预览并选择包含的部分。默认每个对称块前 5 个能级，文本最多 200 KB；结果读取在后台进行。": (
        "Preview and choose sections. Defaults to the first 5 levels per symmetry block, up to 200 KB; "
        "results load in the background."),
    "每个对称块的能级数": "Levels per symmetry block",
    "正在读取结果…": "Reading results…",
    "无法生成摘要": "Cannot generate summary",
    "摘要大小：{n} KB": "Summary size: {n} KB",
    "没有发现缺失的输入文件。": "No missing input files found.",
    "缺少输入文件": "Missing input files",
    "仍然运行吗？": "Run anyway?",
    "结果摘要…": "Results summary…",
    "结果摘要": "Results summary",
    "结果摘要（给 agent）": "Results summary (for an agent)",
    "附带完整配置": "Include the complete configuration",
    "保存结果摘要": "Save results summary",
    "Markdown 文件 (*.md)": "Markdown files (*.md)",
    "已保存结果摘要：{p}": "Results summary saved: {p}",
    "已复制结果摘要": "Results summary copied",
    # MCP 与外部修改的记录
    "MCP 可读取允许目录中的结果、创建和更新方案、启动与停止计算。客户端通过标准输入输出连接，不开放端口。": (
        "MCP can read results, create and update plans, and start or stop calculations in the allowed "
        "directory. Clients connect through standard input/output; no port is opened."),
    "MCP 已关闭": "MCP disabled",
    "MCP 已关闭。只能由用户本人打开（GUI 的“设置”菜单，或用户自己在终端运行命令）；请告知用户，不要代为执行。": (
        "MCP is disabled. Only the user may enable it (in the GUI Settings menu or in their own terminal). "
        "Inform the user; do not run commands on their behalf."),
    "MCP 已关闭；请由用户运行 grasp_gui mcp --enable 打开": (
        "MCP is disabled; the user must run grasp_gui mcp --enable to enable it"),
    "MCP 已打开": "MCP enabled",
    "MCP 默认关闭；请先由用户运行 grasp_gui mcp --enable": "MCP is off by default; the user must first run grasp_gui mcp --enable",
    "[此计算由 MCP agent 启动]": "[This calculation was started by an MCP agent]",
    "保留我的修改": "Keep my changes",
    "允许 AI agent 通过 MCP 使用本程序": "Allow AI agents via MCP",
    "图片文件必须使用 PNG、SVG 或 PDF 扩展名": "Image files must have a PNG, SVG or PDF extension",
    "工作目录记录": "Working-directory record",
    "工作目录记录已改变": "Working-directory record changed",
    "工作目录记录已被其他程序修改。请选择载入新记录，或保留并覆盖为当前修改。": (
        "Another program changed the record. Load the new record, or keep your changes and overwrite it."),
    "工作目录记录已被其他程序修改，请重新载入": "Another program changed the record; reload it",
    "已载入外部修改的记录；新增步骤由用户决定是否运行": "Loaded the externally changed record; the user decides whether to run added steps",
    "必须且只能提供 config 或 example 之一": "Provide exactly one of config or example",
    "无法保存 MCP 开关": "Cannot save the MCP setting",
    "无法生成 PNG": "Cannot generate PNG",
    "无法连接后台运行进程，请检查运行状态和日志": "Cannot connect to the background runner; check its state and log",
    "此目录没有工作目录记录": "This directory has no working-directory record",
    "没有可绘制的径向波函数": "No radial wavefunctions to plot",
    "没有可绘制的能级": "No levels to plot",
    "没有正在运行的任务": "No task is running",
    "确定用当前修改覆盖外部记录吗？": "Overwrite the external record with your current changes?",
    "示例不存在：{name}": "Unknown example: {name}",
    "程序页不存在：{step}": "Unknown program page: {step}",
    "结果太大，请减少文件、能级或成分数量": "Result too large; request fewer files, levels or components",
    "绘图需要 Qt；当前服务器没有图形库，请使用桌面版或旧系统版": (
        "Plotting requires Qt; this server has no graphics library. Use the desktop or legacy build."),
    "自定义命令默认拒绝；请由用户用 --allow-custom 启动服务": (
        "Custom commands are refused by default; the user must start the service with --allow-custom"),
    "覆盖外部记录": "Overwrite external record",
    "计算在另一台机器上运行，请在那台机器上停止": "The calculation is running on another machine; stop it there",
    "起始步骤超出流程范围": "Starting step is outside the pipeline",
    "路径必须位于允许的根目录中": "The path must be inside the allowed root directory",
    "载入新的记录": "Load new record",
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
    # 无界面的服务器入口
    "这是服务器版，不能打开窗口；请使用桌面版或旧系统版。": (
        "This is the server build and cannot open windows. Use the desktop or legacy build."),
    "服务器版命令：remote、mcp、check、script、steps、reference、_run；--version 显示程序和协议版本。": (
        "Server commands: remote, mcp, check, script, steps, reference, _run; "
        "--version shows the application and protocol versions."),
    # 用户选择的 rsync 文件传输
    "传输文件必须是目录内的相对路径": "Transfer files must use relative paths inside the directory",
    "文件太多，请缩小选择范围": "Too many files; select a smaller directory",
    "目标路径包含符号链接，请换一个目录：{p}": "Destination contains a symbolic link; choose another directory: {p}",
    "rsync 请填写完整路径或使用 rsync": "Use rsync or its absolute path",
    "找不到 rsync；请安装系统 rsync 或设置完整路径": "Cannot find rsync; install system rsync or set its absolute path",
    "此 rsync 缺少续传或校验所需选项，请安装 GNU rsync：{v}；缺少 {p}": (
        "This rsync lacks resume or verification options; install GNU rsync: {v}; missing {p}"),
    "请先选择要传输的文件": "Select files to transfer first",
    "含空格或特殊字符的路径需要两端 rsync 支持 --protect-args": (
        "Paths with spaces or special characters require --protect-args on both rsync installations"),
    "传输已取消，已传的部分可在下次续传": "Transfer cancelled; partial data can be resumed next time",
    "文件传输需要现有 SSH 连接复用，请先重新连接": "File transfer requires the existing SSH master; reconnect first",
    "文件在确认后已改变，请刷新清单后重新选择": "Files changed after confirmation; refresh the list and select again",
    "服务器拒绝传输会话，请管理员将 MaxSessions 调大；现有分析会话仍可使用": (
        "Server refused the transfer session; ask the administrator to increase MaxSessions. Analysis "
        "remains available."),
    "rsync 传输失败：{e}": "rsync transfer failed: {e}",
    "从本机上传…": "Upload from this computer…",
    "下载到本机…": "Download to this computer…",
    "上传本机目录…": "Upload local directory…",
    "服务器 rsync": "Server rsync",
    "目标目录": "Destination directory",
    "每个文件的大小阈值；1 MB = 1,000,000 字节": "Per-file threshold; 1 MB = 1,000,000 bytes",
    "大文件阈值": "Large-file threshold",
    "跳过已有文件": "Skip existing files",
    "续传 / 校验": "Resume / verify",
    "覆盖已有文件": "Overwrite existing files",
    "同名文件": "Existing files",
    "包括大文件": "Include large files",
    "大小": "Size",
    "大文件": "Large file",
    ".w、.c、.cm、角系数等大文件通常不需要下载；能级、组成和波函数曲线可直接在远程窗口查看。": (
        "Large .w, .c, .cm and angular files usually need no download. View levels, compositions and "
        "wavefunctions remotely."),
    "正在读取文件清单…": "Reading file list…",
    "刷新清单": "Refresh file list",
    "确认传输…": "Confirm transfer…",
    "取消传输": "Cancel transfer",
    "传输仍在进行，关闭窗口将取消传输。是否取消并关闭？": (
        "A transfer is still running. Closing this window will cancel it. Cancel and close?"),
    "已跳过符号链接或特殊文件：{n}": "Symbolic links or special files skipped: {n}",
    "小文件：{n} 个，{b} MB；选中大文件：{l} 个，{s} MB；未选大文件：{u} 个，{t} MB": (
        "Small files: {n}, {b} MB; selected large: {l}, {s} MB; unselected large: {u}, {t} MB"),
    "请选择目标目录": "Select a destination directory",
    "传输 {n}% · {s} · 剩余 {t}": "Transfer {n}% · {s} · remaining {t}",
    "传输失败，请查看 SSH 连接状态": "Transfer failed; check the SSH connection",
    "已按所选规则处理 {n} 个文件": "Processed {n} files using the selected policy",
    "可安装捆绑服务器版，或在此设置已有程序的完整路径。": (
        "Install the bundled server, or enter the absolute path of an existing executable here."),
    "服务器版本较旧": "Older server version",
    # 捆绑服务器版的显式安装
    "本构建不含服务器版": "This build does not include the server package",
    "服务器压缩包校验失败": "Server archive checksum failed",
    "服务器压缩包包含不安全的路径或文件类型": "Server archive contains unsafe paths or file types",
    "服务器版本记录无效": "Invalid server version record",
    "捆绑服务器版的版本或协议与桌面版不一致": "Bundled server version or protocol does not match this desktop build",
    "安装目录必须是完整路径或 ~/ 路径": "Install directory must be an absolute or ~/ path",
    "无法识别服务器安装检查结果": "Cannot read server installation checks",
    "服务器需要 glibc 2.17 或更新版本": "Server requires glibc 2.17 or newer",
    "服务器安装目录空间不足": "Not enough space in the server installation directory",
    "安装已取消": "Installation cancelled",
    "服务器安装请求超时": "Server installation request timed out",
    "服务器安装失败": "Server installation failed",
    "从服务器移除": "Remove from server",
    "从服务器移除…": "Remove from server…",
    "安装 / 更新服务器版": "Install / update server",
    "安装 / 更新服务器版…": "Install / update server…",
    "安装与分析会话依次进行；不使用 sudo，不修改 PATH 或 shell 配置。": (
        "Installation and analysis sessions run sequentially. No sudo, PATH or shell configuration changes."),
    "安装目录": "Installation directory",
    "检查服务器": "Check server",
    "确认移除": "Confirm removal",
    "确认安装": "Confirm installation",
    "捆绑版本：{v}；大小：{n} MiB": "Bundled version: {v}; size: {n} MiB",
    "没有本程序安装的版本": "No versions installed by this application",
    "架构：{a}；{g}；可用空间：{n} MiB": "Architecture: {a}; {g}; free space: {n} MiB",
    "安装目录：{p}\n链接：{l}\n已安装版本：\n{v}": "Install directory: {p}\nLink: {l}\nInstalled versions:\n{v}",
    "移除下面列出的版本目录及本程序创建的链接；不删除计算目录或结果。": (
        "Remove the listed version directories and the application-created link. "
        "Calculation directories and results are kept."),
    "将传输捆绑包、校验并切换链接；保留上一版和仍在运行的旧版。": (
        "Transfer and verify the bundled archive, then switch the link. Keep the previous and any running versions."),
    "服务器 {v}，桌面 {d}；协议兼容，可继续使用。现在安装捆绑版本吗？": (
        "Server {v}, desktop {d}: the protocol is compatible, so you can continue. Install the bundled version now?"),
    "服务器端不可用": "Server executable unavailable",
    "使用已有的路径": "Use an existing path",
    # SSH 远程连接
    "SSH 登录": "SSH login",
    "SSH 主机确认": "SSH host confirmation",
    "SSH 提示": "SSH notification",
    "请通过管理员或其他可信渠道核对主机指纹；一致后再输入 yes 或指纹，不确定请取消。":
        "Verify the host fingerprint with your administrator or another trusted channel. "
        "Enter yes or the fingerprint only if it matches; otherwise cancel.",
    "服务器端程序请填写完整路径或 ~/ 路径": "Enter a full server executable path or a ~/ path",
    "连接服务器…": "Connect to server…",
    "连接服务器": "Connect to server",
    "SSH 主机（沿用 ~/.ssh/config）": "SSH host (uses ~/.ssh/config)",
    "SSH 控制目录必须属于当前用户且权限为 700": "The SSH control directory must belong to you with mode 700",
    "SSH 登录或服务器握手超时": "SSH login or server handshake timed out",
    "SSH 连接已关闭": "SSH connection closed",
    "SSH 连接已断开": "SSH connection lost",
    "上一级": "Parent directory",
    "使用此目录": "Use this directory",
    "使用用户 scope": "Use user scope",
    "参数": "Parameters",
    "启动或停止的结果无法确认；重连后请先核对状态，不要直接重复操作":
        "Cannot confirm start/stop; check the state after reconnecting before repeating the operation",
    "在新窗口中打开径向波函数": "Open radial wave functions in a new window",
    "在新窗口中打开能级图": "Open levels in a new window",
    "密钥或 ssh-agent 优先；密码和验证码仅在 OpenSSH 输入框中输入，不保存。":
        "Prefer keys or ssh-agent; enter passwords and verification codes only in the OpenSSH prompt. They are "
        "never saved.",
    "已复制 Slurm 脚本，请核对服务器设置后提交。": "Slurm script copied; check the server settings before submitting.",
    "断线与计算": "Disconnection and calculations",
    "断线会结束计算，请由用户选择用户 scope、Slurm 或明确照常启动":
        "Disconnection will end the calculation; choose a user scope, Slurm, or explicitly start normally",
    "服务器 {h} 延迟 {n} ms": "Server {h}: latency {n} ms",
    "服务器 {h}:{p}": "Server {h}:{p}",
    "服务器握手失败，请检查程序路径并安装或更新服务器端":
        "Server handshake failed; check the executable path and install or update the server program",
    "服务器未开启 lingering，不能使用用户 scope": "Lingering is disabled on the server; a user scope cannot be used",
    "服务器目录": "Server directory",
    "服务器端程序": "Server executable",
    "服务器退出登录会结束计算。可使用用户 scope（须开启 lingering），导出 Slurm，或明确选择照常启动。":
        "Logging out will end the calculation. Use a user scope (requires lingering), export Slurm, or explicitly "
        "start normally.",
    "本机 OpenSSH 低于 8.4，仅支持密钥或 ssh-agent 登录":
        "Local OpenSSH is older than 8.4; only key or ssh-agent authentication is supported",
    "正在从 {h} 读取…": "Reading from {h}…",
    "没有运行记录": "No run recorded",
    "照常启动": "Start normally",
    "用户 scope 失败时可导出 Slurm，或重新选择照常启动；请先核对运行状态。":
        "If a user scope fails, export Slurm or explicitly start normally; check the run state first.",
    "组成尚在读取": "Composition loading",
    "自动尝试 ~/.local/bin/grasp_gui 和 PATH；也可填完整路径": "Tries ~/.local/bin/grasp_gui then PATH; or enter the full path",
    "请输入有效的 SSH 主机或 user@host": "Enter a valid SSH host or user@host",
    "读取更多组成": "Load more components",
    "读取期间远程文件已改变，请刷新": "Remote files changed while reading; refresh",
    "远程协议不兼容，请更新服务器端程序": "Incompatible remote protocol; update the server program",
    "远程文件请在服务器目录中选择": "Select remote files in the server directory",
    "远程模式只查看已算跃迁；计算请运行服务器上的流程。": "Remote mode displays existing transitions; use the server pipeline to compute them.",
    "远程流程": "Remote pipeline",
    "远程流程包含可运行任意程序的自定义命令。确认运行吗？":
        "The remote pipeline contains custom commands that can run arbitrary programs. Run it?",
    "远程流程只读；可启动、停止和查看，修改请在服务器上完成。":
        "Remote pipeline is read-only; start, stop and view it here; edit it on the server.",
    "远程目录中文件过多，请选择更小的目录": "Too many remote directory entries; choose a smaller directory",
    "远程结果已截断，请选择更少的文件": "Remote results truncated; select fewer files",
    "远程结果过大，请减少文件或行数": "Remote result too large; request fewer files or rows",
    "远程请求超时，正在重新连接": "Remote request timed out; reconnecting",
    "远程运行": "Remote run",
    "远程模式不导出原始 GRASP 跃迁文件；可导出 CSV 或能级图。":
        "Remote mode cannot export original GRASP transition files; export CSV or a level diagram instead.",
    "连接 / 刷新": "Connect / refresh",
    "重新连接": "Reconnect",
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


def saved_run_settings():
    """GUI 与无窗口服务采用相同的运行设置和首次 MPI 默认值。"""
    cfg = load_config()
    bindir, mpirun = default_bindir(), cfg.get("mpirun", "mpirun")
    if "np" not in cfg:
        cfg["np"] = default_mpi_np(bindir, mpirun)
        save_config(np=cfg["np"])
    return dict(bindir=bindir, np=int(cfg["np"]), mpirun=mpirun,
                mpitmp=cfg.get("mpitmp", "tmp_mpi"), nice=cfg.get("nice", True))


def save_config(**kw):
    import grasp_session
    try:
        with grasp_session.locked(CONFIG_FILE):
            cfg = load_config()
            cfg.update(kw)
            grasp_session.atomic(os.path.realpath(CONFIG_FILE), json.dumps(cfg).encode("utf-8"))
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
    output_fields = ()       # 影响输出名称的字段，供预检追溯派生名称。

    @classmethod
    def defaults(cls):
        return {f.key: f.default for f in cls.fields if isinstance(f, Field)}

    def validate(self, v):
        """检查表单参数；不合法时由程序页抛出 ValueError。"""
        pass

    def inputs(self, v, run=None):
        """返回运行所需文件；None 表示尚未声明，供预检明确标为未知。"""
        return None

    def outputs(self, v, run=None):
        return None

    def removes(self, v):
        return []

    def result_files(self, v):
        """结果按流程关联层次，不从文件名的数字猜层序。"""
        return []

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

    def inputs(self, v, run=None):
        return []

    def outputs(self, v, run=None):
        return ["isodata"]


class RcsfGenerate(Step):
    output_fields = ("saveas",)
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
        lines = [v["order"], v["core"]]
        for k, lst in enumerate(v["lists"]):
            lines += nonblank_lines(lst["configs"])
            lines += ["", lst["active"].strip(), f'{lst["jmin"].strip()},{lst["jmax"].strip()}',
                      lst["nexc"].strip(), "y" if k < len(v["lists"]) - 1 else "n"]
        return answers(lines)

    def post(self, v):
        s = v["saveas"].strip()
        return [("cp", "rcsf.out", s)] if s else []

    def title(self, v):
        s = v["saveas"].strip()
        return f"rcsfgenerate -> {s}" if s else "rcsfgenerate"

    def inputs(self, v, run=None):
        return []

    def outputs(self, v, run=None):
        return ["rcsf.out"]


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

    def inputs(self, v, run=None):
        return ["rcsfmr.inp", "rcsf.inp"]

    def outputs(self, v, run=None):
        return ["rcsf.out"]


class RcsfSplit(Step):
    output_fields = ("state", "splits")
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

    def inputs(self, v, run=None):
        return [v["state"].strip() + ".c"]

    def outputs(self, v, run=None):
        return [v["state"].strip() + row.rsplit(None, 1)[1] + ".c" for row in nonblank_lines(v["splits"])]


# 零阶 / 一阶划分 (rcsfzerofirst 把零阶空间的 CSF 排在各块最前面)：rangular、rci 的非默认设置按块询问 ICCUT，
# 即各块前 ICCUT 个 CSF 按变分处理，其余按一阶微扰处理 (只保留它们与零阶空间的相互作用和对角元)。ICCUT 取零阶空间
# CSF 列表各块的 CSF 数；该列表在加入流程时可能还不存在，所以输入中先写占位行 "@ICCUT 零阶列表 CSF 列表"，
# 运行时由 expand_iccut 换成各块的数 (导出的脚本中用 awk 计算，见 describe)。
ICCUT = "@ICCUT"
ICCUT_AWK = "f&&/^ *[*]/{print int(n/3);n=0;next} f&&NF{n++} /^CSF[(]s[)]:/{f=1} END{print int(n/3)}"


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
        zero, full = line.split()[1:3]
        try:
            zb, fb = csf_blocks(os.path.join(cwd, zero)), csf_blocks(os.path.join(cwd, full))
        except (OSError, StopIteration, IndexError) as e:
            raise RuntimeError(T("无法读取零阶空间的 CSF 列表 {z} 或 {f}：{e}", z=zero, f=full, e=e))
        if [s for _, s in zb] != [s for _, s in fb]:
            raise RuntimeError(T("零阶空间的 CSF 列表 {z} 与 {f} 的对称块不同 ({a} / {b})：两者的 J 和宇称块必须一一对应。",
                                 z=zero, f=full, a=" ".join(s for _, s in zb), b=" ".join(s for _, s in fb)))
        for (n, sym), (ncf, _) in zip(zb, fb):
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

    def validate(self, v):
        for key in ("zero", "full"):              # set_CSF_ZFlist.f90: FILNAM*256
            file_name(v[key], 256, "rcsfzerofirst", key)

    def stdin(self, v):
        self.validate(v)
        return answers([v["zero"].strip(), v["full"].strip()])

    def post(self, v):
        s = v["saveas"].strip()
        return [("cp", "rcsf.out", s)] if s else []

    def title(self, v):
        s = v["saveas"].strip()
        return f"rcsfzerofirst -> {s}" if s else "rcsfzerofirst"

    def inputs(self, v, run=None):
        return [v["zero"].strip(), v["full"].strip()]

    def outputs(self, v, run=None):
        return ["rcsf.out"]


def angular_files(run=None):
    run = run or {}
    count = int(run.get("np", 1))
    if count <= 1:
        return ["mcp.30"]
    return [os.path.join(run.get("mpitmp", "tmp_mpi"), f"{i:03d}", f"mcp{i:03d}.30") for i in range(count)]


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

    def inputs(self, v, run=None):
        return ["rcsf.inp"] + ([v["zero"].strip()] if v.get("zf") else [])

    def outputs(self, v, run=None):
        return angular_files(run)


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

    def validate(self, v):
        if v["use_file"]:                          # genrwf.f90: INFILE*128
            file_name(v["wfile"].strip() or "rwfn.out", 128, "rwfnestimate", "wfile")

    def stdin(self, v):
        self.validate(v)
        lines = ["y"]
        if v["use_file"]:
            lines += ["1", v["wfile"].strip(), v["forbs"]]
        lines += [v["method"], v["orbs"]]
        if v["method"] == "4":              # 方法 4 在轨道列表之后询问 Z 的增加量
            lines.append(v["delz"])
        return answers(lines)

    def inputs(self, v, run=None):
        return ["isodata", "rcsf.inp"] + ([v["wfile"].strip() or "rwfn.out"] if v["use_file"] else [])

    def outputs(self, v, run=None):
        return ["rwfn.inp"]


class RMcdhf(Step):
    output_fields = ("save",)
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

    def inputs(self, v, run=None):
        return ["isodata", "rcsf.inp", "rwfn.inp"] + angular_files(run)

    def outputs(self, v, run=None):
        state = v["save"].strip()
        return ["rwfn.out", "rmix.out"] + ([state + ext for ext in (".c", ".w", ".m")] if state else [])

    def removes(self, v):
        # rsave 移动混合系数文件，不能将 rmix.out 留作后续步骤的现成输入。
        return ["rmix.out"] if v["save"].strip() else []

    def result_files(self, v):
        return [v["save"].strip() + ".m"] if v["save"].strip() else ["rmix.out"]


class RSave(Step):
    output_fields = ("state",)
    key = name = prog = "rsave"
    fields = [
        Note("把 rcsf.inp / rwfn.out / rmix.out 等保存为 name.c / name.w / name.m ..."),
        Entry("state", "态名称", "odd2"),
    ]

    def args(self, v):
        return [v["state"].strip()]

    def title(self, v):
        return f"rsave {v['state']}"

    def inputs(self, v, run=None):
        return ["rcsf.inp", "rwfn.out", "rmix.out"]

    def outputs(self, v, run=None):
        return [v["state"].strip() + ext for ext in (".c", ".w", ".m")]

    def removes(self, v):
        return ["rmix.out"]

    def result_files(self, v):
        return [v["state"].strip() + ".m"]


class RCi(Step):
    output_fields = ("state",)
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

    def validate(self, v):
        state_name(v["state"], 127, "rci")            # rci92.f90: NAME*128

    def stdin(self, v):
        self.validate(v)
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

    def inputs(self, v, run=None):
        return ["isodata"] + [v["state"].strip() + ext for ext in (".c", ".w")] + (
            [v["zero"].strip()] if v.get("zf") else [])

    def outputs(self, v, run=None):
        return [v["state"].strip() + ".cm"]

    def result_files(self, v):
        return self.outputs(v)


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
        state_name(v["state"], 23, "jj2lsj")          # jj2lsj_code.f90: NAME*24
        if not v.get("coupling_files"):
            return
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,22}", v["state"].strip()):
            raise NameValidationError(T("Coupling 态名称须为不含空格的 ASCII 基名，最多 23 个字符"),
                                      v["state"].strip(), 23, "state")
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

    def inputs(self, v, run=None):
        return [v["state"].strip() + ext for ext in (".c", ".cm" if v["ci"] else ".m")]

    def outputs(self, v, run=None):
        return [v["state"].strip() + ext for ext in (
            (".lsj.lbl", ".lsj.c", ".lsj.j") if v.get("coupling_files") else (".lsj.lbl",))]


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
            raise NameValidationError(T("Coupling 态名称须为不含空格的 ASCII 基名，最多 23 个字符"),
                                      v["state"].strip(), 23, "state")
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

    def validate(self, v):
        state_name(v["state"], 23, "rhfs")            # hfs92.f90: NAME*24

    def stdin(self, v):
        self.validate(v)
        return answers(["y", v["state"].strip(), yn(v["ci"])])

    def post(self, v):
        if not v["lsj"]:
            return []
        return [("run", ["rhfs_lsj"], answers([v["state"].strip(), yn(v["ci"]), yn(v["sort"])]))]

    def title(self, v):
        return f"rhfs {v['state']}"

    def inputs(self, v, run=None):
        return ["isodata"] + [v["state"].strip() + ext for ext in (
            ".c", ".w", ".cm" if v["ci"] else ".m")] + (
            [v["state"].strip() + ".lsj.lbl"] if v["lsj"] else [])

    def outputs(self, v, run=None):
        return [v["state"].strip() + (".ch" if v["ci"] else ".h")]


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
            raise NameValidationError(T("rhfszeeman95 态名称须为不含空格的 ASCII 基名，最多 23 个字符"),
                                      v["state"].strip(), 23, "state")

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

    def validate(self, v):
        state_name(v["state"], 23, "ris4")            # ris.f90: NAME*24

    def stdin(self, v):
        self.validate(v)
        # 最后一问只在角系数文件不存在时出现；多给的一行不会被读取
        return answers(["y", v["state"].strip(), yn(v["ci"]), yn(v["higher"]), yn(v["save"])])

    def title(self, v):
        return f"ris4 {v['state']}"


class NameValidationError(ValueError):
    """保留原有异常消息，同时供 GUI/MCP 报告字段和限制；上限仍由各页提供。"""

    def __init__(self, message, name, limit, field, unit="characters"):
        super().__init__(message)
        self.details = dict(kind="name", field=field, name=name, limit=limit, unit=unit, reason=message)


def state_name(value, limit, prog, field="state"):
    """GRASP 把态名读入定长的字符变量，再按第一个空格截取：超长时被截断，或者截出空名字，程序打开错误的文件。
    limit 依据 compas/grasp 源码，见 docs/CONFIG_FORMAT.md 的“名称长度”。"""
    name = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", name) or len(name) > limit:
        raise NameValidationError(
            T("{p} 的态名称“{s}”须为英文字母、数字、下划线、点或短横线 (不以点或短横线开头)，最多 {n} 个字符",
              p=prog, s=name, n=limit), name, limit, field)
    return name


def file_name(value, limit, prog, field="file"):
    """程序读入的文件名：不含空白，UTF-8 字节数不超过 limit (超长时 Fortran 截断后打开错误的文件)。"""
    name = value.strip()
    if not name or re.search(r"\s", name) or len(name.encode("utf-8")) > limit:
        raise NameValidationError(T("{p} 的文件名“{s}”不能含空白，最多 {n} 个字节", p=prog, s=name, n=limit),
                                  name, limit, field, "bytes")
    return name


def tool_state(value):
    """工具按态名拼接文件名；rasfsplit 还把它传给未加引号的 cp。"""
    state = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}", state):
        raise NameValidationError(T("态名称须为 1–80 个英文字母、数字、下划线、点或短横线，且不能以点或短横线开头"),
                                  state, 80, "state")
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

    def inputs(self, v, run=None):
        return ["isodata"] + [v["state"].strip() + ext for ext in (
            ".c", ".w", ".cm" if v["ci"] else ".m")]

    def outputs(self, v, run=None):
        return [v["state"].strip() + (".ci" if v["ci"] else ".i")]



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
        state_name(v["state"], 64, "rmixextract")     # rmixextract.f90: StrInput*64
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
        if not 1 <= len(files) <= 20:
            raise ValueError(T("请给出 1–20 个文件名，每个不超过 80 个字符"))
        for name in files:
            if len(name) > 80:
                raise NameValidationError(T("请给出 1–20 个文件名，每个不超过 80 个字符"), name, 80, "files")
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

    def validate(self, v):
        for f in shlex.split(v["files"]):          # rlevels.f90: strInFile*80
            file_name(f, 80, "rlevels", "files")

    def args(self, v):
        self.validate(v)
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

    def validate(self, v):
        file_name(v["tfile"], 80, "rtabtrans2", "tfile")       # rtabtrans2.f90: FILENAME1*80

    def stdin(self, v):
        self.validate(v)
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

    def validate(self, v):
        for key in ("init", "final"):              # fname.f90: NAME(2)*24
            state_name(v[key], 23, "rbiotransform", key)

    def stdin(self, v):
        self.validate(v)
        return answers(["y", yn(v["ci"]), v["init"], v["final"], "y"])

    def inputs(self, v, run=None):
        return ["isodata"] + [v[key].strip() + ext for key in ("init", "final")
                              for ext in (".c", ".w", ".cm" if v["ci"] else ".m")]

    def outputs(self, v, run=None):
        return [v[key].strip() + ext for key in ("init", "final")
                for ext in (".bw", ".cbm" if v["ci"] else ".bm")]


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

    def validate(self, v):
        for key in ("init", "final"):              # fname.f90: NAME(2)*24
            state_name(v[key], 23, "rtransition", key)

    def stdin(self, v):
        self.validate(v)
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

    def inputs(self, v, run=None):
        return ["isodata"] + [v[key].strip() + ext for key in ("init", "final")
                              for ext in (".c", ".bw", ".cbm" if v["ci"] else ".bm")]

    def outputs(self, v, run=None):
        return [v["init"].strip() + "." + v["final"].strip() + (".ct" if v["ci"] else ".t")]


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


STEPS = [RNucleus(), RcsfGenerate(), RcsfInteract(), RcsfSplit(), RcsfZeroFirst(), RAngular(), RWfnEstimate(), RMcdhf(), RSave(),
         RCi(), Jj2lsj(), Coupling(), RHfs(), RHfsZeeman95(), RIs4(), RMixExtract(), RMixAccumulate(), RTabHfs(),
         RAsfSplit(), RLevels(),
         RTableLevels(), RTabTrans1(), RTabTrans2(),
         RBioTransform(), RTransition(), Custom()]

# 界面左侧程序列表的功能分组 (每个程序恰好出现一次)
STEP_GROUPS = [
    ("准备", ["rnucleus", "rcsfgenerate", "rcsfinteract", "rcsfsplit", "rcsfzerofirst"]),
    ("自洽场与 CI", ["rangular", "rwfnestimate", "rmcdhf", "rsave", "rci"]),
    ("性质与跃迁", ["rhfs", "rhfszeeman95", "ris4", "rbiotransform", "rtransition"]),
    ("结果与工具", ["jj2lsj", "coupling", "rlevels", "rtablevels", "rtabtrans1", "rtabtrans2", "rmixextract",
                  "rmixaccumulate", "rtabhfs", "rasfsplit", "custom"]),
]
assert sorted(k for _, keys in STEP_GROUPS for k in keys) == sorted(s.key for s in STEPS)


# ---------------------------------------------------------------------------
#  态组 (宇称)
#
#  odd 与 even 各有一套参数的程序页；其余程序页 (rnucleus, rlevels, rtablevels,
#  rbiotransform, rtransition, 自定义) 两组共用。文件名中的组名 (odd2.c, outodd_rci,
#  oddCI6 …) 在两组之间按名称替换对应。
# ---------------------------------------------------------------------------

GROUPS = ("odd", "even")
GROUP_STEPS = ("rcsfgenerate", "rcsfinteract", "rcsfsplit", "rcsfzerofirst", "rangular", "rwfnestimate", "rmcdhf",
               "rsave", "rci", "rmixextract", "rmixaccumulate", "rasfsplit",
               "jj2lsj", "coupling", "rhfs", "rhfszeeman95", "ris4")

# even 组与 odd 组默认值中物理内容不同的部分 (grasptest/case1 的 2s2p(2))
EVEN_DEFAULTS = {
    "rcsfgenerate": dict(lists=[dict(configs="1s(2,i)2s(1,i)2p(2,i)", active="2s,2p",
                                     jmin="1", jmax="5", nexc="0")]),
    "rmcdhf": dict(levels="1,2,3\n1,2,3\n1,2"),
    "rci": dict(levels="1,2,3\n1,2,3\n1,2"),
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
    import grasp_session
    try:
        grasp_session.write(workdir, data)
        return True
    except (OSError, ValueError):
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

    mpi_prefix 非空 (如 "mpirun -np 4") 时，有并行版本的程序写成 "<mpi_prefix> <prog>_mpi"。
    """
    out = (program_fallbacks([job]) if fallback else []) + [action_sh(a) for a in job["pre"]]
    args = " ".join(shlex.quote(x) for x in job["args"])
    if mpi_prefix and job.get("mpi"):
        cmd = f"{mpi_prefix} {job['prog']}_mpi {args}".rstrip()
    else:
        cmd = f"{shlex.quote(job['prog'])} {args}".rstrip()
    if job["log"]:
        cmd += f" > {shlex.quote(job['log'])}"
    if any(ln.startswith(ICCUT + " ") for ln in job["stdin"].split("\n")):
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
    if mpi_prefix:
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
        if mpi and cfg["np"] > 1:
            mpitmp = os.path.join(cwd, cfg["mpitmp"])
            os.makedirs(mpitmp, exist_ok=True)
            env["MPI_TMP"] = mpitmp
            # hwloc 的 GL 插件会连接图形显示，在 WSLg 下使 mpirun 启动时卡住
            env.setdefault("HWLOC_COMPONENTS", "-gl")
            argv = (shlex.split(cfg["mpirun"]) + ["-np", str(cfg["np"]),
                    self.resolve(bindir, argv[0] + "_mpi")] + list(argv[1:]))
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
        try:
            while True:
                data = os.read(fd, 65536)
                if not data:
                    break
                if logf:
                    logf.write(data)
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
        if job["prog"] in ("rmcdhf", "rmcdhf_mem"):
            err = check_mcp(cfg["cwd"], cfg["np"], cfg["mpitmp"])
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
