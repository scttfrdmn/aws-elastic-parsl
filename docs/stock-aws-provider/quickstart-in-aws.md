# Quickstart: from a submit host in AWS

Here the Parsl script runs on an EC2 instance, the submit host, and the workers connect to its
private IP. No tunnel is needed.

The provider always builds its own VPC (`10.0.0.0/16`), and the workers can only use private addresses
inside that VPC. The simplest way to give them a private path to the submit host is therefore to
launch the submit host **inside the provider's VPC**. A submit host in another VPC, such as the default
VPC, is reachable only over public IPs or through VPC peering. See
[networking.md](networking.md#submit-host-in-another-vpc) for why that is harder and less safe.

The steps:

1. On your workstation, create the provider's infrastructure.
2. Create an IAM role for the submit host.
3. Launch the submit host into one of the provider's subnets, with the provider's security group.
4. On the submit host, run the Parsl script with `address=` set to its private IP.
5. When you are done, terminate the submit host and tear down from your workstation.

The commands use `us-west-2`, an EC2 key pair called `my-key`, and Ubuntu 24.04 for both the submit
host and the workers. Ubuntu 24.04 runs Python 3.12 on both sides, so the versions match without
any extra work.

## 1. Create the infrastructure (workstation)

With the fixes (pending upstream PRs), save this as `create_infra.py`:

```python
"""Create (or reuse) the AWSProvider infrastructure and print its IDs."""
import urllib.request

from parsl.providers import AWSProvider

REGION = 'us-west-2'
my_ip = urllib.request.urlopen('https://checkip.amazonaws.com', timeout=10).read().decode().strip()

# Constructing the provider creates the VPC, subnets, internet gateway, route table and
# security group, and writes the state file; no instance is launched. image_id is
# only used when launching, so any value will do here.
provider = AWSProvider(
    image_id='unused',
    key_name=None,
    region=REGION,
    ssh_cidr='{}/32'.format(my_ip),   # lets you SSH to the submit host
    state_file='awsproviderstate.json',
)
print('vpc', provider.vpc_id)
print('security group', provider.sg_id)
print('subnets', ' '.join(provider.sn_ids))
```

With Parsl ≤ 2026.11.09 (released), the constructor also needs credentials it accepts. Replace the
constructor call with:

```python
import os

provider = AWSProvider(
    image_id='unused',
    key_name='my-key',
    region=REGION,
    profile=os.environ['AWS_PROFILE'],
    state_file='awsproviderstate.json',
)
```

The released security group already allows SSH from anywhere. You can narrow it as shown in
[released-workarounds.md](released-workarounds.md#ssh-and-icmp-open-to-the-world-12), but keep your
own address.

Run it:

```bash
python create_infra.py
```

## 2. Create an IAM role for the submit host (workstation)

The submit host gets its AWS credentials from an instance role. Save the policy from
[credentials.md](credentials.md#iam-permissions) as `parsl-aws-provider-policy.json`, then:

```bash
cat > ec2-trust.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"},
                 "Action": "sts:AssumeRole"}]
}
EOF
aws iam create-role --role-name parsl-submit --assume-role-policy-document file://ec2-trust.json
aws iam put-role-policy --role-name parsl-submit --policy-name parsl-aws-provider \
  --policy-document file://parsl-aws-provider-policy.json
aws iam create-instance-profile --instance-profile-name parsl-submit
aws iam add-role-to-instance-profile --instance-profile-name parsl-submit --role-name parsl-submit
```

IAM changes take a few seconds to propagate. If the next step fails with
`Invalid IAM Instance Profile name`, wait and retry.

## 3. Launch the submit host (workstation)

The submit host uses the provider's security group. That group allows all TCP from `10.0.0.0/16`, so the
workers can reach the HTEX worker port without any extra rule. The subnets assign public IPs, so you
can SSH in.

```bash
REGION=us-west-2
AMI=$(aws ssm get-parameter --region $REGION \
  --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query Parameter.Value --output text)
SG=$(python3 -c 'import json; print(json.load(open("awsproviderstate.json"))["sgID"])')
SUBNET=$(python3 -c 'import json; print(json.load(open("awsproviderstate.json"))["snIDs"][0])')

SUBMIT_ID=$(aws ec2 run-instances --region $REGION --image-id "$AMI" --instance-type t3.medium \
  --key-name my-key --subnet-id "$SUBNET" --security-group-ids "$SG" \
  --iam-instance-profile Name=parsl-submit \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=parsl-submit}]' \
  --query 'Instances[0].InstanceId' --output text)
aws ec2 wait instance-running --region $REGION --instance-ids "$SUBMIT_ID"
SUBMIT_IP=$(aws ec2 describe-instances --region $REGION --instance-ids "$SUBMIT_ID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

scp -i ~/.ssh/my-key.pem awsproviderstate.json ubuntu@$SUBMIT_IP:
ssh -i ~/.ssh/my-key.pem ubuntu@$SUBMIT_IP
```

If `run-instances` reports that the instance type is not supported in the subnet's availability zone,
use another subnet from the state file.

Do not give the submit host a `Name` tag starting with `parsl.aws.`. For infrastructure created by
released Parsl, the cleanup script attributes instances by that prefix. It would then treat the
submit host as the provider's and terminate it. The by-tag searches in
[troubleshooting.md](troubleshooting.md#finding-leaked-resources) use the same prefix.

## 4. Run Parsl (submit host)

Install Parsl into a venv and activate it, so that `interchange.py` is on `PATH`:

```bash
sudo apt-get update && sudo apt-get install -y python3-venv
python3 -m venv ~/parsl-venv
# Released:
~/parsl-venv/bin/pip install 'parsl[aws]==2026.11.09'
# Or with the fixes (pending upstream PRs):
~/parsl-venv/bin/pip install 'parsl[aws] @ git+https://github.com/scttfrdmn/parsl@aws-provider-integration'
source ~/parsl-venv/bin/activate
```

With the fixes, save this as `run_in_aws.py`:

```python
"""Run Parsl apps on EC2 from a submit host inside the AWSProvider's VPC."""
import logging

import boto3
import parsl
from parsl.addresses import address_by_route
from parsl.app.app import python_app
from parsl.config import Config
from parsl.executors import HighThroughputExecutor
from parsl.providers import AWSProvider

REGION = 'us-west-2'
UBUNTU_2404 = '/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id'


@python_app
def whereami():
    import platform

    import parsl
    return '{} python={} parsl={}'.format(platform.node(), platform.python_version(), parsl.__version__)


def main():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('botocore').setLevel(logging.WARNING)
    ami = boto3.Session(region_name=REGION).client('ssm').get_parameter(
        Name=UBUNTU_2404)['Parameter']['Value']

    provider = AWSProvider(
        image_id=ami,
        key_name='my-key',            # optional; lets you SSH to workers to read their logs
        region=REGION,
        instance_type='t3.small',
        state_file='awsproviderstate.json',   # the copy from your workstation
        walltime='02:00:00',
        init_blocks=1,
        min_blocks=0,
        max_blocks=4,
    )

    htex = HighThroughputExecutor(
        label='aws_htex',
        address=address_by_route(),   # this host's private IP, in 10.0.0.0/16
        worker_port=54000,
        encrypted=False,              # see networking.md; traffic stays inside the VPC
        provider=provider,
    )

    with parsl.load(Config(executors=[htex])):
        print([f.result() for f in [whereami() for _ in range(8)]])


if __name__ == '__main__':
    main()
```

```bash
python run_in_aws.py
```

The instance role supplies the credentials through boto3's default chain
([#2](https://github.com/scttfrdmn/aws-elastic-parsl/issues/2)).

### Parsl ≤ 2026.11.09 (released) on the submit host

The submit host is itself an instance in the region that the provider did not start. The released
`status()` therefore raises `KeyError` on the first poll every time
([#1](https://github.com/scttfrdmn/aws-elastic-parsl/issues/1)), so you must use the subclass. The
released credential check also rejects instance roles. Point it at an empty profile, which boto3
resolves through instance metadata:

```bash
mkdir -p ~/.aws && printf '[default]\nregion = us-west-2\n' >> ~/.aws/config
```

Copy `released_aws.py` from [released-workarounds.md](released-workarounds.md) to the submit host. Then
use this `run_in_aws.py`:

```python
"""Run Parsl apps on EC2 from a submit host inside the AWSProvider's VPC (Parsl <= 2026.11.09)."""
import logging

import boto3
import parsl
from parsl.addresses import address_by_route
from parsl.app.app import python_app
from parsl.config import Config
from parsl.executors import HighThroughputExecutor

from released_aws import ScopedStatusAWSProvider, released_worker_init

REGION = 'us-west-2'
UBUNTU_2404 = '/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id'


@python_app
def whereami():
    import platform

    import parsl
    return '{} python={} parsl={}'.format(platform.node(), platform.python_version(), parsl.__version__)


def main():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('botocore').setLevel(logging.WARNING)
    ami = boto3.Session(region_name=REGION).client('ssm').get_parameter(
        Name=UBUNTU_2404)['Parameter']['Value']

    provider = ScopedStatusAWSProvider(
        image_id=ami,
        key_name='my-key',
        region=REGION,
        instance_type='t3.small',
        profile='default',            # resolved through the instance role
        worker_init=released_worker_init(walltime_minutes=120),
        state_file='awsproviderstate.json',
        init_blocks=1,
        min_blocks=0,
        max_blocks=4,
    )
    provider.max_nodes = 1000

    htex = HighThroughputExecutor(
        label='aws_htex',
        address=address_by_route(),
        worker_port=54000,
        encrypted=False,
        provider=provider,
    )

    with parsl.load(Config(executors=[htex])):
        print([f.result() for f in [whereami() for _ in range(8)]])


if __name__ == '__main__':
    main()
```

## 5. Clean up (workstation)

First terminate the submit host. Then remove the provider's infrastructure:

```bash
aws ec2 terminate-instances --region us-west-2 --instance-ids "$SUBMIT_ID"
aws ec2 wait instance-terminated --region us-west-2 --instance-ids "$SUBMIT_ID"
```

With the fixes, save this as `teardown.py`:

```python
"""Remove the AWSProvider infrastructure recorded in awsproviderstate.json."""
from parsl.providers import AWSProvider

import sys

# An existing state file is read, not re-created; image_id is not used.
provider = AWSProvider(image_id='unused', key_name=None, region='us-west-2',
                       state_file='awsproviderstate.json')
# True: everything removed and the state file deleted. False: something was left
# (the WARNING in the log says what and why) and the state file is kept.
sys.exit(0 if provider.teardown() else 1)
```

```bash
python teardown.py
```

With the fixes, `teardown()` deletes only resources tagged
`parsl:infrastructure=<this infrastructure's ID>`. It finds the provider's instances by that tag, so
it never terminates the submit host. But a submit host that is still in the VPC is left running, and it
blocks the deletion of the VPC: teardown returns `False`, logs a WARNING naming it, and keeps the state
file. That is why the submit host is terminated first. If that happens, terminate the submit host and
run `teardown.py` again. See [lifecycle-and-cost.md](lifecycle-and-cost.md#teardown).

With Parsl ≤ 2026.11.09 (released), use the cleanup script instead. It is a dry run without `--yes`.
The released provider leaves the internet gateway and route table untagged, so deleting them needs
`--include-untagged`:

```bash
python examples/cleanup_stock_provider.py --region us-west-2 --state-file awsproviderstate.json
python examples/cleanup_stock_provider.py --region us-west-2 --state-file awsproviderstate.json \
  --yes --include-untagged
```

The script skips any VPC that still contains something it can't attribute to the provider, such as a
submit host you forgot to terminate. The dry run lists it under `NOT OURS`.

The IAM role costs nothing to keep. To remove it:

```bash
aws iam remove-role-from-instance-profile --instance-profile-name parsl-submit --role-name parsl-submit
aws iam delete-instance-profile --instance-profile-name parsl-submit
aws iam delete-role-policy --role-name parsl-submit --policy-name parsl-aws-provider
aws iam delete-role --role-name parsl-submit
```
