# Writing configurations from scripts and AI agents / 由脚本和 AI agent 生成配置

This is the minimal set of rules for a program or AI agent that runs GRASP2018 and should also hand over a
[configuration](CONFIG_FORMAT.md) of the calculation. The person who receives it imports the file into GRASP GUI
(*File → Import configuration file…*), sees every step as a filled-in form, can rerun the pipeline and compare the
results with the energies the agent obtained.

An agent can also provide the configuration as JSON text (plain or in a Markdown code block); the user can
choose *From clipboard* in the import dialog. agent 也可直接给出配置文本，用户在导入对话框中从剪贴板读入。

本文是最简规范：让运行 GRASP2018 的脚本或 AI agent 同时给出与本程序匹配的[配置文件](CONFIG_FORMAT.md)。操作者用
“文件 → 导入配置文件…”导入后，每一步都显示为填好的表单，可以逐步核对参数、重新计算，并与 agent 给出的能量对照。

## Principle / 原则

**The configuration is the calculation.** Write the configuration first and run GRASP with the script generated from
it; never feed GRASP input written by hand. Then the configuration reproduces exactly what was run.

**配置就是计算本身。** 先写配置，再用由配置生成的脚本运行 GRASP；不要手写程序的交互输入。这样配置与实际运行的计算完全一致。

## Workflow / 流程

```bash
grasp_gui steps > STEPS.md                     # field reference of this GUI version (also docs/STEPS.md)
grasp_gui check calc.json                      # must end with "configuration can be imported", ideally no warnings
grasp_gui script calc.json --bindir "$GRASP/bin" [--mpi "mpirun -np 8"] > run.sh
sh run.sh                                      # in an empty directory holding only the attached files
grasp_gui reference oddCI.cm evenCI.cm         # paste the output as "reference" into calc.json
grasp_gui check calc.json
```

Without the GUI binary, `python3 grasp_config.py …` in the source tree does the same (standard library only).
If a step has to change, edit the configuration and generate the script again; do not edit `run.sh`.

1. `steps` 列出各程序页的参数 (键、类型、默认值、含义)；2. 写配置并用 `check` 检查；3. 用 `script` 生成脚本，在只含附带文件的
空目录中运行；4. 用 `reference` 取得结果文件的能级能量，填入配置的 `reference`。没有程序文件时可在源码目录中用
`python3 grasp_config.py …`。需要修改时改配置、重新生成脚本，不要改脚本。

## Rules / 规则

1. Top level: `"format": "grasp-gui-config"`, `"version": 2`, `"package": "GRASP2018"`, `title`, `summary` (the
   physics: system, states, model, what is computed), `runtime`, `created_by` (agent name), `jobs`, and `reference`
   when results exist.
2. **One job per program run**, in execution order: `{"step": key, "group": "odd"|"even", "vals": {...}}`.
   Keys and fields are listed in [STEPS.md](STEPS.md). Give every field. Values are strings, except `bool`
   fields (`true`/`false`) and the `lists` of `rcsfgenerate`; `text` fields use `\n` between lines (e.g. one line of
   levels per J block). `choice` fields take one of the listed codes.
3. `group` (only for the tabs that have it): `"odd"` or `"even"` by the parity of the states the step works on. It
   chooses the form set in the GUI; file names are free.
4. Steps exchange files through the working directory by name, as in GRASP (`rcsf.inp`, `rwfn.inp`, `isodata`,
   `name.c/.w/.m/.cm`). Use the copy fields of the forms (`saveas`, `csf`, `copyfrom`, `save`, …) instead of separate
   copy commands. The examples in `demos/` show the usual order (rnucleus → rcsfgenerate → rangular → rwfnestimate →
   rmcdhf → … → rci → jj2lsj …).
5. Nothing machine-specific: no absolute paths, GRASP bin directory, MPI process count or `mpirun` command.
6. `custom` (any program with literal stdin) only for programs that have no tab; say why in `summary`. The operator
   has to confirm these steps on import. Do not write `{"command": …}` jobs (they exist for old GUI versions).
7. `files`: only inputs the GUI cannot generate (used by custom steps), plain names, UTF-8 text, ≤ 1 MB each.
8. Every manual action that changed the results must be a job of the configuration. Calculations done outside it
   are not covered by the configuration; mention them in `summary`.
9. `reference`: the output of `grasp_gui reference` for the final mixing coefficient files (Hartree, all levels in
   file order), unchanged.
