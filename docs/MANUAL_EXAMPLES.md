# 手册算例清单 / Manual example inventory

本次核对的 GRASP 源码为 `9006157730a82ac839f2b4ff4e938bcba63a539e`。
Issue 引用的 [An Introduction to Relativistic Theory as Implemented in GRASP, Atoms 11 (2023), 7](https://doi.org/10.3390/atoms11010007)
是理论文章；有逐次程序输入的配套手册是 [GRASP Manual for Users, Atoms 11 (2023), 68](https://doi.org/10.3390/atoms11040068)。
以下章节号指后者；清单同时覆盖理论文章的应用实例。

The cited Atoms 11, 7 article supplies the theory. The step-by-step inputs are in the companion **GRASP Manual for Users**,
Atoms 11, 68. Section numbers below refer to that manual. A configuration uses the existing forms only; no program inputs
or configuration format were changed. “No directory” means absent from `grasptest` at the commit above.

## 编号计算算例 / Numbered calculations

| 手册章节 / Section | `grasptest` | 示例 / Configuration | 覆盖或限制 / Coverage or limitation |
|---|---|---|---|
| §6.1，Li I | example1 | example1 | 已有 / Existing |
| §6.2，B II | example2 | example2 | 新增 example2_coupling，独立安装 compas/coupling，可用 LK3 标记列能级 / New external Coupling workflow |
| §6.3，Si VIII | example3 | example3 | 新增 example3_coupling 的部分标记；手册 sed 修补不自动化，未生成完整 LK3/JK3 能级表 / Partial recoupling only, manual label repairs excluded |
| §6.4，Fe XV | example4 | example4 | 已有；可导出 MPI / Existing, MPI export available |
| §6.5，Ni XIV | example5 | example5 | 已有 / Existing |
| §6.6，扩展 MR | 无 / none | 暂无 / none | 缺 `rcsfmr` 程序页；还依赖 §6.5 的 LSJ 输出。不能用固定 MR 冒充按实际混合系数选择 / No rcsfmr form; requires §6.5 LSJ results and dynamic MR selection |
| §6.7，RCI 重启 | 无 / none | 暂无 / none | `rci` 页没有 restart 字段，且需要中断留下的 `rci.res` / No restart field; requires an interrupted run's rci.res |
| §6.8，Be I 自然轨道 | example8 | example8 | 已有 / Existing |
| §6.9，Ni XXV 诱导跃迁 | example9 | example9 | 已有；含原有 custom 步骤 / Existing, including its existing custom steps |
| §9，Mo XXXVIII | case1 | case1 | 已有 / Existing |
| §10，Li 样序列 | case2 | **case2** | 新增 C IV，Z=6；原脚本 Z=6–12 / New, one ion of the sequence |
| §11，Mg 样序列 | case3 | **case3** | 新增 Fe XV，Z=26；原脚本实际 Z=26–60，不是 Z=6–12 / New; corrected range from source |
| §12，Nd 场移 | case4 | case4 | 已有；fical 使用原有 custom 步骤 / Existing, with fical custom steps |
| §9.7、§10.3 | case1_mpi、case2_mpi | 同上 / same | 由 MPI 导出覆盖，不重复创建示例 / Covered by MPI export |

case2、case3 各自选择序列的第一个元素，走 `z < 7` / `z < 27` 分支，现场计算角向数据，不依赖前一个 Z 的文件。
保留完整 n=5 模型；不实现序列、跨目录搬运或 Octave/MATLAB 绘图。case3 的 `sh_collect` 只是把已有输出再复制到序列父目录，单目录示例不执行。
核数据原样取所选 Z：case2 A=12、质量 12.0107；case3 A=56、质量 55.845，核自旋和矩保持脚本中的演示值。

Both examples choose the first ion and compute their own angular data. They retain the full n=5 model, without sequence
or cross-directory operations. Nuclear inputs come from the selected Z branch, including the script's illustrative spin
and moments. The sequence-only collection and plotting steps are excluded.

## 正文教学例 / Other worked examples

| 章节 / Section | `grasptest` | 处理 / Disposition |
|---|---|---|
| §§5.1–5.8，关联模型、相互作用筛选、组合激发 | 无独立目录 / none | 新增 **manual_csfs**，9 次程序调用；输出各节 CSF 文件，不计算能量 / New complete CSF-only tutorial |
| §5.9，自定义轨道顺序 | 无 / none | `rcsfgenerate.order` 只允许 `* / r / s`，没有 `u` 和配套 `clist.ref` 工作流 / Unsupported user ordering |
| §5.10，jjgen | 无 / none | 缺 jjgen 程序页 / No jjgen form |
| §7.1，拆分 CSF | 无 / none | case2 已包含同样的 1s²2p、n=5 SDT 列表及 n=3/4/5 拆分 / Covered by case2 |
| §7.2，提取与压缩 | 无 / none | 新增 manual_tools：先重算 Li I，再提取和累积 / New complete Li I workflow |
| §7.3，轨道绘图 | 无 / none | rwfnplot 无程序页；GUI 波函数页提供绘图，但不等价于运行原始交互练习 / Viewer capability, not a runnable form workflow |
| §7.4，LSJ 超精细输出 | 无 / none | rhfs 的 lsj 字段可处理已有 example1 输出；不是另一个独立计算模型 / Supported on example1 results |
| §7.5，超精细表 | 无 / none | 新增 manual_tools；先运行 rhfs_lsj，再生成 hfs.tex / HFS-only table from calculated data |
| §§7.6–7.9，能级、跃迁及寿命表 | example1 等 / etc. | 现有示例使用 rtablevels / rtabtrans1 / rtabtrans2；手工标签编辑不自动化 / Existing table workflows, manual editing excluded |
| §7.10，含 LS 成分的表 | 无 / none | 无 perl_lscomp / lscomp.pl 页，且依赖先前结果 / No dedicated form |
| §7.11，按对称块拆 ASF | 无 / none | 新增 manual_asfsplit：先重算 Si VIII，再按对称块拆分 / New complete Si VIII workflow |
| §8，输出文件说明 | 对应已有例 / existing | 解释 §6 结果，不增加计算流程 / Output descriptions, not new workflows |
| §§9.2–9.6、10.2、11.3–11.5 | case1–3 | 实验比较、序列绘图和拟合，不是单个工作目录中的 GRASP 输入流程 / Analysis of existing results |
| §13.1，逐层收敛建议 | 无 / none | 方法建议，无独立完整输入；已有逐层示例 / General advice, existing layer-by-layer examples |
| §13.2，Mg I 的 HF 初估 | 无 / none | 完整教学要求先演示失败 SCF，再运行外部 hf、拼接轨道并转换；没有相应专用页及失败后继续的步骤语义 / Failure/recovery and external HF workflow not covered by existing forms |
| §13.3，No I 降低核电荷 | 无 / none | 要逐次原位编辑 isodata（含分数 Z）并保留其它核参数，且演示未收敛状态；没有相应操作页 / In-place nuclear-data edits and convergence recovery not represented |
| §13.4，非默认 SCF 控制 | 无 / none | 缺节点计数阈值、odamp 字段 / Missing node-threshold and damping controls |
| §13.5，U I 改网格 | 无 / none | 需重编译网格尺寸并输入非默认网格，现有页不支持 / Requires rebuilt grid and unsupported grid fields |
| §13.6，关联轨道不收敛 | example8 | 引用已有方法 4 示例，无新完整流程 / Refers to example8 |
| §14.1，Fe XIII 零阶 / 一阶 | 无 / none | 新增 **manual_zf**，11 步，五个能级，ICCUT=9/15/20 / New complete calculation |
| §14.2，波函数累积 | 无 / none | 新增 manual_accumulate：重算 §14.1，再选零阶空间并做 CI / New accumulation and CI workflow |
| §14.3，计算策略 | 无 / none | 方法建议，不给出独立完整的输入 / Strategy discussion |
| 理论文章 §5.1，Zn 核四极矩 | 无 / none | 研究结果与文献引用，没有完整输入 / No complete inputs |
| 理论文章 §5.2，核半径变化 | 无 / none | 研究结果；完整场移教学另见 case4 / Results, related worked workflow is case4 |
| 理论文章 §5.3，Al 样光谱 | 无 / none | 研究结果，没有完整输入 / No complete inputs |
| 理论文章 §5.4，Eu II 外场谱 | 无 / none | 研究结果，且需外场混合处理 / No complete inputs; external-field mixing needed |

## 核对方式 / Verification

- `tools/make_demos.py --check <grasptest>`：case2、case3 直接读取 shell heredoc，按已选分支替换 Z、n、k。
  case2：28 次完全一致、3 次等价；case3：31 次完全一致、2 次等价，没有其它差异。
- 等价差异：首层直接读取 DF.w / DFeven.w / DFodd.w，而非脚本复制出的同内容文件；case2 的 rnucleus
  最后一行多余 `n` 没有被程序读取（`src/appl/rnucleus90/geniso.f90` 在写入四极矩后关闭文件并 STOP）。
- 正文的独立输入转录及来源见 [manual_inputs](../tools/manual_inputs/README.md)。manual_csfs 的 9 次输入完全一致，
  输出各块 CSF 数与手册一致。manual_zf 为 7 次一致、4 次等价：现代 rcsfzerofirst 显式给出 MR/full 列表，
  rangular/rci 的 ICCUT 从文件读取代替固定数字，rwfnestimate 显式读保存的 mr.w。
- case2、case3 参考值读取自同 Z 作者脚本的真实结果；导出脚本重算按 `1e-7 Hartree` 比较。
  manual_zf 参考值来自配置导出脚本的真实运行。manual_csfs 只生成 CSF，没有能量参考值。
- 全部新增示例无需 custom。Golden 只新增对应的串行和 MPI 脚本，已有脚本保持不变。

New references are calculated, not transcribed from printed energies. The CSF-only tutorial instead checks the manual's
block sizes. Unsupported forms listed above remain follow-up work; none are added in this change.
