"""Submit a few functions to a local Globus Compute endpoint and print the results.

    python -m aws_elastic_parsl.gc_smoke <endpoint name> [N]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def whereami():
    import platform
    import sys

    import parsl
    return f"{platform.node()} python={platform.python_version()} parsl={parsl.__version__} exe={sys.executable}"


def square(x):
    return x * x


def main() -> None:
    from globus_compute_sdk import Executor

    name = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    endpoint_id = json.loads((Path.home() / ".globus_compute" / name / "endpoint.json").read_text())["endpoint_id"]
    print(f"endpoint {name} = {endpoint_id}", flush=True)

    t0 = time.monotonic()
    with Executor(endpoint_id=endpoint_id) as gce:
        print("worker:", gce.submit(whereami).result(timeout=1800), flush=True)
        print(f"first result after {time.monotonic() - t0:.0f}s", flush=True)
        print("squares:", [f.result(timeout=600) for f in [gce.submit(square, i) for i in range(n)]], flush=True)


if __name__ == "__main__":
    main()
