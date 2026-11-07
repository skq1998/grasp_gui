# Shared configuration format / 共享配置格式

A *configuration* describes a GRASP calculation — the parameters of each program and the pipeline — so that
someone else can import it into an empty directory and repeat the calculation from scratch. It contains no
results and no machine-specific settings (GRASP `bin` directory, MPI processes, `mpirun`, working directory).
The examples of the GRASP2018 manual shipped with the GUI (`demos/*.json`) use the same format.

“配置”描述一个 GRASP 计算 (各程序的参数和计算流程)，别人导入到空目录后可以从头重复这个计算。配置不含计算结果，也不含
与机器有关的设置 (GRASP `bin` 目录、MPI 进程数、`mpirun`、工作目录)。随程序提供的 GRASP2018 手册算例 (`demos/*.json`)
也采用这个格式。实现见 `grasp_config.py`。

Programs and AI agents that run GRASP themselves follow the short rules of [AGENT_SPEC.md](AGENT_SPEC.md); the
fields of every program tab are listed in [STEPS.md](STEPS.md).

自己运行 GRASP 的程序和 AI agent 按 [AGENT_SPEC.md](AGENT_SPEC.md) 的简短规则生成配置；各程序页的参数见 [STEPS.md](STEPS.md)。

## File / 文件

A UTF-8 JSON object. Current version: **2**.

File and clipboard input share a 32 MiB UTF-8 text limit, including any Markdown wrapper; attached-file limits
below still apply. 文件与剪贴板共用 32 MiB 的 UTF-8 文本上限（含 Markdown 包裹）；附带文件仍遵守下述限制。

```json
{
  "format": "grasp-gui-config",
  "version": 2,
  "created_by": "GRASP GUI 2.1.0",
  "package": "GRASP2018",
  "title": "Li I 2s and 2p",
  "summary": "…",
  "runtime": "about 1 minute",
  "author": "…",
  "created": "2026-11-01",
  "jobs": [
    {"step": "rnucleus", "vals": {"z": "3", "a": "7", "…": "…"}},
    {"step": "rcsfgenerate", "group": "odd", "vals": {"…": "…"}},
    {"command": {"title": "…", "prog": "…", "args": [], "stdin": "…", "log": "", "pre": [], "post": [], "mpi": false}}
  ],
  "files": [{"name": "my.inp", "text": "…"}],
  "reference": [{"file": "2s_3.cm", "energies": [-7.4477432154]}]
}
```

| Key | Required | Meaning / 含义 |
|---|---|---|
| `format` | yes | always `"grasp-gui-config"` |
| `version` | yes | format version (integer). Readers convert older versions; a newer version is refused with a request to update the GUI. 格式版本：旧版本读入时转换，比程序新的版本拒绝导入并提示更新。 |
| `created_by` | no | program that wrote the file, e.g. `"GRASP GUI 2.1.0"` (informational) |
| `package` | no | program package, default `"GRASP2018"`; `"GRASPG"` (V3) when a step uses a GRASPG tab (`*_csfg`) — such a configuration may also use the GRASP2018 tabs; a GRASP2018 configuration with a GRASPG tab is refused. V2 refuses `"GRASPG"`; other values (grasp-continuum, …) are reserved. 程序包；用到 GRASPG 程序页时为 GRASPG (V3)。 |
| `title`, `summary`, `runtime` | `title` recommended | a string, or one string per interface language: `{"zh": "…", "en": "…"}` |
| `author`, `created` | no | author, date (`YYYY-MM-DD`) |
| `id`, `order` | no | identifier and sort order of the examples shipped with the GUI |
| `jobs` | yes | the pipeline, in order (at least one step); see below |
| `files` | no | small text input files written into the working directory on import |
| `reference` | no | reference results to compare with: level energies (Hartree) of mixing coefficient files, in file order |

### Jobs / 计算步骤

* **Form step** `{"step", "group", "vals"}` — `step` is the key of a program tab (`rnucleus`, `rcsfgenerate`,
  `rmcdhf`, …, `custom`; see `STEPS` in `grasp_core.py`), `vals` the values of its form fields. `group` (`"odd"` or
  `"even"`, default `"odd"`) is used by the tabs that keep separate parameters for the two state groups
  (`GROUP_STEPS`). The program input (stdin), command line and file actions are **generated on import by the
  importing GUI**, so corrections of the input rules also apply to old configurations.
  Missing fields take their default values and unknown fields are ignored; both are reported to the user.
* **Command** `{"command": {…}}` — a step stored as the command itself (program, arguments, stdin, log file,
  file actions `pre` / `post`). Used only for pipeline steps added by GUI versions before 2.1, which did not record
  their form values. Less portable: the input is not regenerated.

* **表单步骤**：`step` 为程序页，`vals` 为表单参数；按态组保存参数的程序页用 `group` 指明 odd / even (默认 odd)。程序输入、
  命令行和文件操作由导入方的界面按当前规则**重新生成**。缺少的参数用默认值，不认识的参数忽略，并都告知用户。
* **命令**：直接保存命令本身，只用于 2.1 之前的版本加入流程、没有记录表单参数的步骤；不会重新生成输入，可移植性较差。

### Name lengths / 名称长度

