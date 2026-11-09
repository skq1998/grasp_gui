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

## Layer-by-layer work / 逐层工作

For each new layer, deliver the **complete configuration**, including the unchanged earlier steps, rather
than a fragment. The user imports from the clipboard in **Update current directory** mode, checks the
change summary, and runs only the appended steps. The input-file check reports missing prerequisites;
undeclared/custom steps need manual checking. It assumes declared producers finish successfully and checks
existence rather than physical correctness or file freshness. A missing file can therefore also mean the
starting step is too late. The user decides whether to run anyway.

After checking results in the GUI, use **Levels → Results summary…**: preview, select sections (the complete
configuration is an optional extra), then copy it or save it as a Markdown file. Return this text to the agent
for planning the next layer. The agent must not infer convergence from an exit code or a successful pipeline alone.

每一层都给出**完整配置**，保留前面的步骤和参数。用户从剪贴板选择“更新当前目录”，核查只新增了一层，
再运行新增步骤。文件检查按执行顺序推算已声明的输入/输出，假定前序步骤成功；只查文件存在性，不查物理正确性
或新旧。缺失也可能是运行起点太晚；自定义等未声明步骤需人工核查。是否仍然运行由用户决定。
在界面核查后，通过“能级图 → 结果摘要…”交给 agent，决定下一层：预览、勾选要包含的部分 (完整配置是可选的附加部分)，
再复制或保存为 Markdown 文件。不能把退出码或流程成功当作收敛证明。

### Summary format / 摘要格式

The summary (clipboard or `<directory>_summary.md`) is Markdown with separate JSON code blocks. The first block
records generation time, `omissions`, `warnings` and evidence limitations. Each selected section follows in this
fixed order; the complete configuration is an optional extra and comes last:

| Section / 部分 | JSON content / 内容 |
|---|---|
| Levels / 能级与主要成分 | Ordered result files; J, parity, index, Hartree, `zero_group`, relative cm⁻¹ within that Hamiltonian group, purity, top 3 jj components, LSJ labels |
| Layer changes / 能级随层的变化 | Per-group (odd/even) matches by J, parity, index; Hartree and consecutive-layer differences in cm⁻¹ |
| SCF convergence / SCF 收敛情况 | Log path, iteration count, last 3 iterations, `converged`; `sum`: final self-consistency and generalised occupation of every orbital from `name.sum`, `new` for orbitals added since the previous rmcdhf of the same group |
| CSF counts / 各态的 CSF 数 | `.c` files, counts for each J/parity block / 各态每个 J、宇称块的 CSF 数 |
| Step run status / 各步运行状态 | Step number, title, `status`, `basis`, run times / 步号、标题、状态、依据、时间 |
| Current complete configuration / 当前完整配置 | Optional; the unchanged shared-configuration object / 可选；原格式完整配置 |

`sum` comes from the rmcdhf summary file (`rmcdhf.sum`, renamed `name.sum` by rsave; compas/grasp 9006157
`endsum.f90:44–60,74–79`). It has no iteration history, only the final values. A small generalised occupation
of the orbitals new in this layer means the layer adds little; `possibly_stale` marks a `.sum` much older than the
mixing file. Without a log, `converged` stays `null` and `sum` is the only convergence evidence.
`sum` 取自 rmcdhf 的总结文件 (rsave 改名为 `name.sum`)，只有最终值，没有迭代过程。本层新增轨道 (`new`) 的广义占据数小，
说明这一层的贡献已经不大；没有日志时 `converged` 为 `null`，最终自洽程度是唯一的收敛线索。

`status` is `success`, `failed`, `not_run`, `running`, `stopped` or `unknown`. The latest matching record
wins. Current-GUI-session records compare parameter fingerprints; the persisted background state contains
only titles, so its fallback requires a unique contiguous title match and explicitly states that parameters
were not verified. Earlier runs are not reconstructed after restarting the GUI. No record means `unknown`,
not proof that the step was never run.

