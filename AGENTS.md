# 开发规则 (给参与开发的人和 AI agent)

GRASP GUI 是 [GRASP2018](https://github.com/compas/grasp) 的图形界面 (Python 3 + PySide6)。本文件是参与开发的全部规则；
与对话中的说法冲突时以本文件为准。项目负责人审核每个 PR，仓库所有者决定合并和发布。

## 1. 开始之前

按顺序阅读：

1. `README.md`：功能概览。
2. `docs/INTERFACE.md`：每个窗口、菜单、控件的行为和保存的状态。**改界面前必读，改完要同步更新。**
3. `docs/DEVELOPMENT.md`：开发经过与设计决定 (为什么这样做)，后续计划。
4. 与任务有关的：`docs/CONFIG_FORMAT.md` (共享配置)、`docs/AGENT_SPEC.md` 和 `docs/STEPS.md` (供 agent 使用的配置规范)。
5. 任务所在的 GitHub issue：目标、范围、验收标准、不能改的地方。范围不清楚时先在 issue 中提问，不要自己扩大范围。

## 2. 环境

```bash
python3 -m venv .venv && . .venv/bin/activate      # Python 3.10 – 3.12
pip install -r requirements.txt pytest
python3 grasp_gui.py                                # 运行界面
python3 -m pytest tests                             # 测试 (见第 6 节)
```

GRASP2018 不随本项目提供。有 GRASP 时用环境变量 `GRASP_BINDIR=/path/to/grasp/bin` 指出程序目录；没有时只能运行不需要 GRASP
的测试，PR 中要写明这一点。代码和文档中不得出现本机路径。

## 3. 模块与依赖方向

| 模块 | 内容 | 依赖 |
|---|---|---|
| `grasp_core.py` | 各程序页的字段和提问顺序 (`STEPS`)、生成输入、脚本、`Runner`、翻译表 `EN`、`T()` | 只用标准库 |
| `grasp_config.py` | 共享配置的读取 / 检查 / 升级 / 导出，命令行 `check / script / steps / reference` | 只用标准库 |
| `grasp_bg.py` | 后台运行进程 (`grasp_gui _run DIR`)、Unix 套接字、运行状态文件 | 只用标准库 |
| `grasp_wfn.py`、`grasp_mix.py`、`grasp_trans.py` | 读取 `.w`、`.c/.m/.cm`、跃迁结果 | 只用标准库 |
| `grasp_gui.py` | 主窗口、命令行入口 | Qt |
| `calc_panel.py`、`wfn_panel.py`、`level_panel.py`、`gui_common.py` | 计算页、径向波函数页、能级图页、公共控件 | Qt |

- 只用标准库的模块**不能导入 Qt**：命令行工具和后台运行进程要在没有图形库的服务器上工作 (有测试检查)。
- 不增加第三方依赖。确实需要时先在 issue 中说明理由，由负责人决定。
- `grasp_gui.py` 在导入 PySide6 之前分派 `_run` 和配置命令，不要改变这个顺序。

## 4. 代码规范

- **跟随周围代码的写法**：命名、注释密度、函数长度与所在文件一致。优先修改已有的函数和结构，不另起一套。
  测试也一样：字符串用双引号，按 PEP 8 加空格 (`a = b`、`f(x, y)`)，每行一条语句。
- 注释和文档字符串用**中文**，简洁，说明“为什么”而不是复述代码。提交说明用英文 (见第 8 节)。
- **界面文字**一律写中文并经过 `T("…")`，同时在 `grasp_core.EN` 中加英文翻译；占位符用 `T("共 {n} 个", n=n)`，不要把
  f-string 传给 `T()`。程序页的字段名、说明、选项同样要有翻译 (有测试检查)。
- 只用 Python 3.10 的语法 (旧系统版用 Python 3.10 打包；有测试检查)。
- 不加没有用到的抽象、配置项和兼容层；不顺手重排、重命名与任务无关的代码 (会让审核和合并变难)。
- 出错时给用户看得懂的提示 (`QMessageBox` 或状态栏)，不要静默吞掉异常；只有确实无关紧要的错误才忽略，并注明原因。
- 通用代码 (`StepForm`、`grasp_config.check` 等) 中不要按程序名写特例：字段之间的依赖用字段属性 (如
  `enabled_by="<复选框的 key>"`：勾选后才能编辑)，参数校验写在程序页的 `validate(v)` 中 (不合法时抛出 `ValueError`)。

## 5. 兼容性 (审核时重点检查)

- **旧系统版**：`legacy` 分支用 PySide2 5.15 和一个替身包运行同一份代码。只用 Qt 5.15 和 Qt 6 都有的 API；用到新的
  Qt 类或写法时在 PR 中列出，由负责人补到 legacy 的替身包中。不要修改 `legacy` 分支。
- **Qt 版本**：打包用 Qt 6.8 LTS (≥ 6.8.3)；不要依赖 6.9 以后才有的功能。
- **共享配置**：已发布的配置文件必须照样能导入。给程序页加字段时设 `added="<首次包含它的版本>"`
  (如 `added="3.0.0"`；缺少时用默认值，不报警告，`docs/STEPS.md` 中标注该版本)；
  改变格式要提高 `grasp_config.VERSION` 并在 `upgrade()` 中转换旧版本，并且先在 issue 中讨论。
- **工作目录中的文件** (`.grasp_gui.json`、`.grasp_gui_run*`) 的格式同样要向前兼容。

## 6. 测试

```bash
python3 -m pytest tests                                  # 必须全部通过 (CI 也运行)
GRASP_BINDIR=/path/to/grasp/bin python3 -m pytest tests  # 加上用 GRASP 重算算例 (几秒钟)
GRASP_SLOW=1 GRASP_BINDIR=… python3 -m pytest tests      # 全部算例 (约一小时)
```

- `tests/golden/` 保存每个手册算例生成的 shell 脚本 (即全部程序输入)。它们变了说明**程序输入变了**：
  - 不是有意的：修代码，不要更新标准结果。
  - 是有意的 (修正或改进某个程序的输入)：`UPDATE_GOLDEN=1 python3 -m pytest tests -k golden` 重新生成，PR 中说明
    改了哪些程序的哪些提问、依据 (GRASP 源码文件和行号或手册章节)，并附上用真实 GRASP 重算算例的结果。
- 新功能和修复的 bug 都要有测试；界面测试见 `tests/test_gui.py` (offscreen，对话框用替身)。
- 界面改动另附截图 (中文和英文界面)。

## 7. GRASP 相关的规则

- 程序的交互输入 (提问顺序、默认回答) **必须对照 GRASP2018 的 Fortran 源码核对**，不能凭经验或猜测。
  错误的输入往往不报错，只是算出错误的结果，所以“程序能跑完”不能证明输入正确。
- GRASP 程序出错时退出码也可能是 0；判断成败要看输出文件。
- MPI 版本的程序 (`*_mpi`) 中路径不能超过 128 个字符。
- 多个 `.w` 文件对应同一组轨道时，不要猜用哪一个，要让用户选择或明确报错。
- 修改与物理有关的代码 (输入生成、结果读取、能级 / 跃迁计算) 时，PR 中要给出用真实 GRASP 核对的结果，或者写明没有核对。

## 8. Git 与 PR

- 基础分支是 issue 中指定的分支，没有指定时为 `main` (例如 V3 的任务基于 `v3`)。从它的最新提交建分支：
  `agent/<issue 编号>-<简短名称>`。一个 issue 一个分支一个 PR，改动尽量小 (几百行以内)。
- 开 PR 前 `git fetch && git rebase origin/<基础分支>`，确保测试通过。PR 的目标分支选基础分支，按模板填写
  (`.github/pull_request_template.md`)。分支可以推到仓库本身或自己的 fork；**推送后必须开 PR**，只推送分支不算完成，
  开 PR 失败时在 issue 中说明。
- 提交说明用英文：第一行概括改动 (不超过约 70 字符)，空一行，用短横线列出要点。不要提交生成的文件、日志、GRASP 的计算输出、
  `build/`、`dist/`。
- 不要：推送到 `main`、`legacy`、`v1`、`v3` 等保护分支，强制推送他人的分支，创建标签或 Release，修改 `grasp_core.VERSION`
  或版本记录 (这些在发布时由负责人完成)。
- 审核意见用新的提交回应 (不要改写已审核的历史)，并在 PR 中逐条回复。

## 9. 需要先在 issue 中讨论的改动

以下部分出错的代价大，改动前先提出方案，得到负责人同意后再写代码：

- `grasp_bg.py` 和 `Runner` (进程、信号、套接字、锁、后台运行)；
- 共享配置格式、工作目录中保存的文件格式；
- 任何程序页的提问顺序 (`grasp_core.STEPS`)；
- 依赖、打包 (`build.sh`)、CI；
- 新增标签页、菜单或改变主窗口布局。

## 10. 文档

- 改了界面：更新 `docs/INTERFACE.md` (中英文两部分)；`README.md` 只放简短的功能列表，不写细节。
- 改了程序页的字段：`python3 grasp_config.py steps > docs/STEPS.md` (有测试检查是否同步)。
- 设计上的重要决定写进 PR 描述；负责人在发布时整理进 `docs/DEVELOPMENT.md`。
