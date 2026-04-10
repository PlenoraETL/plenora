"""
tests/arricchisci/unit/test_executable_runner_coverage.py
=========================================================
Coverage-focused unit tests for executable_runner.py.

Targets uncovered lines: 158-159, 162, 164, 171-176, 215, 217,
229-288, 309-351, 370-392, 397-406.
"""

import os
import sys
import tempfile
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime

import pytest

pytestmark = pytest.mark.unit

from app.core.arricchisci.executable_runner import (
    ExecutableResult,
    list_builtin_executables,
    run_executable,
    resolve_input_file,
    _run_python_module,
    _run_script,
    _resolve_file_source_path,
    _resolve_project_output_path,
    BUILTIN_REGISTRY,
)




# =============================================================================
# _run_script  (lines 309-351)
# =============================================================================

class TestRunScript:

    def test_script_not_found(self):
        result = _run_script(
            {"script_path": "/nonexistent/script.py"},
            "/in.csv", "/tmp/out", {},
        )
        assert result.success is False
        assert "Script non trovato" in result.error

    def test_script_path_none(self):
        result = _run_script({"script_path": None}, "/in.csv", "/tmp/out", {})
        assert result.success is False
        assert "Script non trovato" in result.error

    def test_script_path_missing_key(self):
        result = _run_script({}, "/in.csv", "/tmp/out", {})
        assert result.success is False
        assert "Script non trovato" in result.error

    def test_script_nonzero_exit_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = os.path.join(tmpdir, "bad_script.py")
            Path(script).write_text("import sys; sys.exit(1)", encoding="utf-8")

            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stderr = "Error: something failed"
            mock_result.stdout = ""

            config = {"script_path": script, "output_extension": ".zip"}
            with patch("subprocess.run", return_value=mock_result):
                result = _run_script(config, "/tmp/in.csv", tmpdir, {})

            assert result.success is False
            assert "codice 1" in result.error
            assert "something failed" in result.error

    def test_script_nonzero_no_stderr(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = os.path.join(tmpdir, "script.py")
            Path(script).write_text("pass", encoding="utf-8")

            mock_result = MagicMock()
            mock_result.returncode = 2
            mock_result.stderr = ""
            mock_result.stdout = ""

            config = {"script_path": script}
            with patch("subprocess.run", return_value=mock_result):
                result = _run_script(config, "/in.csv", tmpdir, {})

            assert result.success is False
            assert "nessun errore" in result.error

    def test_script_success_but_no_output_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = os.path.join(tmpdir, "script.py")
            Path(script).write_text("pass", encoding="utf-8")

            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stderr = ""
            mock_result.stdout = "done"

            config = {"script_path": script, "output_extension": ".zip"}
            with patch("subprocess.run", return_value=mock_result):
                result = _run_script(config, "/tmp/in.csv", tmpdir, {})

            assert result.success is False
            assert "output non trovato" in result.error

    def test_script_success_with_output_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = os.path.join(tmpdir, "script.py")
            Path(script).write_text("pass", encoding="utf-8")
            output_dir = os.path.join(tmpdir, "output")

            def fake_run(cmd, **kwargs):
                # Create the output file that the script "produces"
                Path(output_dir).mkdir(parents=True, exist_ok=True)
                # Find the -o argument to know where to write
                o_index = cmd.index("-o")
                out_path = cmd[o_index + 1]
                Path(out_path).write_bytes(b"script output data")
                mock = MagicMock()
                mock.returncode = 0
                mock.stderr = ""
                mock.stdout = "success"
                return mock

            config = {"script_path": script, "output_extension": ".csv"}
            with patch("subprocess.run", side_effect=fake_run):
                result = _run_script(config, "/tmp/in.csv", output_dir, {})

            assert result.success is True
            assert result.output_size_bytes == len(b"script output data")
            assert result.metadata["script"] == script
            assert result.metadata["stdout"] == "success"

    def test_script_params_added_to_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = os.path.join(tmpdir, "script.py")
            Path(script).write_text("pass", encoding="utf-8")

            captured_cmd = []

            def fake_run(cmd, **kwargs):
                captured_cmd.extend(cmd)
                mock = MagicMock()
                mock.returncode = 1
                mock.stderr = "err"
                mock.stdout = ""
                return mock

            config = {"script_path": script}
            params = {"site_id": "S1", "format": "geojson", "empty_val": ""}

            with patch("subprocess.run", side_effect=fake_run):
                _run_script(config, "/in.csv", tmpdir, params)

            # site_id and format should be in cmd, empty_val should NOT
            assert "--site_id" in captured_cmd
            assert "S1" in captured_cmd
            assert "--format" in captured_cmd
            assert "geojson" in captured_cmd
            # BUG: empty string "" is falsy in Python, so params with value ""
            # are silently dropped. This may be intentional, but if a user passes
            # an explicit empty string parameter it will be ignored.
            assert "--empty_val" not in captured_cmd

    def test_script_default_output_extension_is_zip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = os.path.join(tmpdir, "script.py")
            Path(script).write_text("pass", encoding="utf-8")

            captured_cmd = []

            def fake_run(cmd, **kwargs):
                captured_cmd.extend(cmd)
                mock = MagicMock()
                mock.returncode = 1
                mock.stderr = ""
                mock.stdout = ""
                return mock

            config = {"script_path": script}
            with patch("subprocess.run", side_effect=fake_run):
                _run_script(config, "/tmp/input.csv", tmpdir, {})

            # output file should end with .zip (default)
            o_index = captured_cmd.index("-o")
            out_path = captured_cmd[o_index + 1]
            assert out_path.endswith(".zip")

    def test_script_uses_sys_executable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            script = os.path.join(tmpdir, "script.py")
            Path(script).write_text("pass", encoding="utf-8")

            captured_cmd = []

            def fake_run(cmd, **kwargs):
                captured_cmd.extend(cmd)
                mock = MagicMock()
                mock.returncode = 1
                mock.stderr = ""
                mock.stdout = ""
                return mock

            config = {"script_path": script}
            with patch("subprocess.run", side_effect=fake_run):
                _run_script(config, "/in.csv", tmpdir, {})

            assert captured_cmd[0] == sys.executable


# =============================================================================
# _resolve_file_source_path  (lines 370-392)
# =============================================================================

class TestResolveFileSourcePath:

    def test_no_file_source_id_returns_none(self):
        assert _resolve_file_source_path({}) is None
        assert _resolve_file_source_path({"file_source_id": None}) is None

    def test_file_path_from_service(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "data.csv")
            Path(file_path).touch()

            mock_service = MagicMock()
            mock_service.get_file_source.return_value = {"file_path": file_path}

            with patch(
                "app.core.arricchisci.executable_runner.file_source_service",
                mock_service,
                create=True,
            ):
                # We need to patch the import inside the function
                with patch.dict("sys.modules", {
                    "app.services.file_service": MagicMock(
                        file_source_service=mock_service
                    )
                }):
                    result = _resolve_file_source_path({"file_source_id": "fs-1"})

            assert result == str(file_path)

    def test_fallback_to_path_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "data.csv")
            Path(file_path).touch()

            mock_service = MagicMock()
            mock_service.get_file_source.return_value = {"path": file_path}

            with patch.dict("sys.modules", {
                "app.services.file_service": MagicMock(
                    file_source_service=mock_service
                )
            }):
                result = _resolve_file_source_path({"file_source_id": "fs-1"})

            assert result == str(file_path)

    def test_file_path_doesnt_exist_fallback_to_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file in the fs dir for fallback
            fs_dir = os.path.join(tmpdir, "fs-1")
            os.makedirs(fs_dir)
            data_file = os.path.join(fs_dir, "uploaded.csv")
            Path(data_file).touch()

            mock_service = MagicMock()
            mock_service.get_file_source.return_value = {
                "file_path": "/nonexistent/file.csv"
            }

            mock_settings = MagicMock()
            mock_settings.FILE_SOURCES_DIR = Path(tmpdir)

            with patch.dict("sys.modules", {
                "app.services.file_service": MagicMock(
                    file_source_service=mock_service
                )
            }), patch("app.core.arricchisci.executable_runner.settings", mock_settings):
                result = _resolve_file_source_path({"file_source_id": "fs-1"})

            assert result == str(data_file)

    def test_hidden_files_skipped_in_dir_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fs_dir = os.path.join(tmpdir, "fs-2")
            os.makedirs(fs_dir)
            # Hidden file
            Path(os.path.join(fs_dir, ".hidden")).touch()
            # Real file
            real_file = os.path.join(fs_dir, "real.csv")
            Path(real_file).touch()

            mock_service = MagicMock()
            mock_service.get_file_source.return_value = {"file_path": "/nope"}

            mock_settings = MagicMock()
            mock_settings.FILE_SOURCES_DIR = Path(tmpdir)

            with patch.dict("sys.modules", {
                "app.services.file_service": MagicMock(
                    file_source_service=mock_service
                )
            }), patch("app.core.arricchisci.executable_runner.settings", mock_settings):
                result = _resolve_file_source_path({"file_source_id": "fs-2"})

            assert result is not None
            assert ".hidden" not in result

    def test_service_returns_none_returns_none(self):
        mock_service = MagicMock()
        mock_service.get_file_source.return_value = None

        with patch.dict("sys.modules", {
            "app.services.file_service": MagicMock(
                file_source_service=mock_service
            )
        }):
            result = _resolve_file_source_path({"file_source_id": "fs-none"})

        assert result is None

    def test_exception_returns_none(self):
        with patch.dict("sys.modules", {
            "app.services.file_service": MagicMock(
                file_source_service=MagicMock(
                    get_file_source=MagicMock(side_effect=Exception("db error"))
                )
            )
        }):
            result = _resolve_file_source_path({"file_source_id": "fs-err"})

        assert result is None


# =============================================================================
# _resolve_project_output_path  (lines 397-406)
# =============================================================================

class TestResolveProjectOutputPath:

    def test_no_project_id_returns_none(self):
        assert _resolve_project_output_path({}) is None
        assert _resolve_project_output_path({"source_project_id": None}) is None

    def test_success_returns_path(self):
        with patch.dict("sys.modules", {
            "app.utils.result_store": MagicMock(
                get_result_as_tempfile=MagicMock(return_value=Path("/tmp/result.zip"))
            )
        }):
            result = _resolve_project_output_path({"source_project_id": "proj-1"})

        assert result == str(Path("/tmp/result.zip"))

    def test_returns_none_when_no_tempfile(self):
        with patch.dict("sys.modules", {
            "app.utils.result_store": MagicMock(
                get_result_as_tempfile=MagicMock(return_value=None)
            )
        }):
            result = _resolve_project_output_path({"source_project_id": "proj-2"})

        assert result is None

    def test_exception_returns_none(self):
        with patch.dict("sys.modules", {
            "app.utils.result_store": MagicMock(
                get_result_as_tempfile=MagicMock(side_effect=Exception("db error"))
            )
        }):
            result = _resolve_project_output_path({"source_project_id": "proj-3"})

        assert result is None
