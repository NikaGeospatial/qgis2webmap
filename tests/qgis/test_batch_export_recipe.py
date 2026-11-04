"""`docs/batch-export.md`, run as written.

A recipe nobody runs goes stale without anyone noticing: a renamed parameter,
an algorithm id that moved, a command line `qgis_process` no longer accepts.
So these tests take the commands out of the page itself - the `qgis_process`
lines and the Python console snippets - and run them against the real
`qgis_process` binary and the real `processing.run`, changing only the
placeholder paths.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nika_onlymap_exporter.packaging.runtime_manager import discover_runtime_dir
from nika_onlymap_exporter.processing.export_project import ExportProjectAlgorithm
from nika_onlymap_exporter.processing.provider import Qgis2WebMapProvider

qgis_core = pytest.importorskip("qgis.core")

REPO_ROOT = Path(__file__).resolve().parents[2]
RECIPE = REPO_ROOT / "docs" / "batch-export.md"
ALGORITHM_ID = "qgis2webmap:exportwebmap"
PLUGIN = "nika_onlymap_exporter"


def _code_blocks(language: str) -> list[str]:
    text = RECIPE.read_text(encoding="utf-8")
    return re.findall(rf"```{language}\n(.*?)```", text, flags=re.DOTALL)


def _run_command() -> list[str]:
    """The page's one `qgis_process run` line, as the shell would split it."""
    lines = [
        line.strip()
        for block in _code_blocks("sh")
        for line in block.splitlines()
        # Not indented: the folder loop's line is the same command in a loop.
        if line.startswith("qgis_process run")
    ]
    assert len(lines) == 1, f"expected one plain run command, found {lines}"
    return shlex.split(lines[0])


def _qgis_process() -> str:
    """The binary of the QGIS this session runs, before any other on the PATH:
    a different QGIS's `qgis_process` would not load this session's PyQGIS."""
    beside = Path(qgis_core.QgsApplication.prefixPath()) / "bin" / "qgis_process"
    if beside.is_file():
        return str(beside)
    found = shutil.which("qgis_process")
    if found:
        return found
    pytest.skip("qgis_process is not installed beside this QGIS")


