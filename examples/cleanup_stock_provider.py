"""Delete the VPCs (and everything in them) left behind by Parsl's stock AWSProvider.

The stock provider names its VPC ``parsl.aws.<timestamp>`` and never tears it
down (Parsl does not call ``provider.goodbye()``; ``teardown()`` itself fails).
This finds those VPCs by Name tag and removes instances, internet gateways,
subnets, route tables, security groups and finally the VPC.

Usage:
    AWS_PROFILE=aws python examples/cleanup_stock_provider.py [--region R] [--yes]
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import boto3


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default=None)
    ap.add_argument("--yes", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument("--state-file", default="runinfo/awsproviderstate.json")
    args = ap.parse_args()

    session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE"), region_name=args.region)
    ec2 = session.resource("ec2")
    client = session.client("ec2")

    vpcs = list(ec2.vpcs.filter(Filters=[{"Name": "tag:Name", "Values": ["parsl.aws.*"]}]))
    if not vpcs:
        print(f"no stock-provider VPCs in {session.region_name}")
    for vpc in vpcs:
        name = next((t["Value"] for t in vpc.tags or [] if t["Key"] == "Name"), "")
        instances = [i.id for i in vpc.instances.all() if i.state["Name"] != "terminated"]
        igws = list(vpc.internet_gateways.all())
        subnets = list(vpc.subnets.all())
        rts = [rt for rt in vpc.route_tables.all() if not any(a.get("Main") for a in rt.associations_attribute)]
        sgs = [sg for sg in vpc.security_groups.all() if sg.group_name != "default"]
        print(f"{vpc.id} {name}: {len(instances)} instances, {len(igws)} igw, "
              f"{len(subnets)} subnets, {len(rts)} route tables, {len(sgs)} security groups")
        if not args.yes:
            continue
        if instances:
            client.terminate_instances(InstanceIds=instances)
            client.get_waiter("instance_terminated").wait(InstanceIds=instances)
        for igw in igws:
            igw.detach_from_vpc(VpcId=vpc.id)
            igw.delete()
        for sn in subnets:
            sn.delete()
        for rt in rts:
            rt.delete()
        for sg in sgs:
            sg.delete()
        vpc.delete()
        print(f"  deleted {vpc.id}")

    if args.yes and vpcs and Path(args.state_file).exists():
        Path(args.state_file).unlink()
        print(f"removed {args.state_file}")
    elif not args.yes and vpcs:
        print("dry run; pass --yes to delete")


if __name__ == "__main__":
    main()
