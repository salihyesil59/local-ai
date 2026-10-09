import pytest

from local_ai.servers.compute.server import Kernel, check_units, format_result, solve


@pytest.fixture(scope="module")
def kernel(tmp_path_factory):
    root = tmp_path_factory.mktemp("compute")
    k = Kernel(root / "cwd", root / "figures")
    k.start()
    yield k
    k.shutdown()


def run(kernel, code, timeout=30):
    return format_result(kernel.run(code, timeout), timeout)


def test_state_persists(kernel):
    run(kernel, "c = 299792458")
    assert run(kernel, "print(c * 3600 / 1000)").strip() == "1079252848.8"


def test_preloaded_units_and_numpy(kernel):
    out = run(kernel, "print(round(Q_(100, 'km/h').to('m/s').magnitude, 3)); np.sqrt(16)")
    assert "27.778" in out and "4.0" in out


def test_errors_are_reported(kernel):
    assert "ZeroDivisionError" in run(kernel, "1/0")


def test_timeout_interrupts_but_keeps_state(kernel):
    run(kernel, "kept = 42")
    out = run(kernel, "import time\nwhile True: time.sleep(0.1)", timeout=2)
    assert "timed out" in out
    assert run(kernel, "print(kept)").strip() == "42"


def test_figure_saved(kernel):
    out = run(kernel, "plt.plot([1, 2, 3]); plt.show()")
    assert "[figure saved:" in out and ".png" in out


def test_restart_clears_state(kernel):
    run(kernel, "gone = 1")
    kernel.restart()
    assert "NameError" in run(kernel, "gone")


def test_check_units():
    units = {"G": "m^3/(kg*s^2)", "M": "kg", "r": "m"}
    assert check_units("G*M/r**2", units, "m/s^2").startswith("OK")
    assert check_units("G*M/r", units, "m/s^2").startswith("MISMATCH")
    assert check_units("M + r", units).startswith("INCONSISTENT")
    assert "[mass]" in check_units("0.5*m*v**2", {"m": "kg", "v": "m/s"})


def test_solve():
    out = solve(["F = m*a", "a = v/t"], ["F", "a"])
    assert "F = m*v/t" in out and "LaTeX" in out
    assert "x = -2" in solve(["x**2 - 4"], ["x"])


def test_export_notebook(kernel, tmp_path):
    import json

    from local_ai.servers.common import write_notebook

    kernel.restart()
    run(kernel, "a = 2\nprint(a + 1)")
    run(kernel, "plt.plot([1, 2]); plt.show()")
    run(kernel, "undefined_name")
    path = write_notebook(tmp_path / "n.ipynb", kernel.cells, title="Test")
    nb = json.loads(path.read_text(encoding="utf-8"))
    assert nb["nbformat"] == 4 and len(nb["cells"]) == 4
    code = nb["cells"][1]
    assert "".join(code["source"]).startswith("a = 2") and code["outputs"][0]["text"] == ["3\n"]
    assert "image/png" in nb["cells"][2]["outputs"][0]["data"]
    assert nb["cells"][3]["outputs"][0]["output_type"] == "error"
    try:
        import nbformat
    except ImportError:
        return
    nbformat.validate(nbformat.reads(path.read_text(encoding="utf-8"), as_version=4))
