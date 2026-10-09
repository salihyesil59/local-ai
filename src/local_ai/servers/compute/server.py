"""Compute MCP server: a persistent Python kernel with units and symbolic math.

Unlike a fresh Python process per call, variables, imports and functions survive between
calls (like a Jupyter notebook), so the model can build a calculation step by step.
Preloaded: numpy as np, scipy, sympy as sp, pint (`ureg`, `Q_`), matplotlib.pyplot as plt.

Tools:
  python(code)                       run code in the persistent kernel
  reset()                            restart the kernel (clears all state)
  check_units(expression, variables, expected_unit)   dimensional analysis with pint
  solve(equations, unknowns)         symbolic solving with sympy

Not a security sandbox: code runs as your user, in <workspace>/compute.

Run:  local-ai-compute
"""

from __future__ import annotations

import atexit
import base64
import math
import queue
import re
import sys
import threading
import time
from pathlib import Path

from local_ai.servers.common import FastMCP, slugify, workspace_root, write_notebook

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
MAX_OUTPUT = 12000

STARTUP = """
import math
import numpy as np
import scipy
import sympy as sp
import pint
ureg = pint.UnitRegistry()
Q_ = ureg.Quantity
import matplotlib
matplotlib.use("module://matplotlib_inline.backend_inline")
import matplotlib.pyplot as plt
"""


