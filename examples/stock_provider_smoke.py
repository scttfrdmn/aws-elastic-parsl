"""Baseline smoke test: Parsl's stock ``AWSProvider`` driven from a laptop.

Runs one block on EC2 using credentials from ``AWS_PROFILE`` and executes a
few apps on it. The stock provider assumes the workers can dial back to the
interchange on the submit host, which a laptop behind NAT cannot accept, so
this script bridges that gap *outside* the provider: the interchange binds to
127.0.0.1:<port> and an SSH reverse tunnel to each instance exposes it on the
instance's own loopback. No provider code is patched.

Usage:
    AWS_PROFILE=aws python examples/stock_provider_smoke.py [--region us-west-2]

Afterwards run ``examples/cleanup_stock_provider.py`` -- the stock provider
leaves its VPC behind by design.
"""

from __future__ import annotations

import argparse
import inspect
import logging
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import boto3
import parsl
from parsl.app.app import python_app
from parsl.config import Config
from parsl.executors import HighThroughputExecutor
from parsl.providers import AWSProvider

log = logging.getLogger("smoke")

UBUNTU_AMI_PARAM = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"


class ReverseTunnels(threading.Thread):
    """Keep an ``ssh -R`` tunnel open to every running instance of a provider."""

    def __init__(self, provider: AWSProvider, key_path: str, port: int, user: str = "ubuntu"):
        super().__init__(daemon=True, name="reverse-tunnels")
        self.provider = provider
        self.key_path = key_path
        self.port = port
        self.user = user
        self.procs: dict[str, subprocess.Popen] = {}
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.is_set():
            try:
                self._reconcile()
            except Exception:
                log.exception("tunnel reconcile failed")
            self._halt.wait(5)

    def _reconcile(self) -> None:
        ids = list(self.provider.resources)
        if not ids:
            return
        resp = self.provider.client.describe_instances(InstanceIds=ids)
        for r in resp["Reservations"]:
            for inst in r["Instances"]:
                iid, state, ip = inst["InstanceId"], inst["State"]["Name"], inst.get("PublicIpAddress")
                proc = self.procs.get(iid)
                if state != "running" or not ip:
                    if proc and proc.poll() is None:
                        proc.terminate()
                    continue
                if proc is None or proc.poll() is not None:
                    log.info("opening reverse tunnel to %s (%s)", iid, ip)
                    self.procs[iid] = subprocess.Popen(
                        [
                            "ssh", "-N", "-i", self.key_path,
                            "-o", "StrictHostKeyChecking=no",
                            "-o", "UserKnownHostsFile=/dev/null",
                            "-o", "LogLevel=ERROR",
                            # A ControlMaster in ~/.ssh/config would own the forward and
                            # make this process exit, so keep the tunnel self-contained.
                            "-o", "ControlMaster=no",
                            "-o", "ControlPath=none",
                            "-o", "ExitOnForwardFailure=yes",
                            "-o", "ServerAliveInterval=15",
                            "-o", "ConnectTimeout=10",
                            "-R", f"{self.port}:127.0.0.1:{self.port}",
                            f"{self.user}@{ip}",
                        ],
                        stdin=subprocess.DEVNULL,
                    )

    def stop(self) -> None:
        self._halt.set()
        for proc in self.procs.values():
            if proc.poll() is None:
                proc.terminate()


@python_app
def whereami():
    import platform
    import sys

    import parsl
    return f"{platform.node()} python={platform.python_version()} parsl={parsl.__version__} exe={sys.executable}"


