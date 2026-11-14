# Quickstart: from a laptop

This runs a Parsl script on a laptop or workstation and its apps on EC2. The laptop is behind NAT, so
the workers can't connect to it. Instead, the interchange listens on the laptop's loopback, and an
`ssh -R` reverse tunnel to each instance exposes that port on the instance's own loopback. The workers
connect to `127.0.0.1` on the instance, and SSH carries the traffic home. [networking.md](networking.md)
explains why each setting is needed.

`examples/stock_provider_smoke.py` in this repository is the maintained, tested version of this setup.
It detects whether the installed provider has the fixes. The scripts below are a smaller
version of it, for you to adapt.

## Prerequisites

- **Credentials.** AWS credentials that boto3 can find, for example `AWS_PROFILE`. See [credentials.md](credentials.md).
- **Key pair.** An EC2 key pair in the target region, with its private key on the laptop. The tunnels
  need SSH access to the instances.
- **Python 3.12 on the laptop.** The scripts use Ubuntu 24.04 workers, which have Python 3.12, and the
  interchange rejects any other `major.minor`.
- **`ssh` on `PATH`.** Check `~/.ssh/config` for `ControlMaster` (see [Tunnels keep reconnecting](troubleshooting.md#a-tunnel-keeps-reconnecting)).
  The tunnel code below overrides it.

Create the environment and put its `bin` on `PATH`. HTEX starts `interchange.py` from `PATH`:

```bash
uv venv --python 3.12 .venv
# Released:
uv pip install --python .venv 'parsl[aws]==2026.11.09'
# Or with the fixes (pending upstream PRs):
uv pip install --python .venv 'parsl[aws] @ git+https://github.com/scttfrdmn/parsl@aws-provider-integration'
source .venv/bin/activate
```

## The tunnel helper

Save as `tunnel.py`:

```python
"""Keep an SSH reverse tunnel open from this host to each running instance of an AWSProvider."""
import logging
import subprocess
import threading

log = logging.getLogger(__name__)


class ReverseTunnels(threading.Thread):
    """Run ``ssh -N -R port:127.0.0.1:port user@<public ip>`` for every running instance."""

    def __init__(self, provider, key_path, port, user='ubuntu', interval=5):
        super().__init__(daemon=True, name='reverse-tunnels')
        self.provider = provider
        self.key_path = key_path
        self.port = port
        self.user = user
        self.interval = interval
        self.procs = {}
        self._halt = threading.Event()

    def run(self):
        while not self._halt.is_set():
            try:
                self._reconcile()
            except Exception:
                log.exception('tunnel reconcile failed')
            self._halt.wait(self.interval)

    def _reconcile(self):
        ids = list(self.provider.resources)
        if not ids:
            return
        paginator = self.provider.client.get_paginator('describe_instances')
        for page in paginator.paginate(Filters=[{'Name': 'instance-id', 'Values': ids}]):
            for reservation in page['Reservations']:
                for instance in reservation['Instances']:
                    self._update(instance['InstanceId'], instance['State']['Name'],
                                 instance.get('PublicIpAddress'))

    def _update(self, instance_id, state, ip):
        proc = self.procs.get(instance_id)
        if state != 'running' or not ip:
            if proc and proc.poll() is None:
                proc.terminate()
            return
        if proc is None or proc.poll() is not None:
            log.info('opening reverse tunnel to %s (%s)', instance_id, ip)
            self.procs[instance_id] = subprocess.Popen(
                ['ssh', '-N', '-i', self.key_path,
                 '-o', 'StrictHostKeyChecking=no',
                 '-o', 'UserKnownHostsFile=/dev/null',
                 '-o', 'LogLevel=ERROR',
                 # A ControlMaster from ~/.ssh/config would take over the forward and
                 # make this process exit, so keep each tunnel self-contained.
                 '-o', 'ControlMaster=no',
                 '-o', 'ControlPath=none',
                 '-o', 'ExitOnForwardFailure=yes',
                 '-o', 'ServerAliveInterval=15',
                 '-o', 'ConnectTimeout=10',
                 '-R', '{0}:127.0.0.1:{0}'.format(self.port),
                 '{}@{}'.format(self.user, ip)],
                stdin=subprocess.DEVNULL,
            )

    def stop(self):
        self._halt.set()
        for proc in self.procs.values():
            if proc.poll() is None:
                proc.terminate()
```

Host key checking is off because every instance is new. The tunnel carries only the port you forward.

## With the fixes (pending upstream PRs)

Save as `run_laptop.py`, edit the constants, and run `python run_laptop.py`:

```python
"""Run Parsl apps on EC2 from a laptop, with the fixed AWSProvider."""
import logging
import os
import sys
import urllib.request

import boto3
import parsl
from parsl.app.app import python_app
from parsl.config import Config
from parsl.executors import HighThroughputExecutor
from parsl.providers import AWSProvider

from tunnel import ReverseTunnels

REGION = 'us-west-2'
KEY_NAME = 'my-key'                                   # EC2 key pair in REGION
KEY_PATH = os.path.expanduser('~/.ssh/my-key.pem')    # its private key
WORKER_PORT = 54000
STATE_FILE = os.path.abspath('awsproviderstate.json')
TEARDOWN = True    # remove the VPC at the end; False keeps it for the next run
UBUNTU_2404 = '/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id'


@python_app
def whereami():
    import platform
    import sys

    import parsl
    return '{} python={} parsl={} exe={}'.format(
        platform.node(), platform.python_version(), parsl.__version__, sys.executable)


@python_app
def square(x):
    return x * x


def main():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('botocore').setLevel(logging.WARNING)
    if sys.version_info[:2] != (3, 12):
        sys.exit('Ubuntu 24.04 workers run Python 3.12, so run this script with Python 3.12')

    session = boto3.Session(region_name=REGION)
    ami = session.client('ssm').get_parameter(Name=UBUNTU_2404)['Parameter']['Value']
    my_ip = urllib.request.urlopen('https://checkip.amazonaws.com', timeout=10).read().decode().strip()

    provider = AWSProvider(
        image_id=ami,
        key_name=KEY_NAME,
        region=REGION,
        instance_type='t3.small',
        ssh_cidr='{}/32'.format(my_ip),   # the tunnels need SSH from this laptop
        state_file=STATE_FILE,
        walltime='01:00:00',
        init_blocks=1,
        min_blocks=0,
        max_blocks=1,
    )

    htex = HighThroughputExecutor(
        label='aws_htex',
        address='127.0.0.1',          # what workers dial: their own loopback, tunnelled home
        worker_port=WORKER_PORT,      # fixed, so that the tunnel knows which port to forward
        encrypted=False,              # the CurveZMQ certificates never reach the workers
        max_workers_per_node=2,
        provider=provider,
    )

    tunnels = ReverseTunnels(provider, KEY_PATH, WORKER_PORT)
    tunnels.start()
    try:
        with parsl.load(Config(executors=[htex])):
            print('worker:', whereami().result(timeout=1200))
            print('squares:', [f.result() for f in [square(i) for i in range(10)]])
    finally:
        tunnels.stop()
        # teardown() returns False, keeping the state file, if anything was left behind.
        if TEARDOWN and not provider.teardown():
            print('teardown left resources behind; see the WARNING in the log')


if __name__ == '__main__':
    main()
```

What happens:

1. **Constructing the provider.** This creates the VPC, subnets, internet gateway, route table and
   security group, all tagged as this infrastructure, unless `STATE_FILE` already exists. It then
   writes the state file. The security group allows SSH only from `ssh_cidr` (a bare address would
   also be accepted as `/32`). If the state file already exists, its security group is reused
   unchanged, and a WARNING says so. See
   [networking.md](networking.md#security-group-rules).
2. **Loading the config.** `parsl.load` starts the interchange on `127.0.0.1:54000` and launches one
   instance. The first status poll no longer fails ([#1](https://github.com/scttfrdmn/aws-elastic-parsl/issues/1)).
3. **Booting the instance.** The user data installs Parsl into `/opt/parsl-venv`, pinned to your version,
   and starts the worker pool. The worker probes `127.0.0.1:54000` for up to 30 s. By then the tunnel is
   normally up, because SSH is ready well before the install finishes. In a live test the first result
   arrived 89 s after start.
4. **Shutting down.** When the `with` block exits, Parsl cancels the block, which terminates the
   instance. The instance also powers itself off if its worker exits, and at `walltime`, even if the
   laptop goes away ([#7](https://github.com/scttfrdmn/aws-elastic-parsl/issues/7),
   [#8](https://github.com/scttfrdmn/aws-elastic-parsl/issues/8)).
5. **Tearing down.** `provider.teardown()` terminates the provider's remaining instances, waits for
   them, deletes the tagged infrastructure and the state file, and returns `True`
   ([#4](https://github.com/scttfrdmn/aws-elastic-parsl/issues/4)). This took about 50 s in a live
   test. If anything is left, it returns `False` and keeps the state file. See
   [lifecycle-and-cost.md](lifecycle-and-cost.md#teardown).

You can pass `teardown_on_shutdown=True` to the provider instead of calling `teardown()`
([#5](https://github.com/scttfrdmn/aws-elastic-parsl/issues/5)). Parsl then tears down when the
executor shuts down, after the blocks are cancelled.

<!-- PENDING(#5): teardown_on_shutdown is on the aws-provider-teardown-on-shutdown branch. -->

## Parsl ≤ 2026.11.09 (released)

The released provider needs the workarounds from [released-workarounds.md](released-workarounds.md).
Save `released_aws.py` from that page next to `tunnel.py`, then save this as `run_laptop_released.py`:

```python
"""Run Parsl apps on EC2 from a laptop, with the released AWSProvider (Parsl <= 2026.11.09)."""
import logging
import os
import sys

import boto3
import parsl
from parsl.app.app import python_app
from parsl.config import Config
from parsl.executors import HighThroughputExecutor

from released_aws import ScopedStatusAWSProvider, released_worker_init
from tunnel import ReverseTunnels

REGION = 'us-west-2'
KEY_NAME = 'my-key'
KEY_PATH = os.path.expanduser('~/.ssh/my-key.pem')
WORKER_PORT = 54000
STATE_FILE = os.path.abspath('awsproviderstate.json')
UBUNTU_2404 = '/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id'


@python_app
def whereami():
    import platform
    import sys

    import parsl
    return '{} python={} parsl={} exe={}'.format(
        platform.node(), platform.python_version(), parsl.__version__, sys.executable)


@python_app
def square(x):
    return x * x


def main():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('botocore').setLevel(logging.WARNING)
    if sys.version_info[:2] != (3, 12):
        sys.exit('Ubuntu 24.04 workers run Python 3.12, so run this script with Python 3.12')
    profile = os.environ['AWS_PROFILE']   # the released provider ignores AWS_PROFILE itself

    session = boto3.Session(profile_name=profile, region_name=REGION)
    ami = session.client('ssm').get_parameter(Name=UBUNTU_2404)['Parameter']['Value']

    provider = ScopedStatusAWSProvider(
        image_id=ami,
        key_name=KEY_NAME,
        region=REGION,
        instance_type='t3.small',
        profile=profile,
        worker_init=released_worker_init(walltime_minutes=60),
        state_file=STATE_FILE,
        init_blocks=1,
        min_blocks=0,
        max_blocks=1,
    )
    provider.max_nodes = 1000

    htex = HighThroughputExecutor(
        label='aws_htex',
        address='127.0.0.1',
        worker_port=WORKER_PORT,
        encrypted=False,
        max_workers_per_node=2,
        provider=provider,
    )

    tunnels = ReverseTunnels(provider, KEY_PATH, WORKER_PORT)
    tunnels.start()
    try:
        with parsl.load(Config(executors=[htex])):
            print('worker:', whereami().result(timeout=1200))
            print('squares:', [f.result() for f in [square(i) for i in range(10)]])
    finally:
        tunnels.stop()


if __name__ == '__main__':
    main()
```

The released `teardown()` does not work, so remove the VPC with this repository's cleanup script. It is a
dry run unless you pass `--yes`. The released provider leaves its internet gateway and route table
untagged, so deleting them also needs `--include-untagged`:

```bash
python examples/cleanup_stock_provider.py --region us-west-2 --state-file awsproviderstate.json
python examples/cleanup_stock_provider.py --region us-west-2 --state-file awsproviderstate.json \
  --yes --include-untagged
```

See [lifecycle-and-cost.md](lifecycle-and-cost.md#cleaning-up) for what the script will and won't delete.

On the released provider, the security group opens SSH to `0.0.0.0/0`, so the tunnels work without
any change. To narrow it, see [released-workarounds.md](released-workarounds.md#ssh-and-icmp-open-to-the-world-12).

## Adapting the scripts

- **More blocks.** Raise `max_blocks`. The tunnel helper opens one tunnel per running instance. All
  tunnels forward the same port, because each one binds the instance's own loopback.
- **Another AMI.** Change `user` in `ReverseTunnels` to the image's login user (`ec2-user` on Amazon
  Linux, `admin` on Debian), and match the submit-side Python. See [worker-environment.md](worker-environment.md).
- **Slow boots.** Give the worker more time to find the tunnel by passing
  `address_probe_timeout=120` to `HighThroughputExecutor`.
- **Your own dependencies.** On the fixed provider, `worker_init='pip install numpy'` installs into
  the worker venv. On the released provider, pass `extra='pip install numpy'` to `released_worker_init()`.