`converged` is `true`, `false` or `null`. Missing/reused/incomplete logs are explicitly identified, not assumed
converged. Rules follow compas/grasp **9006157**, `rmcdhf90/scf.f90:154–235,292–295`,
`improv.f90:173–185`, `newco.f90:116–128`, and `rscfvu.f90:161–168`: iteration headers, printed
self-consistency, optimized level energies/weights, weighted energies, iteration exhaustion/error markers
and normal program completion. `optimized_levels` follows the printed order; the log does not identify
their J/parity blocks. `block_average_energies_hartree` preserves the separate `Average energy` output
(`matrix.f90:315`), rather than labelling it as optimized eigenvalues. A single optimized level also has
its consecutive-iteration energy difference; multiple levels retain their individual values and weighted difference.
The printed maximum self-consistency is the maximum over printed orbital updates, not a reconstructed SCF
threshold. Normal completion with iterations and no exhaustion/error marker indicates the SCF convergence
path in that source. A running or partial log remains inconclusive.

Results use the indexed readers. Layers follow producer steps in the pipeline, **not filename sorting**.
Missing levels are marked unmatched; matching by J/parity/index does not guarantee the same physical state
through crossings. Repeated writes to the same result filename retain only its last producer; older values
cannot be recovered. Files without a known producer are shown without inferring a pipeline group or layer.
Relative energies always use the lowest included energy **within each Hamiltonian group** as zero.
Every level has `zero_group`; the first block's `relative_zero_hartree` is a group-name → Hartree-zero mapping,
recomputed from the retained levels after summary trimming. Pipeline odd/even groups are separate from these
Hamiltonian groups: matching settings share a zero across parities. `.m` is DC; `.cm` uses the matching `.csum`
header's speed of light, Breit frequency factor, VP, NMS, SMS and SE flags. All-off rci belongs to DC.
Missing/unreadable/malformed `.csum`, or one over 300 seconds older than its `.cm`, yields `unknown` and a warning;
all unknown files share an explicitly uncertain zero. This does not establish that their Hamiltonians agree. Each symmetry block defaults to the first 5 levels
(adjustable 1–200), with an explicit omitted count; partially written files are marked.

The text limit is **200 KiB of UTF-8**. Leading components are omitted first, followed by earlier or
unassociated results and their corresponding SCF/CSF information, with an explicit omission list. Full configuration JSON is never cut in the middle.
If the remaining selection cannot fit, copying is disabled until fewer sections/levels are selected;
export the full configuration separately when needed.

摘要整体是 Markdown，各部分用 JSON 代码块。首块含时间、省略清单、读入警告和证据边界。各步状态以最近可对应的记录
为准：会话内记录核对参数指纹，后台存档仅能按唯一连续标题对应，会明确说明未核验参数；重启后不能恢复更早的运行历史，
无记录标为 unknown。收敛 true/false/null 分别表示有据可判的收敛、未收敛、无法判断；读取日志仅保留最后三次迭代，
不把正常退出直接当作收敛。层序取自流程，缺失标记不对应，同名覆盖无法恢复旧值，未关联文件不猜流程组别；能级交叉时仍需核验
物理态身份。相对能量始终在各哈密顿量组内，以已包含的最低能量为零；裁剪后按剩余能级重新计算。
每行 `zero_group` 为哈密顿量组，首块 `relative_zero_hartree` 为“组名 → 零点总能量”的映射。
哈密顿量组与流程中的偶/奇宇称组不同：相同设置跨宇称共用零点。`.m` 为 DC，`.cm` 从同名 `.csum` 读取光速、
Breit 频率因子及 VP/NMS/SMS/SE 开关；全部关闭的 rci 属于 DC。
`.csum` 缺失、不可读、损坏或比 `.cm` 早超过 300 秒时归入 unknown 并警告；未知文件共用一个不保证可比的零点。每块能级有省略计数，尚未写完的混合系数文件注明 partial。
200 KiB 上限先省主要成分、再省较早或未关联结果，并列明；完整配置不截断，放不下时需少选内容或单独导出配置。

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
