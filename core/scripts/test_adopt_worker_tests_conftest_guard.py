# -*- coding: utf-8 -*-
"""Security review (2026-11-18): every static scan here reads the worker's TEST file, but pytest itself would
auto-execute a conftest.py (or honor a pytest/tox/setup config) sitting anywhere under JOBDIR before any test even
runs -- bypassing every scan regardless of what it found. main() must refuse outright rather than ever reach pytest
in that case."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import adopt_worker_tests as awt  # noqa: E402


def test_refuses_a_jobdir_holding_a_conftest(tmp_path):
    (tmp_path / "conftest.py").write_text("import os; os.system('touch /tmp/pwned')\n", encoding="utf-8")
    (tmp_path / "test_x.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    rc = awt.main([str(tmp_path), "test_x.py", "test_adopted_x.py"])
    assert rc == 6


def test_refuses_a_jobdir_holding_a_pytest_ini(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_x.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    rc = awt.main([str(tmp_path), "test_x.py", "test_adopted_x.py"])
    assert rc == 6


def test_an_ordinary_jobdir_with_no_config_is_not_refused_at_this_gate(tmp_path, monkeypatch):
    """Not a full end-to-end run (that needs the real worker scaffolding) -- just confirms the conftest guard
    doesn't false-positive on a clean JOBDIR, by checking it never even reaches the early-return path."""
    (tmp_path / "test_x.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(awt, "sw", type("M", (), {"scan_source": staticmethod(lambda src: calls.append("scanned") or [(1, "boom")])})())
    rc = awt.main([str(tmp_path), "test_x.py", "test_adopted_x.py"])
    assert calls == ["scanned"]
    assert rc == 2
