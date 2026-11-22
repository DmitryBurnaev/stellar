from pathlib import Path
import stat

import pytest

from src.modules.cli import generate_secrets


def test_generate_configuration_privately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    template = tmp_path / "template"
    config = tmp_path / "generated"
    template.write_text("DB_NAME=stellar\n")
    monkeypatch.setattr(generate_secrets, "ENV_TEMPLATE_PATH", template)
    monkeypatch.setattr(generate_secrets, "ENV_FILE_PATH", config)

    generate_secrets.main()

    values = dict(line.split("=", 1) for line in config.read_text().splitlines() if line)
    assert values.pop("DB_NAME") == "stellar"
    assert set(values) == {"DB_PASSWORD", "ADMIN_PASSWORD", "APP_SECRET_KEY"}
    assert len(set(values.values())) == 3
    output = capsys.readouterr().out
    assert all(len(value) >= 32 and value not in output for value in values.values())
    assert stat.S_IMODE(config.stat().st_mode) == 0o600

    before = config.read_bytes()
    with pytest.raises(SystemExit, match="already exists"):
        generate_secrets.main()
    assert config.read_bytes() == before
