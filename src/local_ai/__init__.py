"""Local research assistant: MCP tool servers and a research agent for LM Studio."""

import os

__version__ = "0.1.0"

# OpenBLAS (under numpy) reserves memory for every CPU thread when it loads: about 500 MB of committed memory per
# process on a 16-thread CPU, in the UI and in each MCP server, which do no large matrix work. numpy loads here with
# one BLAS thread; the variable is removed again, so the programs they start (the compute kernel, a project's
# tests, the user's scripts) get the machine's default. A value set by the user is kept as it is.
if "OPENBLAS_NUM_THREADS" not in os.environ:
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    try:
        import numpy  # noqa: F401
    finally:
        del os.environ["OPENBLAS_NUM_THREADS"]
