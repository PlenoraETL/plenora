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
# run_executable: builtin resolution  (lines 158-159, 162, 164)
# =============================================================================

class TestRunExecutableBuiltinResolution:

    def test_builtin_resolves_to_python_module(self):
        """A valid builtin_id merges registry config and runs as python_module."""
        api_config = {
            "executable": {
                "type": "builtin",
                "builtin_id": "dxf_imdf",
            }
        }
        with patch(
            "app.core.arricchisci.executable_runner._run_python_module",
            return_value=ExecutableResult(success=True, output_path="/out.zip"),
        ) as mock_run:
            result = run_executable(api_config, "/tmp/in.dxf", "/tmp/out")

        assert result.success is True
        mock_run.assert_called_once()
        # The merged config should have both builtin fields and exec_config overrides
        call_config = mock_run.call_args[0][0]
        assert call_config["module"] == "dxf_imdf_sync.process"
        assert call_config["function"] == "process"
        # builtin_id and type from exec_config override
        assert call_config["builtin_id"] == "dxf_imdf"
        assert call_config["type"] == "builtin"

    def test_builtin_exec_config_overrides_registry(self):
        """exec_config fields override registry fields during merge."""
        api_config = {
            "executable": {
                "type": "builtin",
                "builtin_id": "dxf_imdf",
                "function": "custom_process",  # override
            }
        }
        with patch(
            "app.core.arricchisci.executable_runner._run_python_module",
            return_value=ExecutableResult(success=True),
        ) as mock_run:
            run_executable(api_config, "/tmp/in.dxf", "/tmp/out")

        call_config = mock_run.call_args[0][0]
        assert call_config["function"] == "custom_process"

    def test_script_type_dispatches_to_run_script(self):
        """exec_type == 'script' dispatches to _run_script."""
        api_config = {
            "executable": {
                "type": "script",
                "script_path": "/some/script.py",
            }
        }
        with patch(
            "app.core.arricchisci.executable_runner._run_script",
            return_value=ExecutableResult(success=True),
        ) as mock_run:
            result = run_executable(api_config, "/tmp/in.csv", "/tmp/out")

        assert result.success is True
        mock_run.assert_called_once()

    def test_python_module_dispatches_correctly(self):
        """exec_type == 'python_module' dispatches to _run_python_module."""
        api_config = {
            "executable": {
                "type": "python_module",
                "module": "some.module",
                "function": "run",
            }
        }
        with patch(
            "app.core.arricchisci.executable_runner._run_python_module",
            return_value=ExecutableResult(success=True),
        ) as mock_run:
            result = run_executable(api_config, "/tmp/in.csv", "/tmp/out")

        assert result.success is True
        mock_run.assert_called_once()

    def test_default_type_is_python_module(self):
        """When 'type' is missing, defaults to python_module."""
        api_config = {
            "executable": {
                "module": "some.module",
            }
        }
        with patch(
            "app.core.arricchisci.executable_runner._run_python_module",
            return_value=ExecutableResult(success=True),
        ) as mock_run:
            result = run_executable(api_config, "/tmp/in.csv", "/tmp/out")

        assert result.success is True
        mock_run.assert_called_once()

    def test_params_passed_through(self):
        """params dict is forwarded to the runner."""
        api_config = {"executable": {"type": "python_module", "module": "m"}}
        with patch(
            "app.core.arricchisci.executable_runner._run_python_module",
            return_value=ExecutableResult(success=True),
        ) as mock_run:
            run_executable(api_config, "/in.csv", "/out", params={"site_id": "S1"})

        call_params = mock_run.call_args[0][3]
        assert call_params == {"site_id": "S1"}

    def test_none_params_becomes_empty_dict(self):
        """params=None is converted to {} before passing to runner."""
        api_config = {"executable": {"type": "python_module", "module": "m"}}
        with patch(
            "app.core.arricchisci.executable_runner._run_python_module",
            return_value=ExecutableResult(success=True),
        ) as mock_run:
            run_executable(api_config, "/in.csv", "/out", params=None)

        call_params = mock_run.call_args[0][3]
        assert call_params == {}


# =============================================================================
# run_executable: execution_time and exception  (lines 171-176)
# =============================================================================

