"""手册示例生成器与独立转录的输入核对。"""

import json
from pathlib import Path

import pytest

from tools import make_demos as demos


@pytest.mark.parametrize("make", [demos.case2, demos.case3, demos.manual_csfs, demos.manual_zf])
def test_manual_demo_rebuilds_jobs(make):
    d = make()
    stored = json.loads((Path(demos.OUT) / (d.id + ".json")).read_text())
    # 旧示例允许缺少带 added 版本的新字段，比较补齐默认值后的表单。
    rebuilt = [dict(j, vals=demos.grasp_config.job_values(j)) for j in d.jobs]
    imported = [dict(j, vals=demos.grasp_config.job_values(j)) for j in stored["jobs"]]
    assert rebuilt == imported
    _, warnings = demos.grasp_config.check(stored)
    assert not warnings
    assert all(j["step"] != "custom" for j in d.jobs)


@pytest.mark.parametrize("make", [demos.manual_csfs, demos.manual_zf])
def test_book_transcripts(make, tmp_path):
    d = make()
    d.id = "renamed"
    assert demos.check(d, [str(tmp_path)]) == 0


def test_sequence_check_expands_selected_heredoc(tmp_path, capsys):
    d = demos.case2()
    d.id = "renamed"
    d.src = ""
    d.checks = d.checks[:1]
    # 同名 heredoc 的第二段故意不同；只能取所选 Z 分支的第一段。
    (tmp_path / "sh_nuc_seq").write_text("rnucleus <<EOF \n$z\n$m\nn\n$MM\n1\n1\n1\nn\nEOF\n"
                                         "rnucleus <<EOF\n99\nEOF\n")
    assert demos.check(d, [str(tmp_path)]) == 0
    assert "1 equivalent" in capsys.readouterr().out
    rule = d.equivalent
    d.equivalent = None
    assert demos.check(d, [str(tmp_path)]) == 1
    assert "1 differences" in capsys.readouterr().out
    d.equivalent = rule
    d.jobs[0]["vals"]["mass"] = "13"
    assert demos.check(d, [str(tmp_path)]) == 1
    assert "1 differences" in capsys.readouterr().out
