"""Unit tests for the desktop training pipeline (no GPU / heavy deps)."""
import json
import sys
from pathlib import Path

import httpx
import pytest

from core.ollama_client import OllamaClient
from desktop.training import TrainingError, TrainingParams, _absolutize_from, run_pipeline


def _params(tmp_path, **kwargs):
    dataset = tmp_path / "ds.jsonl"
    dataset.write_text(
        json.dumps({"messages": [{"role": "user", "content": "hi"}]}) + "\n",
        encoding="utf-8",
    )
    base = {"dataset": str(dataset), "adapter_name": "test-lora"}
    base.update(kwargs)
    return TrainingParams(**base)


def _noop_stop():
    return lambda: False


def test_pipeline_requires_pro(tmp_path, monkeypatch):
    import licensing

    def _raise(feature):
        raise licensing.ProRequiredError(f"{feature} requires Pro")

    monkeypatch.setattr(licensing, "ensure_pro", _raise)
    with pytest.raises(TrainingError, match="Pro"):
        run_pipeline(
            _params(tmp_path), log=lambda msg: None, stop_requested=_noop_stop(),
            data_dir=str(tmp_path),
        )


def test_pipeline_missing_dataset(tmp_path):
    with pytest.raises(TrainingError, match="Датасет не найден"):
        run_pipeline(
            TrainingParams(dataset=str(tmp_path / "nope.jsonl")),
            log=lambda msg: None,
            stop_requested=_noop_stop(),
            data_dir=str(tmp_path),
        )


def test_pipeline_no_trainer(tmp_path, monkeypatch):
    import desktop.training as training

    monkeypatch.setattr(training, "_detect_trainer", lambda log, env=None: (None, "", None))
    with pytest.raises(TrainingError, match="Окружение обучения"):
        run_pipeline(
            _params(tmp_path), log=lambda msg: None, stop_requested=_noop_stop(),
            data_dir=str(tmp_path),
        )


def test_pipeline_cancelled_before_merge(tmp_path, monkeypatch):
    import desktop.training as training

    flag = {"stop": False}

    def trainer(settings, dataset, max_samples):
        flag["stop"] = True
        return "data/lora_output/adapter"

    monkeypatch.setattr(training, "_detect_trainer", lambda log, env=None: (trainer, "fake", None))
    result = run_pipeline(
        _params(tmp_path),
        log=lambda msg: None,
        stop_requested=lambda: flag["stop"],
        data_dir=str(tmp_path),
    )
    assert result["cancelled"] is True
    assert result["adapter"] == "data/lora_output/adapter"
    assert result["registered"] is False


def test_pipeline_full_flow_registers_and_creates(tmp_path, monkeypatch):
    import finetune.export_gguf as export_mod
    import finetune.merge as merge_mod
    import desktop.training as training

    monkeypatch.chdir(tmp_path)
    llama_dir = tmp_path / "llama.cpp"
    llama_dir.mkdir()
    monkeypatch.setenv("LLAMA_CPP_DIR", str(llama_dir))

    def trainer(settings, dataset, max_samples):
        return "data/lora_output/adapter"

    monkeypatch.setattr(training, "_detect_trainer", lambda log, env=None: (trainer, "fake", None))

    def fake_merge(adapter_path, settings):
        merged = tmp_path / "merged"
        merged.mkdir(exist_ok=True)
        return str(merged)

    monkeypatch.setattr(merge_mod, "merge_lora", fake_merge)

    def fake_export(model_dir, settings, llama_cpp_dir=None):
        gguf_dir = Path(settings.gguf_dir)
        gguf_dir.mkdir(parents=True, exist_ok=True)
        gguf = gguf_dir / "model.q4_K_M.gguf"
        gguf.write_text("gguf", encoding="utf-8")
        (gguf_dir / "Modelfile").write_text(
            "FROM ./model.q4_K_M.gguf\nPARAMETER temperature 0.2\n", encoding="utf-8"
        )
        return str(gguf)

    monkeypatch.setattr(export_mod, "export_gguf", fake_export)

    created = {}

    class FakeClient:
        def create(self, name, modelfile):
            created["name"] = name
            created["modelfile"] = modelfile
            return "success"

    logs = []
    result = run_pipeline(
        _params(tmp_path, ollama_model_name="fluxion-test"),
        log=logs.append,
        stop_requested=_noop_stop(),
        data_dir=str(tmp_path / "data"),
        client=FakeClient(),
    )
    assert result["cancelled"] is False
    assert result["created"] is True
    assert result["registered"] is True
    assert result["ollama_model"] == "fluxion-test"
    assert created["name"] == "fluxion-test"
    assert created["modelfile"].startswith(
        f"FROM {(tmp_path / 'data' / 'gguf' / 'model.q4_K_M.gguf').resolve()}"
    )

    from finetune.marketplace import AdapterRegistry

    registry = AdapterRegistry(tmp_path / "data" / "lora_registry")
    adapters = registry.list()
    assert [a.name for a in adapters] == ["test-lora"]
    assert adapters[0].ollama_model == "fluxion-test"

    assert any("зарегистрирован" in line for line in logs)


