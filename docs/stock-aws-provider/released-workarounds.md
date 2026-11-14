# Workarounds for Parsl ≤ 2026.11.09 (released)

The released `AWSProvider` has defects that stop it from launching a block, installing a working
worker, or stopping an instance. All of them are fixed on the pending upstream branches (see the table in
[index.md](index.md)). If you must use a release, this page gives a workaround for each defect that has
one. The workarounds touch only your own code: a small subclass and a `worker_init` string. They do not
patch Parsl.

The workarounds assume an apt-based image such as Ubuntu 24.04. The released user-data script installs
packages with `apt-get` only, so dnf-based images (Amazon Linux 2023) need a different `worker_init`.

## The workaround module

Save this as `released_aws.py` next to your Parsl script. The quickstarts import it.

```python
"""Workarounds for parsl.providers.AWSProvider in Parsl <= 2026.11.09.

See https://github.com/scttfrdmn/aws-elastic-parsl/blob/main/docs/stock-aws-provider/released-workarounds.md
"""
import shlex

import parsl
from parsl.jobs.states import JobState, JobStatus
from parsl.providers import AWSProvider
from parsl.providers.aws.aws import translate_table


class ScopedStatusAWSProvider(AWSProvider):
    """AWSProvider whose status() only describes this provider's own instances.

    The released status() calls describe_instances(InstanceIds=[]) before the first
    block exists, which describes every instance in the region, and then raises
    KeyError on the first one it did not start (Parsl/parsl#3764).
    """

    def status(self, job_ids):
        job_ids = list(job_ids)
        if not job_ids:
            return []
        found = {}
        paginator = self.client.get_paginator('describe_instances')
        for page in paginator.paginate(Filters=[{'Name': 'instance-id', 'Values': job_ids}]):
            for reservation in page['Reservations']:
                for instance in reservation['Instances']:
                    found[instance['InstanceId']] = translate_table.get(
                        instance['State']['Name'], JobState.UNKNOWN)
        statuses = []
        for job_id in job_ids:
            # EC2 stops reporting an instance some time after it terminates.
            status = JobStatus(found.get(job_id, JobState.COMPLETED))
            if job_id in self.resources:
                self.resources[job_id]['status'] = status
            statuses.append(status)
        return statuses


def released_worker_init(walltime_minutes=60, requirement=None, extra=''):
    """worker_init for apt-based images that installs a working, version-matched worker
    and makes the instance shut down (and so terminate) when the worker exits, and in
    any case after walltime_minutes.

    requirement defaults to the submit side's Parsl version.
    extra is appended, for your own setup commands.
    """
    if requirement is None:
        requirement = 'parsl=={}'.format(parsl.__version__)
    return '\n'.join([
        "trap 'shutdown -h now' EXIT",
        'shutdown -h +{}'.format(int(walltime_minutes)),
        'apt-get install -y python3-venv',
        'python3 -m venv /opt/parsl-venv',
        '/opt/parsl-venv/bin/pip install {}'.format(shlex.quote(requirement)),
        'export PATH="/opt/parsl-venv/bin:$PATH"',
        extra,
    ])
```

Use it like this:

```python
import os

from released_aws import ScopedStatusAWSProvider, released_worker_init

provider = ScopedStatusAWSProvider(
    image_id='ami-0123456789abcdef0',      # an Ubuntu 24.04 AMI in the region below
    key_name='my-key',                     # must not be None on the released provider
    region='us-west-2',                    # the released default is always us-east-2
    instance_type='t3.small',
    profile=os.environ['AWS_PROFILE'],     # the released check ignores AWS_PROFILE
    worker_init=released_worker_init(walltime_minutes=60),
    state_file='awsproviderstate.json',
    init_blocks=1,
    min_blocks=0,
    max_blocks=1,
)
# The released capacity check counts every instance this object has launched and not
# cancelled, including instances that shut themselves down. Raise that limit; the
# scaling strategy still enforces max_blocks for concurrent blocks.
provider.max_nodes = 1000
```

