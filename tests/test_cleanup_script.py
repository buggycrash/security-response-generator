import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLEANUP_SCRIPT = PROJECT_ROOT / "cleanup.sh"
COMMON_SCRIPT = PROJECT_ROOT / "scripts" / "common.sh"


def make_cleanup_project(tmp_path: Path):
    project = tmp_path / "project"
    scripts = project / "scripts"
    fake_bin = tmp_path / "bin"
    install_dir = tmp_path / "home" / ".local" / "bin"
    scripts.mkdir(parents=True)
    fake_bin.mkdir()
    install_dir.mkdir(parents=True)
    (project / "cleanup.sh").write_text(
        CLEANUP_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (project / "setup.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (project / "srg").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (scripts / "common.sh").write_text(COMMON_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    (install_dir / "srg").symlink_to(project / "srg")

    ollama_log = tmp_path / "ollama.log"
    fake_ollama = fake_bin / "ollama"
    fake_ollama.write_text(
        "#!/usr/bin/env bash\n"
        "set -eu\n"
        'case "${1:-}" in\n'
        "  list)\n"
        "    printf 'NAME ID SIZE MODIFIED\\n'\n"
        "    printf 'gemma4:e4b-it-qat abc 1GB now\\n'\n"
        "    printf 'embeddinggemma:latest def 1GB now\\n'\n"
        "    ;;\n"
        "  rm)\n"
        '    printf \'%s\\n\' "$2" >> "$SRG_TEST_OLLAMA_LOG"\n'
        "    ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_ollama.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "SRG_TEST_OLLAMA_LOG": str(ollama_log),
    }
    return project, install_dir, ollama_log, env


def run_cleanup(project: Path, *args: str, user_input: str = "", env: dict | None = None):
    return subprocess.run(
        ["bash", str(project / "cleanup.sh"), *args],
        cwd=project.parent,
        env=env,
        input=user_input,
        text=True,
        capture_output=True,
        check=False,
    )


def test_cleanup_help_works_outside_project_directory(tmp_path):
    result = subprocess.run(
        ["bash", str(CLEANUP_SCRIPT), "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--keep-models" in result.stdout
    assert "--wipe-engagements" in result.stdout


def test_cleanup_removes_owned_launcher_and_configured_models(tmp_path):
    project, install_dir, ollama_log, env = make_cleanup_project(tmp_path)

    result = run_cleanup(
        project,
        user_input="REMOVE EXTERNAL SRG SETUP\n",
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert not (install_dir / "srg").exists()
    assert ollama_log.read_text(encoding="utf-8").splitlines() == [
        "gemma4:e4b-it-qat",
        "embeddinggemma",
    ]
    assert "Cleanup complete" in result.stdout
    assert 'export PATH="' in result.stdout


def test_cleanup_preserves_launcher_owned_by_another_checkout(tmp_path):
    project, install_dir, _, env = make_cleanup_project(tmp_path)
    launcher = install_dir / "srg"
    launcher.unlink()
    launcher.symlink_to(tmp_path / "old-project" / "srg")

    result = run_cleanup(
        project,
        "--keep-models",
        user_input="REMOVE EXTERNAL SRG SETUP\n",
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert launcher.is_symlink()
    assert "not owned by this checkout" in result.stdout


def test_engagement_wipe_requires_second_exact_confirmation(tmp_path):
    project, install_dir, _, env = make_cleanup_project(tmp_path)
    private_file = project / "engagements" / "customer" / "private_context" / "secret.md"
    private_file.parent.mkdir(parents=True)
    private_file.write_text("secret", encoding="utf-8")

    result = run_cleanup(
        project,
        "--keep-models",
        "--wipe-engagements",
        user_input="REMOVE EXTERNAL SRG SETUP\nno\n",
        env=env,
    )

    assert result.returncode == 1
    assert private_file.exists()
    assert (install_dir / "srg").is_symlink()
    assert "Nothing was changed" in result.stdout


def test_confirmed_engagement_wipe_preserves_demo_seed(tmp_path):
    project, _, _, env = make_cleanup_project(tmp_path)
    customer_file = project / "engagements" / "customer" / "private_context" / "secret.md"
    customer_file.parent.mkdir(parents=True)
    customer_file.write_text("secret", encoding="utf-8")
    demo_private = project / "engagements" / "demo" / "private_context"
    demo_private.mkdir(parents=True)
    (demo_private / "demo-system.md").write_text("fictional", encoding="utf-8")
    (demo_private / "extra.md").write_text("sensitive", encoding="utf-8")
    demo_standards = project / "engagements" / "demo" / "customer_standards"
    demo_standards.mkdir()
    (demo_standards / ".gitkeep").write_text("", encoding="utf-8")
    (demo_standards / "extra.md").write_text("sensitive", encoding="utf-8")
    (project / ".srg").mkdir()

    result = run_cleanup(
        project,
        "--keep-models",
        "--wipe-engagements",
        user_input="REMOVE EXTERNAL SRG SETUP\nWIPE ENGAGEMENT DATA\n",
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert not (project / "engagements" / "customer").exists()
    assert not (project / ".srg").exists()
    assert (demo_private / "demo-system.md").read_text(encoding="utf-8") == "fictional"
    assert not (demo_private / "extra.md").exists()
    assert (demo_standards / ".gitkeep").exists()
    assert not (demo_standards / "extra.md").exists()