GRASP reads state and file names into fixed-length character variables. A longer name is cut off silently, and
programs that split the name at its first blank get an empty name when the variable is full; they then open the
wrong file (for example jj2lsj with a 24-character state name opens `fort.21` and stops with "Not a Configuration
Symmetry List File"). The importing GUI checks these limits (`grasp_gui check`). Limits from compas/grasp
`9006157` (source file: variable):

| Program tab | Field | Limit | Source |
|---|---|---|---|
| jj2lsj, rhfs, ris4, rhfszeeman95, Coupling (jj2lsj files) | `state` | 23 characters | `jj2lsj_code.f90`, `hfs92.f90`, `ris.f90`, `hfszeeman06.f90`: `NAME*24` |
| rbiotransform, rtransition | `init`, `final` | 23 characters each | `fname.f90`: `NAME(2)*24` |
| rmixextract | `state` | 64 characters | `rmixextract.f90`: `StrInput*64` |
| rasfsplit, rmixaccumulate | `state` | 80 characters (GUI limit; program: 100 / 199) | `rasfsplit.f90`, `rmixaccumulate.f90` |
| rci | `state` | 127 characters | `rci92.f90`: `NAME*128` |
| rwfnestimate | `wfile` | 128 bytes | `genrwf.f90`: `INFILE*128` |
| rcsfzerofirst | `zero`, `full` | 256 bytes | `set_CSF_ZFlist.f90`: `FILNAM*256` |
| rtabtrans2 | `tfile` | 80 bytes | `rtabtrans2.f90`: `FILENAME1*80` |
| rlevels, rtabhfs | each of `files` | 80 bytes | `rlevels.f90`: `strInFile*80`; rtabhfs: GUI limit |

State names use letters, digits, `_`, `.` and `-` (not starting with `.` or `-`), no blanks or non-ASCII characters.
**Keep every state name at most 23 characters**, also in the forms without a limit of their own (`rmcdhf` `save`,
`rsave`, `rcsfsplit`, `rcsfgenerate` `saveas`): the same name is read later by jj2lsj, rhfs, rtransition and the
other 24-character programs. Names derived from it count too, such as the `name` + label files of rcsfsplit.

GRASP 把态名、文件名读入定长的字符变量：名字过长时被悄悄截断；按第一个空格截取名字的程序在变量写满时得到空名字，
会打开错误的文件 (例如 jj2lsj 遇到 24 个字符的态名时打开 `fort.21`，报 “Not a Configuration Symmetry List File”)。
导入方的界面按上表检查 (`grasp_gui check`)。态名只用英文字母、数字、`_`、`.`、`-` (不以 `.` 或 `-` 开头)，不含空格和
非 ASCII 字符。**所有态名都不要超过 23 个字符**，包括本身没有限制的表单 (rmcdhf 的 `save`、rsave、rcsfsplit、
rcsfgenerate 的 `saveas`)：后面的 jj2lsj、rhfs、rtransition 等程序还要读入这个名字；rcsfsplit 加上标签后的名字同样计算在内。

### Attached files / 附带文件

`{"name", "text"}`: a plain file name (no directory, not starting with `.`), UTF-8 text, at most 1 MB per file and
4 MB in total. Only for inputs the GUI cannot generate (e.g. files used by custom steps).

附带文件：普通文件名 (不含目录，不以 `.` 开头)，UTF-8 文本，每个最多 1 MB，合计最多 4 MB；只用于界面不能生成的输入文件。

## Import / 导入

1. The file is read and converted to the current version; it is refused if the format, version, package, a
   program tab or an attached file is invalid.
2. The import dialog shows the description, the programs needed (missing ones are marked), the attached files,
   compatibility warnings and — highlighted — every **custom command** (custom tab steps and stored commands).
   These can run any program; a configuration from a file with custom commands must be confirmed explicitly.
3. The configuration is imported into an empty directory: attached files are written and the pipeline and forms
   are stored as the session of that directory (`.grasp_gui.json`). A separate `.grasp_gui_inputs.json` stores only
   validated attached-input basenames for results-only cleanup, including after restarting. This local list is not
   part of the shared configuration or session format. *▶ Run all* computes it from scratch.

1. 读取并转换为当前版本；格式、版本、程序包、程序页或附带文件不合法时拒绝导入。
2. 导入对话框显示说明、所需的程序 (找不到的标出)、附带文件、兼容性提示，并醒目地列出全部**自定义命令** (“自定义”程序页和
   原样保存的命令)；这些命令可以运行任意程序，文件中的配置含有自定义命令时需要用户确认。
3. 导入到空目录：写入附带文件，流程和表单参数作为该目录的记录保存；另将附带输入基名写入 `.grasp_gui_inputs.json`，
   供重启后的只删结果操作保留输入。该本地清单不改变共享配置或工作目录记录的格式。点“▶ 运行全部”即可从头计算。

## Export / 导出

*File → Export configuration…* writes the current pipeline. Before saving, the dialog points out what may not
work elsewhere: steps stored as commands, absolute paths, custom commands.

“文件 → 导出配置…”导出当前流程；导出前提示可能影响可移植性的地方：按命令保存的步骤、绝对路径、自定义命令。

## Versions / 版本

| Version | GUI | Changes |
|---|---|---|
| 1 | 1.3.0 – 2.0.0 | examples only: `"demo_format": 1`, no `format` / `package` |
| 2 | 2.1.0 | `format`, `package`, `created_by`, `author`, `created`, command steps, attached files |

When the format changes, `VERSION` in `grasp_config.py` is increased and `upgrade()` gets a conversion from the
previous version, so that every older file stays readable.

格式改变时，`grasp_config.py` 中的 `VERSION` 加 1，并在 `upgrade()` 中加入从上一版本的转换，保证旧文件都能读入。