def test_absolutize_from_keeps_absolute(tmp_path):
    modelfile = tmp_path / "Modelfile"
    modelfile.write_text(
        "FROM /abs/model.gguf\nPARAMETER temperature 0.2\n", encoding="utf-8"
    )
    text = _absolutize_from(modelfile, "/abs/model.gguf")
    assert "FROM /abs/model.gguf" in text


# ── training environment helpers ────────────────────────────────────────────


def test_env_python_path_windows(tmp_path):
    import desktop.training as training

    py = training.env_python_path(tmp_path / "venv")
    if training.os.name == "nt":
        assert py == tmp_path / "venv" / "Scripts" / "python.exe"
    else:
        assert py == tmp_path / "venv" / "bin" / "python"


def test_detect_training_env_requires_marker(tmp_path):
    import desktop.training as training

    env_dir = tmp_path / "training_env"
    env_dir.mkdir()
    assert training.detect_training_env(env_dir) is None

    py = training.env_python_path(env_dir)
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("", encoding="utf-8")
    (env_dir / training.ENV_MARKER).write_text(
        json.dumps({"python": str(py)}), encoding="utf-8"
    )
    assert training.detect_training_env(env_dir) == str(py)


def test_run_cmd_streams_output_and_success():
    import desktop.training as training

    logs = []
    training._run_cmd(
        [sys.executable, "-c", "print('hello'); print('world')"],
        log=logs.append,
        stop_requested=lambda: False,
    )
    assert logs == ["hello", "world"]


def test_run_cmd_failure_raises(tmp_path):
    import desktop.training as training

    with pytest.raises(TrainingError, match="кодом 1"):
        training._run_cmd(
            [sys.executable, "-c", "raise SystemExit(1)"],
            log=lambda msg: None,
            stop_requested=lambda: False,
        )


def test_run_cmd_stop_terminates_process():
    import desktop.training as training

    code = (
        "import time\n"
        "for i in range(50):\n"
        "    print(i, flush=True)\n"
        "    time.sleep(0.1)\n"
    )
    with pytest.raises(TrainingError, match="Остановлено"):
        training._run_cmd(
            [sys.executable, "-c", code],
            log=lambda msg: None,
            stop_requested=lambda: True,
        )


def test_install_training_environment_reports_missing_python(tmp_path, monkeypatch):
    import desktop.training as training

    monkeypatch.setattr(
        training, "find_host_python", lambda: (None, "нужен Python 3.11/3.12")
    )
    with pytest.raises(TrainingError, match="Python 3.11"):
        training.install_training_environment(
            tmp_path / "training_env",
            log=lambda msg: None,
            stop_requested=lambda: False,
        )


def test_install_training_environment_runs_pip(tmp_path, monkeypatch):
    import desktop.training as training

    commands = []

    def fake_run_cmd(cmd, log, stop_requested, extra_env=None):
        commands.append([str(part) for part in cmd])
        if "-m" in cmd and "venv" in cmd:
            py = training.env_python_path(cmd[-1])
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("", encoding="utf-8")

    monkeypatch.setattr(training, "_run_cmd", fake_run_cmd)
    monkeypatch.setattr(training, "find_host_python", lambda: ("py312", "ok"))
    result = training.install_training_environment(
        tmp_path / "training_env",
        log=lambda msg: None,
        stop_requested=lambda: False,
    )

    assert result == str(training.env_python_path(tmp_path / "training_env"))
    assert any("venv" in " ".join(c) for c in commands)
    assert any("pip" in " ".join(c) and "torch" in " ".join(c) for c in commands)
    assert any("unsloth" in " ".join(c) for c in commands)
    assert training.detect_training_env(tmp_path / "training_env") == result


def test_install_training_environment_autoinstalls_python(tmp_path, monkeypatch):
    import desktop.training as training

    commands = []

    def fake_run_cmd(cmd, log, stop_requested, extra_env=None):
        commands.append([str(part) for part in cmd])
        if "-m" in cmd and "venv" in cmd:
            py = training.env_python_path(cmd[-1])
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("", encoding="utf-8")

    monkeypatch.setattr(training, "_run_cmd", fake_run_cmd)
    monkeypatch.setattr(
        training, "find_host_python", lambda: (None, "нужен Python 3.11/3.12")
    )
    monkeypatch.setattr(training, "autoinstall_python", lambda log, stop: "fresh312")
    result = training.install_training_environment(
        tmp_path / "training_env",
        log=lambda msg: None,
        stop_requested=lambda: False,
        auto_install_python=True,
    )

    assert result == str(training.env_python_path(tmp_path / "training_env"))
    assert commands[0][0] == "fresh312"
    assert training.detect_training_env(tmp_path / "training_env") == result