def _towns_project(project, folder: Path, name: str = "towns", choose=None) -> Path:
    """A saved project with one file-backed layer, which `qgis_process` reopens.

    `choose(project, layer)`, when given, makes the dialog's choices in it
    before it is saved, as a user who exported from the dialog first would.
    """
    folder.mkdir(parents=True, exist_ok=True)
    data = folder / f"{name}.geojson"
    data.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [0.87, 51.15]},
                        "properties": {"name": "Ashford"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    layer = qgis_core.QgsVectorLayer(str(data), "Towns", "ogr")
    assert layer.isValid()
    project.addMapLayer(layer)
    if choose is not None:
        choose(project, layer)
    path = folder / f"{name}.qgz"
    assert project.write(str(path))
    project.clear()
    return path


def _process_env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    """A throwaway QGIS profile that loads the plugin from this checkout."""
    env = dict(os.environ)
    env.update(
        QGIS_CUSTOM_CONFIG_PATH=str(tmp_path / "qgis-profile"),
        QGIS_PLUGINPATH=str(REPO_ROOT),
        QT_QPA_PLATFORM="offscreen",
    )
    env.update(overrides)
    return env


def _run(
    args: list[str], env: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, env=env, cwd=cwd, capture_output=True, text=True, timeout=300
    )


class TestTheRecipeNamesWhatExists:
    def test_the_algorithm_id_is_the_registered_one(self, qgis_app) -> None:
        algorithm = ExportProjectAlgorithm()
        assert f"{Qgis2WebMapProvider().id()}:{algorithm.name()}" == ALGORITHM_ID
        assert _run_command()[2] == ALGORITHM_ID

    def test_every_parameter_is_documented_and_no_other(self, qgis_app) -> None:
        """A parameter added to the algorithm must reach the page's table, and
        the page must never name one the algorithm does not have."""
        algorithm = ExportProjectAlgorithm()
        algorithm.initAlgorithm()
        real = {p.name() for p in algorithm.parameterDefinitions()}

        text = RECIPE.read_text(encoding="utf-8")
        table = text.split("## The parameters", 1)[1].split("\n## ", 1)[0]
        documented = set(re.findall(r"`([A-Z_]+)`", table))
        named = {
            token.split("=", 1)[0]
            for token in _run_command()[_run_command().index("--") + 1 :]
        }
        for block in _code_blocks("python"):
            named |= set(re.findall(r'"([A-Z_]+)":', block))

        assert documented == real
        assert named <= real, f"the page uses {named - real}, which do not exist"


class TestQgisProcess:
    """The headless recipe, through the real binary."""

    def test_the_documented_command_writes_the_map(
        self, project, tmp_path, qgis_app
    ) -> None:
        if discover_runtime_dir() is None:
            pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
        binary = _qgis_process()
        source = _towns_project(project, tmp_path / "data")
        output = tmp_path / "web" / "towns.html"
        env = _process_env(tmp_path)

        enable = shlex.split(
            next(
                line
                for block in _code_blocks("sh")
                for line in block.splitlines()
                if line.startswith("qgis_process plugins enable")
            )
        )
        assert enable[-1] == PLUGIN
        enabled = _run([binary, *enable[1:]], env, tmp_path)
        assert enabled.returncode == 0, enabled.stdout + enabled.stderr

        command = []
        for token in _run_command()[1:]:
            if token.startswith("--PROJECT_PATH="):
                token = f"--PROJECT_PATH={source}"
            elif token.startswith("OUTPUT="):
                token = f"OUTPUT={output}"
            command.append(token)
        done = _run([binary, *command], env, tmp_path)

        assert done.returncode == 0, done.stdout + done.stderr
        html = output.read_text(encoding="utf-8")
        assert "<om-map" in html
        assert "<title>Towns</title>" in html

    def test_the_documented_command_keeps_the_choices_saved_in_the_project(
        self, project, tmp_path, qgis_app
    ) -> None:
        """What "What a run exports" promises: the dialog's choices saved in the
        file reach a `qgis_process` run, and a control the command leaves out
        keeps its saved value rather than the parameter's default."""
        if discover_runtime_dir() is None:
            pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
        from nika_onlymap_exporter.core.settings import load_state, save_state

        def choose(project, towns) -> None:
            hidden = qgis_core.QgsVectorLayer(
                "Point?crs=EPSG:4326", "Villages", "memory"
            )
            project.addMapLayer(hidden)
            state = load_state(project)
            state.for_layer(hidden.id()).include = False
            state.for_layer(towns.id()).popup = False
            state.show_legend = False
            state.basemap = "positron"
            save_state(project, state)

        binary = _qgis_process()
        source = _towns_project(project, tmp_path / "data", choose=choose)
        output = tmp_path / "web" / "towns.html"
        env = _process_env(tmp_path)
        enabled = _run([binary, "plugins", "enable", PLUGIN], env, tmp_path)
        assert enabled.returncode == 0, enabled.stdout + enabled.stderr

        command = []
        for token in _run_command()[1:]:
            if token.startswith("--PROJECT_PATH="):
                token = f"--PROJECT_PATH={source}"
            elif token.startswith("OUTPUT="):
                token = f"OUTPUT={output}"
            command.append(token)
        assert not any(token.startswith("LEGEND=") for token in command)
        done = _run([binary, *command], env, tmp_path)

        assert done.returncode == 0, done.stdout + done.stderr
        html = output.read_text(encoding="utf-8")
        assert 'label="Towns"' in html
        assert 'label="Villages"' not in html
        assert 'class="om-popup-title"' not in html
        assert '<om-widget type="legend"' not in html
        assert 'basemap="positron"' in html
        # The command's own TITLE still wins over the saved map name.
        assert "<title>Towns</title>" in html

    def test_without_the_runtime_it_refuses_and_says_what_to_do(
        self, project, tmp_path, qgis_app, monkeypatch
    ) -> None:
        """What "Before the first run" promises: it stops at once, with a
        non-zero status, and names the dialog as the way to install."""
        binary = _qgis_process()
        monkeypatch.delenv("ONLYMAP_RUNTIME_DIR", raising=False)
        if discover_runtime_dir() is not None:
            pytest.skip("a runtime is vendored in this checkout")
        source = _towns_project(project, tmp_path / "data")
        output = tmp_path / "web" / "towns.html"
        empty_cache = tmp_path / "no-runtime"
        empty_cache.mkdir()
        env = _process_env(tmp_path, ONLYMAP_RUNTIME_CACHE=str(empty_cache))
        env.pop("ONLYMAP_RUNTIME_DIR", None)
        _run([binary, "plugins", "enable", PLUGIN], env, tmp_path)

        done = _run(
            [
                binary,
                "run",
                ALGORITHM_ID,
                f"--PROJECT_PATH={source}",
                "--",
                f"OUTPUT={output}",
            ],
            env,
            tmp_path,
        )

        assert done.returncode != 0
        assert "QGIS2WebMap by NIKA" in done.stdout + done.stderr
        assert not output.exists()


# Run in a QGIS of its own: `processing` is the QGIS Processing plugin, which
# this test session never initialises, and the loop replaces the open project.
_CONSOLE = """
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])
from hermetic import start_qgis

app = start_qgis(Path(sys.argv[3]))
from qgis.core import QgsApplication

sys.path.append(QgsApplication.pkgDataPath() + "/python/plugins")
from processing.core.Processing import Processing

Processing.initialize()
from nika_onlymap_exporter.processing.provider import Qgis2WebMapProvider

# Held, as the plugin holds it: an unreferenced provider is collected.
provider = Qgis2WebMapProvider()
QgsApplication.processingRegistry().addProvider(provider)
code = Path(sys.argv[1]).read_text(encoding="utf-8")
exec(compile(code, "batch-export.md", "exec"), {"__name__": "__console__"})
QgsApplication.processingRegistry().removeProvider(provider)
app.exitQgis()
"""


class TestPythonConsole:
    def test_the_folder_loop_exports_every_project(
        self, project, tmp_path, qgis_app
    ) -> None:
        if discover_runtime_dir() is None:
            pytest.skip("OnlyMap runtime not available; set ONLYMAP_RUNTIME_DIR")
        folder = tmp_path / "projects"
        for name in ("a-towns", "b-towns"):
            _towns_project(project, folder, name)

        loop = next(b for b in _code_blocks("python") if "glob(" in b)
        assert 'Path("/path/to/projects")' in loop
        snippet = tmp_path / "loop.py"
        snippet.write_text(loop.replace("/path/to/projects", str(folder)), "utf-8")
        harness = tmp_path / "harness.py"
        harness.write_text(_CONSOLE, encoding="utf-8")

        done = _run(
            [
                sys.executable,
                str(harness),
                str(snippet),
                str(REPO_ROOT / "tests"),
                str(tmp_path / "qgis"),
            ],
            _process_env(tmp_path, PYTHONPATH=os.pathsep.join(sys.path)),
            tmp_path,
        )

        assert done.returncode == 0, done.stdout + done.stderr
        for name in ("a-towns", "b-towns"):
            html = (folder / f"{name}.html").read_text(encoding="utf-8")
            assert "<om-map" in html