Complete scripts using this module are in [quickstart-laptop.md](quickstart-laptop.md) and
[quickstart-in-aws.md](quickstart-in-aws.md).

## Defect by defect

### No block launches: `KeyError: 'i-…'` ([#1](https://github.com/scttfrdmn/aws-elastic-parsl/issues/1))

The first status poll raises `KeyError` with the ID of any instance in the region that this provider
object did not start. That includes other people's instances and your submit host. It also includes
instances from your own earlier runs, and terminated instances, which EC2 keeps reporting for a while.
Parsl launches init blocks from the same poll, so nothing ever launches.

**Workaround.** Use `ScopedStatusAWSProvider`. Without it, the released provider works only in a region
where the account has no instances at all.

### `AWS_PROFILE`, SSO and instance roles are rejected ([#2](https://github.com/scttfrdmn/aws-elastic-parsl/issues/2))

The constructor raises `ConfigurationError` unless you pass `profile=` or `key_file=`, or set both
`AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`.

**Workaround.**

- **Laptop:** pass `profile=os.environ['AWS_PROFILE']`. Named profiles that use SSO, `credential_process`
  or role assumption all work through `profile=`.
- **EC2 instance role:** create a `[default]` section in `~/.aws/config` (a `region` line is enough) and
  pass `profile='default'`. An explicit profile with no credentials of its own still falls through to
  the container and instance-metadata providers in boto3's chain. See [credentials.md](credentials.md).

### `key_file` with the documented keys raises `TypeError` ([#3](https://github.com/scttfrdmn/aws-elastic-parsl/issues/3))

The released code passes the JSON object to `boto3.session.Session(**creds)`.

**Workaround.** Use the boto3 argument names (`aws_access_key_id`, `aws_secret_access_key`,
`aws_session_token`) and no other keys. Prefer a profile anyway.

### Worker install fails: `process_worker_pool.py: command not found` ([#6](https://github.com/scttfrdmn/aws-elastic-parsl/issues/6))

The released user data runs `pip3 install numpy scipy parsl` as root. Ubuntu 24.04 refuses it (PEP 668).
`--break-system-packages` fails too, on Debian-owned packages. On images where the install works, it
installs the latest Parsl, which a submit side on any other version rejects with `VersionMismatch`.

**Workaround.** `released_worker_init()` builds a venv pinned to the submit side's version, and puts its
`bin` first on `PATH`. The released script inlines `worker_init` in the same shell before the worker
command, so the `export` carries over. The failing `pip3 install` still runs first, but it fails quickly
and does not stop the script.

### Instances never shut down ([#7](https://github.com/scttfrdmn/aws-elastic-parsl/issues/7)) and walltime is ignored ([#8](https://github.com/scttfrdmn/aws-elastic-parsl/issues/8))

The released script puts `halt` after the launcher. The launcher ends with `exit`, so the `halt` is never
reached. An instance whose worker exits, or never started, keeps running and billing until Parsl cancels
the block. Parsl does not cancel it if the submit process dies, if the executor is in a bad state (for
example after a `VersionMismatch`), or if `linger=True`.

**Workaround.** `released_worker_init()` sets `trap 'shutdown -h now' EXIT` and schedules
`shutdown -h +<walltime_minutes>`. Because the launcher's `exit` runs in the same shell, the trap fires
when the worker exits for any reason. The provider launches instances with
`InstanceInitiatedShutdownBehavior=terminate`, so shutting down terminates the instance. The provider's
own `walltime` and `linger` parameters have no effect on the released provider. Pass the walltime to
`released_worker_init()` instead. The trap also ignores `linger`, so leave `linger` at `False`.

### `submit()` returns `None`; `key_name=None` fails ([#9](https://github.com/scttfrdmn/aws-elastic-parsl/issues/9))

When a launch fails, the released provider logs the error and returns `None`. Parsl then reports
`ScalingFailed: Attempt to provision nodes did not return a job ID`, and the real cause is only in the
log. `KeyName=None` is always sent, which botocore rejects.