class TestRunExecutableTimingAndException:

    def test_execution_time_set_on_result(self):
        api_config = {"executable": {"type": "python_module", "module": "m"}}
        inner_result = ExecutableResult(success=True)
        with patch(
            "app.core.arricchisci.executable_runner._run_python_module",
            return_value=inner_result,
        ):
            result = run_executable(api_config, "/in.csv", "/out")

        assert result.execution_time_ms >= 0

    def test_exception_caught_returns_failure(self):
        api_config = {"executable": {"type": "python_module", "module": "m"}}
        with patch(
            "app.core.arricchisci.executable_runner._run_python_module",
            side_effect=RuntimeError("unexpected crash"),
        ):
            result = run_executable(api_config, "/in.csv", "/out")

        assert result.success is False
        assert "unexpected crash" in result.error
        assert result.execution_time_ms >= 0


# =============================================================================
# resolve_input_file: file_source and project_source  (lines 215, 217)
# =============================================================================

class TestResolveInputFileSourceTypes:

    def test_file_source_type_calls_resolver(self):
        upstream = {"src-1": {"_node_type": "file_source", "file_source_id": "fs1"}}
        edges = [{"source": "src-1", "target": "exec-1"}]
        with patch(
            "app.core.arricchisci.executable_runner._resolve_file_source_path",
            return_value="/data/file.csv",
        ) as mock_resolve:
            result = resolve_input_file({}, upstream, edges, "exec-1")

        assert result == "/data/file.csv"
        mock_resolve.assert_called_once_with({"_node_type": "file_source", "file_source_id": "fs1"})

    def test_project_source_type_calls_resolver(self):
        upstream = {"src-1": {"_node_type": "project_source", "source_project_id": "p1"}}
        edges = [{"source": "src-1", "target": "exec-1"}]
        with patch(
            "app.core.arricchisci.executable_runner._resolve_project_output_path",
            return_value="/tmp/output.zip",
        ) as mock_resolve:
            result = resolve_input_file({}, upstream, edges, "exec-1")

        assert result == "/tmp/output.zip"
        mock_resolve.assert_called_once_with({"_node_type": "project_source", "source_project_id": "p1"})

    def test_source_node_not_in_upstream_returns_none(self):
        """Edge references a source not in upstream_nodes dict."""
        edges = [{"source": "missing-src", "target": "exec-1"}]
        result = resolve_input_file({}, {}, edges, "exec-1")
        # source_node_data is {}, _node_type is "", neither branch matches
        assert result is None

    def test_multiple_edges_uses_first_matching_target(self):
        """next() picks the first edge matching the target node_id."""
        upstream = {
            "src-a": {"_node_type": "file_source", "file_source_id": "fsa"},
            "src-b": {"_node_type": "file_source", "file_source_id": "fsb"},
        }
        edges = [
            {"source": "src-a", "target": "exec-1"},
            {"source": "src-b", "target": "exec-1"},
        ]
        with patch(
            "app.core.arricchisci.executable_runner._resolve_file_source_path",
            return_value="/data/a.csv",
        ):
            result = resolve_input_file({}, upstream, edges, "exec-1")
        assert result == "/data/a.csv"


# =============================================================================
# _run_python_module  (lines 229-288)
# =============================================================================

