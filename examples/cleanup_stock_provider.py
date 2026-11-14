"""Find and delete EC2 infrastructure left behind by Parsl's AWSProvider.

Only resources the provider created are touched:

* **Tagged** (fixed provider): the VPC and everything in it carry
  ``parsl:provider=AWSProvider`` and ``parsl:infrastructure=<id>``. A resource is
  deleted only if its ``parsl:infrastructure`` tag matches its VPC's.
* **Legacy** (released Parsl <= 2026.11.09): resources carry only the provider's
  Name tags: VPC ``parsl.aws.<ts>``, subnets ``<vpc name>.<az>``, the security group
  ``<vpc name>``, and instances ``parsl.aws.<ts>``. The internet gateway and route table
  carry no tags at all, so their origin cannot be proven. They are deleted only with
  ``--include-untagged``.

A VPC that contains anything not attributable to the provider (a foreign instance,
an unrecognised subnet or security group, ...) is reported and left alone entirely.

Dry run by default:
    AWS_PROFILE=aws python examples/cleanup_stock_provider.py [--region R]
    AWS_PROFILE=aws python examples/cleanup_stock_provider.py --yes [--include-untagged]
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path

import boto3

PROVIDER_TAG = "parsl:provider"
INFRA_TAG = "parsl:infrastructure"


def tags(resource) -> dict[str, str]:
    return {t["Key"]: t["Value"] for t in (resource.tags or [])}


@dataclass
class Plan:
    vpc: object
    name: str
    infra_id: str | None          # None => legacy (Name-tag ownership only)
    instances: list = field(default_factory=list)
    sgs: list = field(default_factory=list)
    subnets: list = field(default_factory=list)
    igws: list = field(default_factory=list)
    rts: list = field(default_factory=list)
    untagged: list = field(default_factory=list)   # provably ours only by context
    foreign: list = field(default_factory=list)    # not ours: blocks the VPC


def owned(resource, plan: Plan, legacy_name_ok) -> bool:
    t = tags(resource)
    if plan.infra_id is not None:
        return t.get(INFRA_TAG) == plan.infra_id
    return legacy_name_ok(t.get("Name", ""))


def build_plan(vpc) -> Plan:
    t = tags(vpc)
    name = t.get("Name", "")
    infra_id = t.get(INFRA_TAG) if t.get(PROVIDER_TAG) == "AWSProvider" else None
    plan = Plan(vpc=vpc, name=name, infra_id=infra_id)

    for inst in vpc.instances.all():
        if inst.state["Name"] == "terminated":
            continue
        ok = owned(inst, plan, lambda n: n.startswith("parsl.aws."))
        (plan.instances if ok else plan.foreign).append(inst)

    for sg in vpc.security_groups.all():
        if sg.group_name == "default":
            continue  # removed with the VPC
        ok = owned(sg, plan, lambda n: n == name)
        (plan.sgs if ok else plan.foreign).append(sg)

    for sn in vpc.subnets.all():
        ok = owned(sn, plan, lambda n: n.startswith(name + "."))
        (plan.subnets if ok else plan.foreign).append(sn)

    for igw in vpc.internet_gateways.all():
        if plan.infra_id is not None:
            (plan.igws if tags(igw).get(INFRA_TAG) == plan.infra_id else plan.foreign).append(igw)
        elif not igw.tags and len(igw.attachments) == 1:
            plan.untagged.append(igw)
        else:
            plan.foreign.append(igw)

    for rt in vpc.route_tables.all():
        if any(a.get("Main") for a in rt.associations_attribute):
            continue  # removed with the VPC
        if plan.infra_id is not None:
            (plan.rts if tags(rt).get(INFRA_TAG) == plan.infra_id else plan.foreign).append(rt)
        elif not rt.tags:
            plan.untagged.append(rt)
        else:
            plan.foreign.append(rt)
    return plan


def describe(r) -> str:
    return getattr(r, "id", str(r))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default=None)
    ap.add_argument("--yes", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument("--include-untagged", action="store_true",
                    help="also delete the untagged internet gateway / route table of a legacy VPC")
    ap.add_argument("--state-file", default="runinfo/awsproviderstate.json",
                    help="removed if it names a VPC that was deleted")
    args = ap.parse_args()

    session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE"), region_name=args.region)
    ec2 = session.resource("ec2")
    client = session.client("ec2")

    candidates = {v.id: v for v in ec2.vpcs.filter(Filters=[{"Name": "tag:Name", "Values": ["parsl.aws.*"]}])}
    candidates.update({v.id: v for v in ec2.vpcs.filter(
        Filters=[{"Name": f"tag:{PROVIDER_TAG}", "Values": ["AWSProvider"]}])})
    if not candidates:
        print(f"no AWSProvider VPCs in {session.region_name}")
        return

    deleted = set()
    for vpc in candidates.values():
        plan = build_plan(vpc)
        kind = f"tagged {plan.infra_id}" if plan.infra_id else "legacy (Name tags only)"
        print(f"{vpc.id} {plan.name}: {kind}")
        for label, items in (("instances", plan.instances), ("security groups", plan.sgs),
                             ("subnets", plan.subnets), ("internet gateways", plan.igws),
                             ("route tables", plan.rts), ("untagged igw/rtb", plan.untagged),
                             ("NOT OURS", plan.foreign)):
            if items:
                print(f"  {label}: {', '.join(describe(i) for i in items)}")
        if plan.foreign:
            print("  -> skipped: contains resources the provider did not create")
            continue
        if plan.untagged and not args.include_untagged:
            print("  -> skipped: untagged internet gateway/route table; pass --include-untagged "
                  "to delete them (they cannot be proven to be the provider's)")
            continue
        if not args.yes:
            print("  -> would delete (dry run; pass --yes)")
            continue

        if plan.instances:
            ids = [i.id for i in plan.instances]
            client.terminate_instances(InstanceIds=ids)
            client.get_waiter("instance_terminated").wait(InstanceIds=ids)
        for sg in plan.sgs:
            sg.delete()
        for igw in plan.igws + [r for r in plan.untagged if hasattr(r, "attachments")]:
            igw.detach_from_vpc(VpcId=vpc.id)
            igw.delete()
        for sn in plan.subnets:
            sn.delete()
        for rt in plan.rts + [r for r in plan.untagged if hasattr(r, "associations_attribute")]:
            rt.delete()
        vpc.delete()
        deleted.add(vpc.id)
        print(f"  deleted {vpc.id}")

    state = Path(args.state_file)
    if deleted and state.exists() and any(v in state.read_text() for v in deleted):
        state.unlink()
        print(f"removed {state}")


if __name__ == "__main__":
    main()