10. **State names: at most 23 characters**, letters, digits, `_`, `.`, `-` only. GRASP programs such as jj2lsj, rhfs
   and rtransition read names into 24-character variables and open the wrong file otherwise. File-name limits of
   other fields: [CONFIG_FORMAT.md, Name lengths](CONFIG_FORMAT.md#name-lengths--名称长度). `grasp_gui check`
   reports violations.

1. 顶层写 `format`、`version` (2)、`package` (GRASP2018)、`title`、`summary` (体系、能级、模型、计算内容)、`runtime`、
   `created_by` (agent 名称)、`jobs`，有结果时写 `reference`。
2. **每运行一次程序对应一步**，按执行顺序；程序页和参数见 [STEPS.md](STEPS.md)，参数全部给出。除 bool 和 rcsfgenerate 的
   lists 外都是字符串，多行参数用 `\n` 分行 (如每个 J 块一行能级)，choice 取所列代码。
3. `group` 按该步所处理的态的宇称取 odd / even，只决定在界面中放在哪一组表单，文件名不受限制。
4. 各步按文件名通过工作目录交换文件；复制文件用表单中的字段 (`saveas`、`csf`、`copyfrom`、`save` 等)，不另加复制命令。
   常见顺序见 `demos/` 中的算例。
5. 不写与机器有关的内容：绝对路径、GRASP 程序目录、MPI 进程数、`mpirun`。
6. 只有没有程序页的程序才用 `custom` (原样写输入)，并在 `summary` 中说明原因；导入时需操作者确认。不写 `{"command": …}`
   步骤 (只用于旧版本界面)。
7. `files` 只放界面不能生成的输入 (自定义步骤用到的)，普通文件名，UTF-8 文本，每个不超过 1 MB。
8. 影响结果的每个手工操作都要是配置中的一步；配置以外的计算在 `summary` 中说明。
9. `reference` 原样填入 `grasp_gui reference` 对最终混合系数文件的输出 (Hartree，按文件中的顺序列出全部能级)。
10. **态名最多 23 个字符**，只用英文字母、数字、`_`、`.`、`-`。jj2lsj、rhfs、rtransition 等程序把态名读入 24 个字符的变量，
   超长时会打开错误的文件。其他字段的长度限制见 [CONFIG_FORMAT.md 的“名称长度”](CONFIG_FORMAT.md#name-lengths--名称长度)；
   `grasp_gui check` 会报告超限。

## Minimal example / 最小示例

```json
{
 "format": "grasp-gui-config", "version": 2, "package": "GRASP2018",
 "created_by": "example agent",
 "title": "Li I 2s 2S1/2: Dirac-Hartree-Fock",
 "summary": "Dirac-Hartree-Fock for the 2s ground state of Li I.",
 "runtime": "a few seconds",
 "jobs": [
  {"step": "rnucleus", "vals": {"z": "3", "a": "7", "revise": false, "rms": "", "skin": "2.3",
                                "mass": "6.941", "spin": "1.5", "mu": "3.2564268", "q": "-0.040"}},
  {"step": "rcsfgenerate", "group": "even", "vals": {"order": "*", "core": "0", "saveas": "2s_DF.c",
   "lists": [{"configs": "1s(2,i)2s(1,i)", "active": "2s", "jmin": "1", "jmax": "1", "nexc": "0"}]}},
  {"step": "rangular", "group": "even", "vals": {"csf": "2s_DF.c", "zf": false, "zero": ""}},
  {"step": "rwfnestimate", "group": "even", "vals": {"use_file": false, "wfile": "", "forbs": "*",
                                                    "method": "2", "orbs": "*", "delz": "0"}},
  {"step": "rmcdhf", "group": "even", "vals": {"mem": false, "levels": "1", "weights": "5", "uweights": "",
   "vary": "*", "spec": "*", "nscf": "100", "log": "", "save": "2s_DF"}}
 ],
 "reference": [{"file": "2s_DF.m", "energies": [-7.4335330947]}]
}
```

## What the operator checks / 操作者如何核验

Import into an empty directory: the dialog lists the programs needed, warnings, custom commands and the reference
energies. Each pipeline step opens its form (double-click) and shows the generated input (*Input preview*). *▶ Run
all* recomputes everything; the *Levels* tab shows the resulting levels for comparison with the reference.

导入到空目录时，对话框列出所需程序、警告、自定义命令和参考能量；双击流程中的步骤可以打开对应的表单，“输入预览”显示生成的
程序输入；“▶ 运行全部”从头重算，在“能级图”页与参考能量对照。

## Text for agent instructions / 可放入 agent 指令的文字

> When you run GRASP2018 calculations, also deliver a GRASP GUI configuration (`grasp-gui-config`, version 2) as
> specified in docs/AGENT_SPEC.md of https://github.com/skq1998/grasp_gui. Write the configuration first, check it
> with `grasp_gui check`, run GRASP only through the script from `grasp_gui script`, and add the output of
> `grasp_gui reference` for the final `.cm`/`.m` files. Use the field names of `grasp_gui steps`, no absolute paths,
> `custom` steps only for programs without a tab, and state names of at most 23 characters.

> 运行 GRASP2018 计算时，按 https://github.com/skq1998/grasp_gui 的 docs/AGENT_SPEC.md 同时给出 GRASP GUI 配置文件
> (`grasp-gui-config`，版本 2)：先写配置并用 `grasp_gui check` 检查，只通过 `grasp_gui script` 生成的脚本运行 GRASP，
> 再把 `grasp_gui reference` 对最终 `.cm`/`.m` 文件的输出填入配置。参数名以 `grasp_gui steps` 为准，不写绝对路径，
> 只有没有程序页的程序才用 `custom` 步骤，态名不超过 23 个字符。