@python_app
def square(x):
    return x * x


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default=None, help="defaults to the profile's region")
    ap.add_argument("--instance-type", default="t3.small")
    ap.add_argument("--key-name", default="aws-elastic-parsl")
    ap.add_argument("--key-path", default=os.path.expanduser("~/.ssh/aws-elastic-parsl.pem"))
    ap.add_argument("--port", type=int, default=54000)
    ap.add_argument("--run-dir", default="runinfo")
    ap.add_argument("--worker-parsl", default=None,
                    help="pip requirement for Parsl on the workers; must report the same version as "
                         "the submit side (default: parsl==<local version>)")
    ap.add_argument("--teardown", action="store_true",
                    help="also call the stock provider's teardown() (known to be broken)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("botocore", "boto3", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    profile = os.environ.get("AWS_PROFILE")
    if not profile:
        sys.exit("set AWS_PROFILE")
    session = boto3.Session(profile_name=profile, region_name=args.region)
    region = session.region_name
    ami = session.client("ssm").get_parameter(Name=UBUNTU_AMI_PARAM)["Parameter"]["Value"]
    log.info("profile=%s region=%s ami=%s", profile, region, ami)

    # The workers' Python minor version and Parsl version must match ours or the
    # interchange rejects them (VersionMismatch). Ubuntu 24.04 ships Python 3.12.
    local_py = ".".join(platform.python_version_tuple()[:2])
    if local_py != "3.12":
        log.warning("submit-side Python is %s; Ubuntu 24.04 workers run 3.12 -> VersionMismatch", local_py)

    requirement = args.worker_parsl or f"parsl=={parsl.__version__}"
    install_kwargs: dict = {}
    fixed = "parsl_requirement" in inspect.signature(AWSProvider).parameters
    if fixed:
        # Fixed provider (scttfrdmn/aws-elastic-parsl#6): it builds a pinned venv itself.
        if args.worker_parsl:
            install_kwargs["parsl_requirement"] = args.worker_parsl
    else:
        # Stock provider: its `pip3 install parsl` is refused on Ubuntu 24.04 (PEP 668),
        # and --break-system-packages fails on Debian-owned packages, so build a
        # version-matched venv here. worker_init is inlined before the worker command,
        # so the PATH export carries over to it.
        install_kwargs["worker_init"] = (
            "apt-get install -y python3-venv && python3 -m venv /opt/parsl-venv && "
            f"/opt/parsl-venv/bin/pip install '{requirement}' && export PATH=/opt/parsl-venv/bin:$PATH"
        )

    params = inspect.signature(AWSProvider).parameters
    if "ssh_cidr" in params:
        # Fixed provider (scttfrdmn/aws-elastic-parsl#12) opens no SSH by default;
        # the reverse tunnels need it from this host only.
        my_ip = urllib.request.urlopen("https://checkip.amazonaws.com", timeout=10).read().decode().strip()
        install_kwargs["ssh_cidr"] = f"{my_ip}/32"
    if "teardown_on_shutdown" in params and args.teardown:
        # Fixed provider (scttfrdmn/aws-elastic-parsl#5): tear down when the executor shuts down.
        install_kwargs["teardown_on_shutdown"] = True

    Path(args.run_dir).mkdir(exist_ok=True)
    provider = AWSProvider(
        image_id=ami,
        key_name=args.key_name,
        region=region,
        instance_type=args.instance_type,
        # The stock credential check ignores AWS_PROFILE (scttfrdmn/aws-elastic-parsl#2);
        # the fixed provider resolves it through boto3's default chain.
        profile=None if fixed else profile,
        state_file=str(Path(args.run_dir) / "awsproviderstate.json"),
        init_blocks=1,
        min_blocks=0,
        max_blocks=1,
        **install_kwargs,
    )

    htex = HighThroughputExecutor(
        label="aws_htex",
        address="127.0.0.1",       # what the workers dial: their own loopback, tunnelled home
        loopback_address="127.0.0.1",
        worker_port=args.port,
        encrypted=False,           # CurveZMQ certs live in the local run dir; transport is SSH
        max_workers_per_node=2,
        provider=provider,
    )

    tunnels = ReverseTunnels(provider, args.key_path, args.port)
    tunnels.start()

    t0 = time.monotonic()
    rc = 1
    with parsl.load(Config(executors=[htex], run_dir=args.run_dir)):
        try:
            print("worker:", whereami().result(timeout=1200))
            print(f"first result after {time.monotonic() - t0:.0f}s")
            print("squares:", [f.result() for f in [square(i) for i in range(10)]])
            rc = 0
        finally:
            tunnels.stop()

    if args.teardown and not install_kwargs.get("teardown_on_shutdown"):
        try:
            provider.teardown()
        except Exception as e:
            log.error("stock teardown() failed: %r", e)
    return rc


if __name__ == "__main__":
    sys.exit(main())
