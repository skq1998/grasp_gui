"""配置文本与文件共用读取规则和序列化。"""

import json

import pytest

import grasp_config as config


def document():
    return dict(format=config.FORMAT, version=config.VERSION, title="中文配置",
                jobs=[dict(step="rnucleus", vals=config.STEPS["rnucleus"].defaults())])


@pytest.mark.parametrize("wrap", ["{}", " \n{}\n\t", "```json\n{}\n```", "```\n{}\n```", "```JSON\r\n{}\r\n```"])
def test_text_and_file_same(tmp_path, wrap):
    content = wrap.format(json.dumps(document(), ensure_ascii=False))
    path = tmp_path / "configuration.json"
    path.write_text(content, encoding="utf-8")
    assert config.loads(content) == config.load(path) == (document(), [])


@pytest.mark.parametrize("content", [None, False, "", " \n", "not JSON", "{}", "[]", "null", "{} {}",
                                      "```python\n{}\n```", "before\n```json\n{}\n```"])
def test_invalid_text(content):
    with pytest.raises(config.ConfigError):
        config.loads(content)


def test_raw_size_limit(tmp_path, monkeypatch):
    content = config.dumps(document())
    monkeypatch.setattr(config, "MAX_CONFIG", len(content.encode("utf-8")))
    assert config.loads(content)[0] == document()
    content += " "
    path = tmp_path / "large.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(config.ConfigError, match="太大"):
        config.loads(content)
    with pytest.raises(config.ConfigError, match="太大"):
        config.load(path)


def test_same_output_bytes(tmp_path):
    path = tmp_path / "configuration.json"
    config.save(document(), path)
    assert path.read_bytes() == config.dumps(document()).encode("utf-8")
    assert config.loads(path.read_text())[0] == document()


def test_rejected_save_keeps_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "configuration.json"
    path.write_text("keep this file")
    monkeypatch.setattr(config, "MAX_CONFIG", 1)
    with pytest.raises(config.ConfigError):
        config.save(document(), path)
    assert path.read_text() == "keep this file"



@pytest.mark.parametrize("content", ["{}", "[]", "not JSON", json.dumps({"format": "other"})])
def test_file_and_clipboard_report_same_format_error(tmp_path, content):
    path = tmp_path / "invalid.json"
    path.write_text(content)
    with pytest.raises(config.ConfigError) as clipboard:
        config.loads(content)
    with pytest.raises(config.ConfigError) as file:
        config.load(path)
    assert str(file.value) == str(clipboard.value)


def test_file_read_errors_keep_filename(tmp_path):
    missing = tmp_path / "missing.json"
    with pytest.raises(config.ConfigError, match="无法读取 missing.json"):
        config.load(missing)
    invalid = tmp_path / "invalid-utf8.json"
    invalid.write_bytes(b"\xff")
    with pytest.raises(config.ConfigError, match="无法读取 invalid-utf8.json"):
        config.load(invalid)