class TestRunPythonModule:

    def test_no_module_specified_error(self):
        result = _run_python_module({}, "/in.csv", "/tmp/out", {})
        assert result.success is False
        assert "'module' non specificato" in result.error

    def test_import_error(self):
        config = {"module": "nonexistent_module_xyz_123", "function": "run"}
        with patch("importlib.import_module", side_effect=ImportError("No module")):
            result = _run_python_module(config, "/in.csv", "/tmp/out", {})
        assert result.success is False
        assert "non installato" in result.error
        assert "nonexistent_module_xyz_123" in result.error

    def test_attribute_error_function_not_found(self):
        config = {"module": "some.module", "function": "missing_func"}
        mock_module = MagicMock(spec=[])  # no attributes
        with patch("importlib.import_module", return_value=mock_module):
            result = _run_python_module(config, "/in.csv", "/tmp/out", {})
        assert result.success is False
        assert "non trovata" in result.error
        assert "missing_func" in result.error

    def test_successful_execution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_file = os.path.join(tmpdir, "input.dxf")
            Path(input_file).touch()
            output_dir = os.path.join(tmpdir, "output")

            # The function will produce output at output_path
            def fake_process(inp, output_zip=None, **kwargs):
                Path(output_zip).write_bytes(b"fake zip content")
                return output_zip

            mock_module = MagicMock()
            mock_module.process = fake_process

            config = {
                "module": "dxf_imdf_sync.process",
                "function": "process",
                "output_extension": ".zip",
            }
            with patch("importlib.import_module", return_value=mock_module):
                with patch.object(mock_module, "process", side_effect=fake_process):
                    # We need getattr to work
                    pass

            # Simpler approach: mock importlib and the function
            mock_func = MagicMock(side_effect=fake_process)
            mock_mod = MagicMock()
            mock_mod.process = mock_func

            with patch("importlib.import_module", return_value=mock_mod):
                result = _run_python_module(config, input_file, output_dir, {})

            assert result.success is True
            assert result.output_size_bytes == len(b"fake zip content")
            assert result.output_filename.endswith(".zip")
            assert result.output_filename.startswith("input_output_")
            assert result.metadata["module"] == "dxf_imdf_sync.process"
            assert result.metadata["function"] == "process"
            assert result.metadata["input"] == input_file

    def test_function_produces_no_output_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_file = os.path.join(tmpdir, "input.dxf")
            Path(input_file).touch()
            output_dir = os.path.join(tmpdir, "output")

            mock_func = MagicMock(return_value="/nonexistent/output.zip")
            mock_mod = MagicMock()
            mock_mod.process = mock_func

            config = {"module": "m", "function": "process"}
            with patch("importlib.import_module", return_value=mock_mod):
                result = _run_python_module(config, input_file, output_dir, {})

            assert result.success is False
            assert "non ha prodotto output" in result.error

    def test_site_id_and_excel_path_passed_as_kwargs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_file = os.path.join(tmpdir, "input.dxf")
            Path(input_file).touch()
            output_dir = os.path.join(tmpdir, "output")

            def fake_process(inp, output_zip=None, site_id=None, excel_path=None):
                Path(output_zip).write_bytes(b"data")
                return output_zip

            mock_func = MagicMock(side_effect=fake_process)
            mock_mod = MagicMock()
            mock_mod.run = mock_func

            config = {"module": "m", "function": "run", "output_extension": ".zip"}
            params = {"site_id": "STATION_01", "excel_path": "/data/db.xlsx"}

            with patch("importlib.import_module", return_value=mock_mod):
                result = _run_python_module(config, input_file, output_dir, params)

            assert result.success is True
            call_kwargs = mock_func.call_args[1]
            assert call_kwargs["site_id"] == "STATION_01"
            assert call_kwargs["excel_path"] == "/data/db.xlsx"

    def test_default_output_extension_is_zip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_file = os.path.join(tmpdir, "input.dxf")
            Path(input_file).touch()
            output_dir = os.path.join(tmpdir, "output")

            def fake_process(inp, output_zip=None):
                Path(output_zip).write_bytes(b"data")
                return output_zip

            mock_func = MagicMock(side_effect=fake_process)
            mock_mod = MagicMock()
            mock_mod.process = mock_func

            config = {"module": "m", "function": "process"}
            # No output_extension specified
            with patch("importlib.import_module", return_value=mock_mod):
                result = _run_python_module(config, input_file, output_dir, {})

            assert result.success is True
            assert result.output_filename.endswith(".zip")

    def test_empty_params_not_passed_as_kwargs(self):
        """Empty site_id and excel_path should not be included in func_kwargs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            input_file = os.path.join(tmpdir, "input.dxf")
            Path(input_file).touch()
            output_dir = os.path.join(tmpdir, "output")

            def fake_process(inp, output_zip=None, **kwargs):
                Path(output_zip).write_bytes(b"data")
                return output_zip

            mock_func = MagicMock(side_effect=fake_process)
            mock_mod = MagicMock()
            mock_mod.process = mock_func

            config = {"module": "m", "function": "process"}
            params = {"site_id": "", "excel_path": None}

            with patch("importlib.import_module", return_value=mock_mod):
                result = _run_python_module(config, input_file, output_dir, params)

            assert result.success is True
            # Empty/None values should NOT appear in kwargs
            call_kwargs = mock_func.call_args[1]
            assert "site_id" not in call_kwargs
            assert "excel_path" not in call_kwargs