def test_install_training_environment_skips_autoinstall_by_default(tmp_path, monkeypatch):
    import desktop.training as training

    def fail_autoinstall(log, stop):
        raise AssertionError("autoinstall must not run when flag is off")

    monkeypatch.setattr(
        training, "find_host_python", lambda: (None, "нужен Python 3.11/3.12")
    )
    monkeypatch.setattr(training, "autoinstall_python", fail_autoinstall)
    with pytest.raises(TrainingError, match="Python 3.11"):
        training.install_training_environment(
            tmp_path / "training_env",
            log=lambda msg: None,
            stop_requested=lambda: False,
        )


# ── offline wheel pack (Full artifact) ──────────────────────────────────────


def test_find_offline_wheels_detects_shipped_pack(tmp_path, monkeypatch):
    import desktop.training as training

    monkeypatch.delenv("FLUXION_OFFLINE_WHEELS", raising=False)
    assert training.find_offline_wheels(tmp_path) is None

    wheels = tmp_path / "training_pack" / "wheels"
    wheels.mkdir(parents=True)
    assert training.find_offline_wheels(tmp_path) == wheels


def test_find_offline_wheels_env_override(tmp_path, monkeypatch):
    import desktop.training as training

    override = tmp_path / "custom_wheels"
    override.mkdir()
    monkeypatch.setenv("FLUXION_OFFLINE_WHEELS", str(override))
    assert training.find_offline_wheels(tmp_path / "nowhere") == override


def test_install_training_environment_offline_uses_local_pack(tmp_path, monkeypatch):
    import desktop.training as training

    commands = []

    def fake_run_cmd(cmd, log, stop_requested, extra_env=None):
        commands.append([str(part) for part in cmd])
        if "-m" in cmd and "venv" in cmd:
            py = training.env_python_path(cmd[-1])
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("", encoding="utf-8")

    monkeypatch.setattr(training, "_run_cmd", fake_run_cmd)
    monkeypatch.setattr(training, "find_host_python", lambda: ("py312", "ok"))

    wheels = tmp_path / "pack"
    wheels.mkdir()
    logs = []
    result = training.install_training_environment(
        tmp_path / "training_env",
        log=logs.append,
        stop_requested=lambda: False,
        offline_wheels=wheels,
    )

    assert result == str(training.env_python_path(tmp_path / "training_env"))
    joined = [" ".join(c) for c in commands]
    torch_cmd = next(c for c in joined if "torch" in c)
    stack_cmd = next(c for c in joined if "unsloth" in c)
    for cmd_text in (torch_cmd, stack_cmd):
        assert "--no-index" in cmd_text
        assert f"--find-links {wheels}" in cmd_text
    assert "--index-url" not in torch_cmd
    assert not any("--upgrade" in c for c in joined)
    assert any("Офлайн" in line for line in logs)
    assert training.detect_training_env(tmp_path / "training_env") == result


def test_install_training_environment_offline_missing_pack(tmp_path, monkeypatch):
    import desktop.training as training

    monkeypatch.setattr(training, "find_host_python", lambda: ("py312", "ok"))
    with pytest.raises(TrainingError, match="Офлайн-пак"):
        training.install_training_environment(
            tmp_path / "training_env",
            log=lambda msg: None,
            stop_requested=lambda: False,
            offline_wheels=tmp_path / "missing",
        )


def test_autoinstall_python_runs_winget(tmp_path, monkeypatch):
    import desktop.training as training

    commands = []

    def fake_run_cmd(cmd, log, stop_requested, extra_env=None):
        commands.append([str(part) for part in cmd])

    monkeypatch.setattr(training, "_run_cmd", fake_run_cmd)
    monkeypatch.setattr(training, "_locate_windows_python", lambda: "C:\\Python312\\python.exe")
    result = training.autoinstall_python(log=lambda msg: None, stop_requested=lambda: False)

    assert result == "C:\\Python312\\python.exe"
    winget_cmd = commands[0]
    assert winget_cmd[1:] == [
        "install",
        "-e",
        "--id",
        "Python.Python.3.12",
        "--silent",
        "--accept-package-agreements",
        "--accept-source-agreements",
    ]