**Workaround.** Always pass a real `key_name`. When you see `ScalingFailed`, look for the EC2 error
(`InvalidAMIID.NotFound`, `VcpuLimitExceeded`, `InsufficientInstanceCapacity` and so on) in
`runinfo/<NNN>/parsl.log`. Set `provider.max_nodes` high, as shown above, so that replacement blocks are
not refused.

### `teardown()` always fails ([#4](https://github.com/scttfrdmn/aws-elastic-parsl/issues/4)) and the VPC is permanent ([#5](https://github.com/scttfrdmn/aws-elastic-parsl/issues/5))

The released `teardown()` raises before it removes anything useful. Parsl never calls it anyway.

**Workaround.** Use `examples/cleanup_stock_provider.py` from this repository. The released provider
does not tag its internet gateway or route table, so the script deletes them only with
`--include-untagged`:

```bash
python examples/cleanup_stock_provider.py --region us-west-2 --state-file awsproviderstate.json
python examples/cleanup_stock_provider.py --region us-west-2 --state-file awsproviderstate.json \
  --yes --include-untagged
```

Upgrading to the fixed provider does not change this. Its `teardown()` deletes only resources tagged
with an infrastructure ID, so it leaves released-Parsl infrastructure in place and returns `False`.
See [lifecycle-and-cost.md](lifecycle-and-cost.md#cleaning-up).

### State-file problems leak VPCs ([#10](https://github.com/scttfrdmn/aws-elastic-parsl/issues/10))

A state file that exists but can't be parsed is treated as missing, so a new VPC is created. A VPC
creation that fails part way leaves resources with no state file recording them. The default state
file is relative to the current directory.

**Workaround.** Always pass an absolute or script-relative `state_file`, for example
`state_file=str(Path(__file__).with_name('awsproviderstate.json'))`. After any failed start, run the
cleanup script in dry-run mode.

### Region default ([#11](https://github.com/scttfrdmn/aws-elastic-parsl/issues/11))

`region` defaults to `us-east-2` regardless of `AWS_DEFAULT_REGION` or your profile's region.

**Workaround.** Always pass `region=`. The AMI ID must come from that region.

### SSH and ICMP open to the world ([#12](https://github.com/scttfrdmn/aws-elastic-parsl/issues/12))

**Workaround.** After the provider has created its security group (once, when the state file is
written), narrow it with the AWS CLI. The group is reused through the state file, so the change persists:

```bash
SG=$(python3 -c 'import json; print(json.load(open("awsproviderstate.json"))["sgID"])')
MYIP=$(curl -s https://checkip.amazonaws.com)
aws ec2 revoke-security-group-ingress --region us-west-2 --group-id "$SG" \
  --protocol tcp --port 22 --cidr 0.0.0.0/0
aws ec2 revoke-security-group-ingress --region us-west-2 --group-id "$SG" \
  --ip-permissions 'IpProtocol=icmp,FromPort=-1,ToPort=-1,IpRanges=[{CidrIp=0.0.0.0/0}]'
# Match the fixed provider: ICMP within the VPC, and "fragmentation needed" from anywhere
# for path-MTU discovery (the VPC MTU is 9001; the internet gateway path is 1500).
aws ec2 authorize-security-group-ingress --region us-west-2 --group-id "$SG" \
  --ip-permissions 'IpProtocol=icmp,FromPort=-1,ToPort=-1,IpRanges=[{CidrIp=10.0.0.0/16}]' \
                   'IpProtocol=icmp,FromPort=3,ToPort=4,IpRanges=[{CidrIp=0.0.0.0/0}]'
aws ec2 authorize-security-group-ingress --region us-west-2 --group-id "$SG" \
  --protocol tcp --port 22 --cidr "$MYIP/32"   # only if you need SSH (the laptop tunnel does)
```

The fixed provider reuses a security group from an existing state file without modifying it, and
logs a WARNING that names it. A group created by released Parsl therefore keeps these open rules
after you upgrade, until you narrow it as above or recreate the infrastructure.