class Kernel:
    """A Jupyter kernel driven through jupyter_client."""

    def __init__(self, cwd: Path, figures: Path):
        self.cwd = cwd
        self.figures = figures
        self.km = None
        self.kc = None
        self.lock = threading.Lock()
        self.cells: list[dict] = []  # executed cells since start/reset, for export_notebook

    def start(self) -> None:
        from jupyter_client import KernelManager

        self.cwd.mkdir(parents=True, exist_ok=True)
        self.figures.mkdir(parents=True, exist_ok=True)
        self.km = KernelManager(kernel_name="python3")
        # Use this interpreter so the kernel sees the same packages.
        self.km.kernel_cmd = [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"]
        self.km.start_kernel(cwd=str(self.cwd))
        self.kc = self.km.client()
        self.kc.start_channels()
        self.kc.wait_for_ready(timeout=60)
        out = self._execute(STARTUP, timeout=120)
        if out["error"]:
            raise RuntimeError(f"Kernel startup failed: {out['error']}")

    def ensure(self) -> None:
        if self.km is None or not self.km.is_alive():
            self.start()

    def shutdown(self) -> None:
        if self.kc is not None:
            self.kc.stop_channels()
        if self.km is not None and self.km.is_alive():
            self.km.shutdown_kernel(now=True)
        self.km = self.kc = None

    def restart(self) -> None:
        self.cells = []
        self.shutdown()
        self.start()

    def run(self, code: str, timeout: float = 60) -> dict:
        with self.lock:
            self.ensure()
            result = self._execute(code, timeout)
            self.cells.append(
                {
                    "code": code,
                    "stdout": "".join(result["stdout"]),
                    "results": result["results"],
                    "error": result["error"] or ("Timed out" if result["timed_out"] else None),
                    "figures_png": result["figures_png"],
                }
            )
            return result

    def _execute(self, code: str, timeout: float) -> dict:
        msg_id = self.kc.execute(code, store_history=True, allow_stdin=False)
        result = {"stdout": [], "results": [], "figures": [], "figures_png": [], "error": None, "timed_out": False}
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result["timed_out"] = True
                self.km.interrupt_kernel()
                self._drain(msg_id, 10)
                break
            try:
                msg = self.kc.get_iopub_msg(timeout=min(remaining, 1.0))
            except queue.Empty:
                continue
            if msg["parent_header"].get("msg_id") != msg_id:
                continue
            kind, content = msg["msg_type"], msg["content"]
            if kind == "stream":
                result["stdout"].append(content["text"])
            elif kind in ("execute_result", "display_data"):
                data = content.get("data", {})
                if "image/png" in data:
                    path = self.figures / f"figure-{int(time.time() * 1000)}.png"
                    path.write_bytes(base64.b64decode(data["image/png"]))
                    result["figures"].append(str(path))
                    result["figures_png"].append(data["image/png"])
                elif "text/plain" in data:
                    result["results"].append(data["text/plain"])
            elif kind == "error":
                result["error"] = ANSI_RE.sub("", "\n".join(content.get("traceback", []))) or (
                    f"{content.get('ename')}: {content.get('evalue')}"
                )
            elif kind == "status" and content["execution_state"] == "idle":
                break
        return result

    def _drain(self, msg_id: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                msg = self.kc.get_iopub_msg(timeout=0.5)
            except queue.Empty:
                continue
            if (
                msg["parent_header"].get("msg_id") == msg_id
                and msg["msg_type"] == "status"
                and msg["content"]["execution_state"] == "idle"
            ):
                return


def format_result(result: dict, timeout: float) -> str:
    parts = []
    if result["stdout"]:
        parts.append("".join(result["stdout"]).rstrip())
    if result["results"]:
        parts.append("\n".join(result["results"]))
    for fig in result["figures"]:
        parts.append(f"[figure saved: {fig}]")
    if result["error"]:
        parts.append(f"Error:\n{result['error']}")
    if result["timed_out"]:
        parts.append(f"Error: timed out after {timeout:g} s; execution interrupted (kernel state kept).")
    text = "\n".join(p for p in parts if p) or "(no output — use print() to show values)"
    if len(text) > MAX_OUTPUT:
        text = text[: MAX_OUTPUT // 2] + "\n[... output truncated ...]\n" + text[-MAX_OUTPUT // 2 :]
    return text


# -- units ---------------------------------------------------------------------------

_ureg = None


def _registry():
    global _ureg
    if _ureg is None:
        import pint

        _ureg = pint.UnitRegistry()
    return _ureg


def check_units(expression: str, variables: dict[str, str], expected_unit: str = "") -> str:
    """Evaluate `expression` with each variable as 1 * its unit and report the dimensions."""
    import numpy as np
    import pint

    ureg = _registry()
    namespace = {name: 1.0 * ureg(unit) for name, unit in variables.items()}
    for fn in ("sqrt", "exp", "log", "sin", "cos", "tan", "arcsin", "arccos", "arctan"):
        namespace[fn] = getattr(np, fn)
    namespace["pi"] = math.pi
    try:
        value = eval(expression, {"__builtins__": {}}, namespace)  # noqa: S307 - local tool
    except pint.DimensionalityError as e:
        return f"INCONSISTENT: {e} (e.g. adding or exponentiating quantities with different dimensions)"
    except Exception as e:
        return f"Error evaluating expression: {type(e).__name__}: {e}"
    if not isinstance(value, pint.Quantity):
        value = value * ureg.dimensionless
    dims = str(value.dimensionality) or "dimensionless"
    if not expected_unit:
        return f"Result dimensions: {dims} (base units: {value.to_base_units().units:~})"
    try:
        expected = ureg(expected_unit)
    except Exception as e:
        return f"Error parsing expected_unit {expected_unit!r}: {e}"
    if value.dimensionality == expected.dimensionality:
        factor = value.to(expected_unit).magnitude
        note = (
            ""
            if math.isclose(factor, 1.0, rel_tol=1e-9)
            else f" (with unit inputs the result is {factor:g} {expected_unit})"
        )
        return f"OK: dimensions match {expected_unit} [{dims}]{note}"
    return (
        f"MISMATCH: expression has dimensions {dims}, but {expected_unit} has {expected.dimensionality}. "
        "Check the formula."
    )


# -- symbolic ------------------------------------------------------------------------


def solve(equations: list[str], unknowns: list[str]) -> str:
    import sympy as sp
    from sympy.parsing.sympy_parser import (
        implicit_multiplication_application,
        parse_expr,
        standard_transformations,
    )

    transformations = standard_transformations + (implicit_multiplication_application,)

    def parse(text: str):
        return parse_expr(text, transformations=transformations)

    eqs = []
    for eq in equations:
        eq = eq.replace("==", "=")
        if "=" in eq:
            lhs, rhs = eq.split("=", 1)
            eqs.append(sp.Eq(parse(lhs), parse(rhs)))
        else:  # expression assumed equal to zero
            eqs.append(parse(eq))
    symbols = [sp.Symbol(u) for u in unknowns]
    solutions = sp.solve(eqs, symbols, dict=True)
    if not solutions:
        return "No solution found."
    lines = []
    for i, sol in enumerate(solutions, 1):
        for sym, expr in sol.items():
            expr = sp.simplify(expr)
            lines.append(f"solution {i}: {sym} = {expr}    LaTeX: {sym} = {sp.latex(expr)}")
    return "\n".join(lines)


def build_server(kernel: Kernel | None = None) -> FastMCP:
    root = workspace_root()
    kern = kernel or Kernel(root / "compute", root / "figures")
    mcp = FastMCP(
        "compute",
        instructions=(
            "Persistent Python kernel for all calculations: state persists between calls. "
            "numpy (np), scipy, sympy (sp), pint (ureg, Q_) and matplotlib (plt) are preloaded. "
            "Always print() the values you will report. Use check_units on every derived formula."
        ),
    )

    @mcp.tool()
    def python(code: str, timeout: float = 60) -> str:
        """Run Python code in a persistent kernel; variables and imports survive between calls.

        Preloaded: np, scipy, sp (sympy), ureg/Q_ (pint units), plt (figures are saved and their path returned).
        print() every value you will quote. On an error, fix the code and run again.
        """
        timeout = max(1.0, min(float(timeout), 600.0))
        return format_result(kern.run(code, timeout), timeout)

    @mcp.tool()
    def reset() -> str:
        """Restart the kernel, clearing all variables."""
        with kern.lock:
            kern.restart()
        return "Kernel restarted."

    @mcp.tool()
    def export_notebook(name: str, folder: str = "") -> str:
        """Save every cell run since the kernel started (or the last reset) as a Jupyter notebook (.ipynb),
        so the calculations can be re-run and checked. folder: optional subfolder of the workspace
        (e.g. a project name); default <workspace>/notebooks."""
        target_dir = (root / slugify(folder)) if folder else root / "notebooks"
        if not kern.cells:
            return "Nothing to export: no code has been run since the kernel started."
        path = write_notebook(target_dir / f"{slugify(name)}.ipynb", list(kern.cells), title=name)
        return f"Notebook saved: {path} ({len(kern.cells)} cells)"

    @mcp.tool()
    def check_units(expression: str, variables: dict[str, str], expected_unit: str = "") -> str:
        """Dimensional analysis of a formula.

        expression: Python syntax, e.g. "G*M/r**2" or "0.5*m*v**2".
        variables: unit of each symbol, e.g. {"G": "m^3/(kg*s^2)", "M": "kg", "r": "m"}.
        expected_unit: unit the result should have, e.g. "m/s^2". Reports OK / MISMATCH.
        """
        return check_units_impl(expression, variables, expected_unit)

    @mcp.tool()
    def solve(equations: list[str], unknowns: list[str]) -> str:
        """Solve equations symbolically with sympy.

        equations: e.g. ["F = m*a", "a = v/t"]; unknowns: e.g. ["a", "F"].
        Returns each solution as plain text and LaTeX.
        """
        try:
            return solve_impl(equations, unknowns)
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"

    return mcp


check_units_impl = check_units
solve_impl = solve


def main() -> None:
    kernel = Kernel(workspace_root() / "compute", workspace_root() / "figures")
    atexit.register(kernel.shutdown)
    build_server(kernel).run()


if __name__ == "__main__":
    main()