def test_autoinstall_python_requires_winget(monkeypatch):
    import desktop.training as training

    monkeypatch.setattr(training, "_locate_windows_python", lambda: None)
    real_shutil = __import__("shutil")

    def fake_which(name, **kwargs):
        return None if name == "winget" else real_shutil.which(name, **kwargs)

    monkeypatch.setattr("shutil.which", fake_which)
    with pytest.raises(TrainingError, match="winget"):
        training.autoinstall_python(log=lambda msg: None, stop_requested=lambda: False)


def test_autoinstall_python_fails_when_not_located(monkeypatch):
    import desktop.training as training

    monkeypatch.setattr(training, "_run_cmd", lambda *a, **k: None)
    monkeypatch.setattr(training, "_locate_windows_python", lambda: None)
    with pytest.raises(TrainingError, match="не найден"):
        training.autoinstall_python(log=lambda msg: None, stop_requested=lambda: False)


def test_locate_windows_python_scans_install_dirs(tmp_path, monkeypatch):
    import desktop.training as training

    py = tmp_path / "Programs" / "Python" / "Python312" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")

    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "missing"))
    found = training._locate_windows_python()
    assert found == str(py)


# ── env subprocess pipeline ────────────────────────────────────────────────


def test_pipeline_env_subprocess_flow(tmp_path, monkeypatch):
    import desktop.training as training

    monkeypatch.chdir(tmp_path)
    llama_dir = tmp_path / "llama.cpp"
    llama_dir.mkdir()
    monkeypatch.setenv("LLAMA_CPP_DIR", str(llama_dir))

    env_py = tmp_path / "training_env" / "Scripts" / "python.exe"
    commands = []

    def fake_run_cmd(cmd, log, stop_requested, extra_env=None):
        commands.append([str(part) for part in cmd])
        module = cmd[cmd.index("-m") + 1] if "-m" in cmd else ""
        if module == "finetune.train_unsloth":
            adapter = Path("data/lora_output/adapter")
            adapter.mkdir(parents=True, exist_ok=True)
        elif module == "finetune.merge":
            Path("data/merged_model").mkdir(parents=True, exist_ok=True)
        elif module == "finetune.export_gguf":
            gguf_dir = Path("data/gguf")
            gguf_dir.mkdir(parents=True, exist_ok=True)
            (gguf_dir / "merged_model.q4_K_M.gguf").write_text("gguf", encoding="utf-8")
            (gguf_dir / "Modelfile").write_text(
                "FROM ./merged_model.q4_K_M.gguf\n", encoding="utf-8"
            )
        if extra_env:
            assert "finetune" in extra_env["PYTHONPATH"] or "site-packages" not in extra_env["PYTHONPATH"]

    monkeypatch.setattr(training, "_run_cmd", fake_run_cmd)

    def noop_trainer(settings, dataset, max_samples):
        raise AssertionError("in-process trainer must not run in env mode")

    monkeypatch.setattr(
        training,
        "_detect_trainer",
        lambda log, env=None: (noop_trainer, "venv", str(env_py)),
    )

    logs = []
    created_models = {}

    class FakeClient:
        def create(self, name, modelfile):
            created_models["name"] = name
            created_models["modelfile"] = modelfile
            return "success"

    result = run_pipeline(
        _params(tmp_path, preset="low", ollama_model_name="fluxion-env"),
        log=logs.append,
        stop_requested=_noop_stop(),
        data_dir=str(tmp_path / "data"),
        client=FakeClient(),
    )

    assert result["cancelled"] is False
    assert result["registered"] is True
    assert result["created"] is True
    assert created_models["name"] == "fluxion-env"
    assert created_models["modelfile"].startswith("FROM ")
    assert "/./" not in created_models["modelfile"].splitlines()[0]
    modules = [c[c.index("-m") + 1] for c in commands if "-m" in c]
    assert "finetune.train_unsloth" in modules
    assert "finetune.merge" in modules
    assert "finetune.export_gguf" in modules
    train_cmd = next(c for c in commands if "finetune.train_unsloth" in c)
    assert "--base-model" in train_cmd
    assert "--epochs" in train_cmd
    assert any("Готов" in line or "зарегистрирован" in line for line in logs)


# ── OllamaClient.create ─────────────────────────────────────────────────────


class _FakeStream:
    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        pass

    def iter_lines(self):
        return iter(self._lines)


def test_ollama_client_create(monkeypatch):
    captured = {}

    def fake_stream(method, url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeStream(['{"status":"pulling manifest"}', '{"status":"success"}'])

    monkeypatch.setattr(httpx, "stream", fake_stream)
    client = OllamaClient("http://localhost:11434", "m")
    status = client.create("fluxion-test", "FROM /abs/model.gguf")

    assert status == "success"
    assert captured["url"] == "http://localhost:11434/api/create"
    assert captured["json"]["model"] == "fluxion-test"
    assert captured["json"]["modelfile"] == "FROM /abs/model.gguf"
    assert captured["json"]["stream"] is True
