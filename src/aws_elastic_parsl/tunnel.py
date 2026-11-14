"""Keep an SSH reverse tunnel open from this host to every worker of an AWSProvider.

HighThroughputExecutor workers dial the interchange on the submit host. When the
submit host is behind NAT (a laptop, or a container on one), set the executor's
``address='127.0.0.1'`` and a fixed ``worker_port``, and run this alongside it: each
worker's ``127.0.0.1:<port>`` is forwarded back to ``127.0.0.1:<port>`` here.

Workers are found by the provider's ownership tag (``parsl:infrastructure``, read
from the provider's state file), so only instances that provider created are used.
The provider must allow SSH from this host (``ssh_cidr='<public ip>/32'``).

    python -m aws_elastic_parsl.tunnel --state-file awsproviderstate.json --port 54000
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import threading
from pathlib import Path

import boto3

log = logging.getLogger(__name__)

INFRASTRUCTURE_TAG_KEY = "parsl:infrastructure"


def ssh_command(key_path: str, user: str, host: str, port: int) -> list[str]:
    return [
        "ssh", "-N", "-i", key_path,
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        # A ControlMaster in ~/.ssh/config would take over the forward and make this
        # process exit, so keep each tunnel self-contained.
        "-o", "ControlMaster=no",
        "-o", "ControlPath=none",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=15",
        "-o", "ConnectTimeout=10",
        "-R", f"{port}:127.0.0.1:{port}",
        f"{user}@{host}",
    ]


class ReverseTunnels(threading.Thread):
    """Reconcile one ``ssh -R`` per running instance, every ``interval`` seconds.

    Instances are selected either by ``infrastructure_id`` (preferred) or, for
    in-process use, by a callable returning instance IDs.
    """

    def __init__(self, ec2_client, key_path: str, port: int, *, user: str = "ubuntu",
                 infrastructure_id: str | None = None, instance_ids=None, interval: float = 5):
        super().__init__(daemon=True, name="reverse-tunnels")
        if (infrastructure_id is None) == (instance_ids is None):
            raise ValueError("give exactly one of infrastructure_id or instance_ids")
        self.ec2 = ec2_client
        self.key_path = key_path
        self.port = port
        self.user = user
        self.infrastructure_id = infrastructure_id
        self.instance_ids = instance_ids
        self.interval = interval
        self.procs: dict[str, subprocess.Popen] = {}
        self._halt = threading.Event()

    def _running(self) -> dict[str, str]:
        """instance id -> public IP, for running instances with a public IP."""
        if self.infrastructure_id is not None:
            filters = [{"Name": f"tag:{INFRASTRUCTURE_TAG_KEY}", "Values": [self.infrastructure_id]}]
        else:
            ids = list(self.instance_ids())
            if not ids:
                return {}
            filters = [{"Name": "instance-id", "Values": ids}]
        filters.append({"Name": "instance-state-name", "Values": ["running"]})
        found = {}
        for page in self.ec2.get_paginator("describe_instances").paginate(Filters=filters):
            for r in page["Reservations"]:
                for inst in r["Instances"]:
                    if inst.get("PublicIpAddress"):
                        found[inst["InstanceId"]] = inst["PublicIpAddress"]
        return found

    def reconcile(self) -> None:
        running = self._running()
        for iid, proc in list(self.procs.items()):
            if iid not in running:
                if proc.poll() is None:
                    proc.terminate()
                del self.procs[iid]
        for iid, ip in running.items():
            proc = self.procs.get(iid)
            if proc is None or proc.poll() is not None:
                log.info("opening reverse tunnel to %s (%s) on port %d", iid, ip, self.port)
                self.procs[iid] = subprocess.Popen(
                    ssh_command(self.key_path, self.user, ip, self.port), stdin=subprocess.DEVNULL)

    def run(self) -> None:
        while not self._halt.is_set():
            try:
                self.reconcile()
            except Exception:
                log.exception("tunnel reconcile failed")
            self._halt.wait(self.interval)

    def stop(self) -> None:
        self._halt.set()
        for proc in self.procs.values():
            if proc.poll() is None:
                proc.terminate()


def infrastructure_id_from_state(state_file: Path) -> str | None:
    try:
        return json.loads(state_file.read_text()).get("infrastructureID")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state-file", required=True, type=Path,
                    help="the AWSProvider state_file; may not exist yet")
    ap.add_argument("--port", type=int, default=54000, help="the executor's worker_port")
    ap.add_argument("--key-path", default=os.path.expanduser("~/.ssh/aws-elastic-parsl.pem"))
    ap.add_argument("--user", default="ubuntu")
    ap.add_argument("--region", default=None)
    ap.add_argument("--interval", type=float, default=5)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    ec2 = boto3.Session(region_name=args.region).client("ec2")
    tunnels: ReverseTunnels | None = None
    current = None
    halt = threading.Event()
    try:
        while not halt.is_set():
            # The provider writes (and teardown removes) the state file; follow it.
            infra = infrastructure_id_from_state(args.state_file)
            if infra != current:
                if tunnels:
                    tunnels.stop()
                    tunnels = None
                current = infra
                if infra:
                    log.info("following infrastructure %s from %s", infra, args.state_file)
                    tunnels = ReverseTunnels(ec2, args.key_path, args.port, user=args.user,
                                             infrastructure_id=infra, interval=args.interval)
                    tunnels.start()
                else:
                    log.info("waiting for state file %s", args.state_file)
            halt.wait(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        if tunnels:
            tunnels.stop()


if __name__ == "__main__":
    main()
