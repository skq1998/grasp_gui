# Skipped-block mixing files

The four original files were supplied in [issue #57](https://github.com/skq1998/grasp_gui/issues/57),
generated with GRASP2018 `compas/grasp@9006157730a82ac839f2b4ff4e938bcba63a539e`.
The three binary files are decoded from the issue's base64 without modification.

- `skip1.m`: rmcdhf omits block 1; blocks 2 and 3 contain one level each.
- `ci.cm`: rci writes an empty block-1 header (`nev=0, twoj1=parity=999`), then blocks 2 and 3.
- `mid.cm`: rci writes block 1, an empty block-2 header, then block 3.
- `skip1.c`: the common CSF list, with blocks 1/2+, 1/2-, 3/2-.

`test_skipped_blocks_grasp.py` separately regenerates all three cases and compares E1 transitions against
independent subsets. Temporary fixture stdin retains the source-verified blank block answer, which the
current form cannot yet express (#58); production input generation and golden scripts remain unchanged.
