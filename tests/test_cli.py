from pathlib import Path

from task_harness.cli import _status, discover, init_project


def test_init_creates_executable_pending_package(tmp_path: Path) -> None:
    assert init_project(tmp_path) == 0
    packages = discover(tmp_path / ".scratch")
    assert len(packages) == 1
    assert _status(packages[0]) == "pending"
    assert (packages[0] / "issues" / "001-example.md").is_file()


def test_done_package_is_not_shortlisted(tmp_path: Path, capsys) -> None:
    init_project(tmp_path)
    capsys.readouterr()
    package = tmp_path / ".scratch" / "todo" / "example-feature"
    (package / "task-status.yaml").write_text("status: done\n", encoding="utf-8")
    from task_harness.cli import list_tasks

    list_tasks(tmp_path / ".scratch", short=True)
    assert capsys.readouterr().out == ""
