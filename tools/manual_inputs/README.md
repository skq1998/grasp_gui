# 手册交互输入转录 / Manual input transcripts

来源：Jönsson et al., [GRASP Manual for Users, Atoms 11 (2023), 68](https://doi.org/10.3390/atoms11040068),
§§5.1–5.8，pp. 21–33，及 §14.1，pp. 349–362，[开放获取全文](https://www.diva-portal.org/smash/get/diva2:1760743/FULLTEXT01.pdf)。
原文为 CC BY 4.0；作者署名及章节来源保留于此。

这些 `.in` 文件仅转录原文 `>>` 标出的程序回答，保留空回答，去掉启动程序与复制文件的 shell 命令。
§5.5 分为生成多参考列表 (`section55_mr.in`) 和相互作用筛选 (`section55.in`)。
它们不是由 GUI 输入生成器导出的标准结果，而是 `tools/make_demos.py --check` 的独立对照输入。

Transcribed answers from the manual's `>>` prompts, preserving blank answers and excluding shell commands.
Section 5.5 has separate multireference-generation and interaction-filtering transcripts. These are independent
manual inputs, not generated golden files. The source is licensed CC BY 4.0; attribution and section mapping are above.

`section141_00.in`–`section141_10.in` 是 §14.1 的 11 次调用；零阶排序在旧手册中无 stdin。
当前接口与旧手册的四处等价差异在生成器检查输出及清单中逐项说明，不改写转录以隐藏差异。
