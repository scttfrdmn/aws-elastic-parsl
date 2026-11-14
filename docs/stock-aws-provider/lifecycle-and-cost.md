# Lifecycle and cost

## The life of a run

1. **`AWSProvider(...)`.** The constructor reads the state file, or creates the VPC infrastructure and
   writes one. No instance is launched yet.
2. **`parsl.load(config)`.** This starts the interchange. The scaling strategy then launches
   `init_blocks` blocks, one instance each, and keeps between `min_blocks` and `max_blocks` blocks
   running according to the task load. The provider polls EC2 every 60 s.
3. **Scaling in.** The strategy cancels blocks that have been idle for `Config.max_idletime` (default
   120 s), down to `min_blocks`. `cancel()` calls `TerminateInstances`.
4. **Shutdown** (end of the `with parsl.load(...)` block, `parsl.dfk().cleanup()`, or interpreter exit).
   Parsl cancels all remaining blocks unless the executor is in a bad state. It then shuts down the
   executor. With the fixes and `teardown_on_shutdown=True`, the executor's shutdown also tears down
   the infrastructure ([#5](https://github.com/scttfrdmn/aws-elastic-parsl/issues/5)).
5. **The infrastructure.** It stays, unless you tear it down. Parsl never calls the provider's
   `goodbye()` or `teardown()` on its own.

<!-- PENDING(#5): teardown_on_shutdown is on the aws-provider-teardown-on-shutdown branch. -->

Instances that the shutdown step does **not** stop:

- instances whose submit process was killed (SIGKILL, laptop sleep or crash, lost SSH session);
- all blocks of an executor in a bad state, for example after a `VersionMismatch` or repeated block
  failures, because Parsl skips scaling in such an executor;
- all instances when `linger=True`, because `cancel()` is ignored.

What happens to them depends on the version:

- **Released:** they run until you terminate them.
- **With the fixes:** unless `linger=True`, each instance still ends itself. Either the exit trap fires
  when its worker gives up on the interchange, or the scheduled walltime shutdown is reached.
  Neither needs Parsl.

## Instance shutdown

### Parsl ≤ 2026.11.09 (released) ([#7](https://github.com/scttfrdmn/aws-elastic-parsl/issues/7))

Instances never shut themselves down. The `halt` at the end of the user data comes after the
launcher's `exit` and is never reached. On systemd, `halt` would not power off anyway. An instance
whose worker failed to install, crashed, or lost its interchange keeps running until someone terminates
it. Use the `worker_init` trap from [released-workarounds.md](released-workarounds.md).

### With the fixes ([#7](https://github.com/scttfrdmn/aws-elastic-parsl/issues/7))

Unless `linger=True`, the user data sets `trap 'shutdown -h now' EXIT` before doing anything else. The
instance powers off, and so terminates, when the worker pool exits for any reason: success, crash,
failed install, failed `worker_init`, or loss of the interchange. A worker that loses its interchange
exits after the HTEX `heartbeat_threshold` (default 120 s). In a live test, an instance whose worker
exited with status 1 was `shutting-down` 91 s after start and `terminated` at 106 s, with no cancel
from Parsl.

## Walltime

- **Parsl ≤ 2026.11.09 (released) ([#8](https://github.com/scttfrdmn/aws-elastic-parsl/issues/8)).**
  `walltime` is accepted and ignored. Instances run until cancelled.
- **With the fixes ([#8](https://github.com/scttfrdmn/aws-elastic-parsl/issues/8)).** Unless
  `linger=True`, each instance runs `shutdown -h +<minutes>` at boot.

  **This is a behaviour change.** The default `walltime='01:00:00'` now ends every instance after one
  hour, where before it ran until cancelled. Set `walltime` for long runs.

Details:

- **Start of the clock.** It starts at boot, so package and Parsl installation count against it.
- **Precision.** The value is `HH:MM:SS`, rounded **up** to whole minutes, with a minimum of 1. For
  example, `00:00:30` becomes 1 minute and `00:01:01` becomes 2.
- **Running tasks.** Tasks still running when the instance shuts down fail with `ManagerLost`. To avoid
  this, set the HTEX `drain_period` (in seconds) somewhat below the walltime, so that the worker pool
  stops accepting tasks and exits first. Set `Config(retries=...)` so lost tasks run again.
- **Replacement.** When a block ends at walltime and work remains, the scaling strategy launches a
  replacement block, up to `max_blocks`.

```python
from parsl.config import Config
from parsl.executors import HighThroughputExecutor
from parsl.providers import AWSProvider

config = Config(
    retries=2,
    executors=[HighThroughputExecutor(
        label='aws_htex',
        address='127.0.0.1',
        worker_port=54000,
        encrypted=False,
        drain_period=4 * 3600 - 15 * 60,     # stop taking tasks 15 minutes before walltime
        provider=AWSProvider(
            image_id='ami-0123456789abcdef0',
            key_name='my-key',
            region='us-west-2',
            walltime='04:00:00',
        ),
    )],
)
```

## `linger`

`linger=True` keeps instances running after their worker exits.

- **Released:** `linger` only controls the unreachable `halt`. `cancel()` returns `False` for every
  block and terminates nothing.
- **With the fixes:**
  - There is no exit trap and no walltime shutdown.
  - `cancel()` is still ignored, so Parsl can't stop the instances.
  - `teardown_on_shutdown` is skipped, with a warning.

You must terminate lingering instances yourself:

```bash
aws ec2 describe-instances --region us-west-2 \
  --filters 'Name=tag:Name,Values=parsl.aws.*' 'Name=instance-state-name,Values=pending,running' \
  --query 'Reservations[].Instances[].InstanceId' --output text
aws ec2 terminate-instances --region us-west-2 --instance-ids i-0123456789abcdef0
```

Use `linger` only for debugging a worker interactively.

## Spot instances

`spot_max_bid > 0` launches each block as a one-time Spot Instance with that maximum price, in USD
per instance-hour. The interruption behaviour is `terminate`.

```python
provider = AWSProvider(
    image_id='ami-0123456789abcdef0',
    key_name='my-key',
    region='us-west-2',
    instance_type='c7i.large',
    spot_max_bid=0.10,
)
```

- **Price too low.** If the Spot price is above your maximum, the launch fails.
  - **With the fixes:** this raises `SubmitException` with EC2's error.
  - **Released:** it logs the error and returns `None`, which Parsl reports as `ScalingFailed`.

  Setting the maximum to the on-demand price avoids most such failures.
- **Interruptions.** An interruption terminates the instance. Parsl sees the block as completed,
  tasks running on it fail with `ManagerLost`, and the strategy may launch a replacement. Use
  `Config(retries=...)`.
- **Quota.** Spot has its own vCPU quota, separate from the on-demand one.

## Teardown

### With the fixes ([#4](https://github.com/scttfrdmn/aws-elastic-parsl/issues/4), [#10](https://github.com/scttfrdmn/aws-elastic-parsl/issues/10))

**Teardown deletes only what the provider created.** Every resource the provider creates is tagged at
creation time with `parsl:provider=AWSProvider` and `parsl:infrastructure=<infrastructure ID>`. That
covers the VPC, subnets, security group, internet gateway, route table and instances. The
infrastructure ID is the VPC's `parsl.aws.<timestamp>` name, recorded in the state file as
`infrastructureID`. Teardown, and the rollback after a failed creation, describe each resource
first. They delete it only if its `parsl:infrastructure` tag matches.

`provider.teardown()` (or `provider.goodbye()`, which calls it) does the following:

1. **Terminates the provider's instances and waits for them.** These are the instances in the VPC
   tagged with this infrastructure ID, plus any this provider object launched in this session. They
   are found by tag filter, not as "everything in the VPC". The wait is at most about 10 minutes.
2. **Stops if any other instance is still in the VPC.** That instance is left running, and so is
   everything else.
3. **Deletes the infrastructure.** If the VPC carries this infrastructure ID, teardown deletes the
   security group, detaches and deletes the internet gateway, then deletes the subnets, the route
   table and the VPC. Each step happens only if the resource's tag matches.
4. **Removes the state file.**

**`teardown()` returns `True` or `False`.**

- **`True`:** everything was removed and the state file was deleted. This also happens when the VPC
  no longer exists.
- **`False`:** something was left. The state file is kept, and a WARNING in the log lists what was
  left and why.

It raises only for unexpected EC2 errors. A resource that is already gone is skipped, so you can run
teardown again from the same state file after fixing the cause. Check the return value:

```python
if not provider.teardown():
    print('teardown left resources behind; see the WARNING in the log')
```

Teardown leaves these in place and returns `False`; nothing raises:

- **Instances the provider did not launch.** A submit host, or anything else you launched into the
  VPC, is left running. It also blocks the deletion of the VPC and its resources. Terminate such
  instances, then run teardown again.
- **Untagged resources inside the VPC,** and resources tagged with a different infrastructure ID.
- **State files with no `infrastructureID`.** These were written by released Parsl, which did not
  tag anything, by an earlier version of the fixes, or by hand. Nothing is deleted, except instances
  this provider object launched in this session. The WARNING lists the resource IDs. Remove them
  deliberately with the cleanup script (below).
- **State files pointing at an untagged VPC,** or a VPC with another infrastructure ID.

To tear down automatically when Parsl shuts down, pass `teardown_on_shutdown=True`. The default is
`False`, which keeps the VPC for reuse through the state file. Teardown then runs after the blocks are
cancelled. It is skipped, with a warning, when `linger=True`.

### Parsl ≤ 2026.11.09 (released) ([#4](https://github.com/scttfrdmn/aws-elastic-parsl/issues/4))

`teardown()` cannot succeed:

- It deletes the internet gateway without detaching it, and deletes subnets and the security group
  before the instances are gone, so both fail with `DependencyViolation`.
- On a provider loaded from a state file, the internet gateway and route table IDs were never
  saved, so it raises `AttributeError`.
- It ends with `os.remove(self.config['state_file_path'])`, and there is no `self.config`.

Use the cleanup script.

## Cleaning up

`examples/cleanup_stock_provider.py` finds VPCs in a region that are named `parsl.aws.*` or tagged
`parsl:provider=AWSProvider`. It deletes only resources it can attribute to the provider. It is a dry
run unless you pass `--yes`.

- **Tagged VPCs (the fixed provider).** A resource counts as the provider's only if its
  `parsl:infrastructure` tag matches the VPC's.
- **Legacy VPCs (released Parsl).** Ownership is inferred from the provider's `Name` tags:
  - instances named `parsl.aws.*`;
  - subnets named `<vpc name>.<zone>`;
  - the security group named `<vpc name>`.

  The internet gateway and route table of a legacy VPC carry no tags, so their origin can't be
  proven. They are deleted only with `--include-untagged`. Without it, the legacy VPC is reported and
  skipped.
- **Anything not attributable** in a VPC means the whole VPC is reported and skipped. Examples are an
  instance you launched (such as a submit host), an unrecognised subnet or security group, or a
  gateway with another tag.
- **Deletion order.** For a VPC it can delete, the script terminates the instances and waits, then
  deletes the security group, the internet gateway (detached first), the subnets and the route table.
  The VPC goes last, along with its default security group and main route table.

```bash
# Dry run: lists what it would delete, what it would skip, and why
AWS_PROFILE=myprofile python examples/cleanup_stock_provider.py --region us-west-2
# Delete fixed-provider (tagged) infrastructure, and remove the state file if it names a deleted VPC
AWS_PROFILE=myprofile python examples/cleanup_stock_provider.py --region us-west-2 \
  --state-file awsproviderstate.json --yes
# Released-Parsl (legacy) VPCs also need --include-untagged
AWS_PROFILE=myprofile python examples/cleanup_stock_provider.py --region us-west-2 \
  --state-file awsproviderstate.json --yes --include-untagged
```

- **Scope.** It considers **all** provider VPCs in the region, not just the one in your state file.
  If other people run Parsl in the same account and region, check the dry run first.
- **`--include-untagged`.** It deletes the untagged internet gateway and route tables of a legacy VPC,
  on the evidence of the VPC's name alone. Use it only after the dry run shows that VPC is yours.
- **`--state-file`.** The default is `runinfo/awsproviderstate.json`, as used by the smoke test.
  Pass the path you actually use. The file is removed only if it names a VPC that was deleted.
- **Region.** Without `--region`, it uses your profile's region. The released provider defaults to
  `us-east-2`, so check there too.

To do the same by hand, use this order (replace the IDs):

```bash
R=us-west-2; VPC=vpc-0123456789abcdef0
aws ec2 describe-instances --region $R --filters Name=vpc-id,Values=$VPC \
  Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down \
  --query 'Reservations[].Instances[].InstanceId' --output text
aws ec2 terminate-instances --region $R --instance-ids <ids> && \
  aws ec2 wait instance-terminated --region $R --instance-ids <ids>
aws ec2 describe-internet-gateways --region $R --filters Name=attachment.vpc-id,Values=$VPC \
  --query 'InternetGateways[].InternetGatewayId' --output text
aws ec2 detach-internet-gateway --region $R --internet-gateway-id <igw> --vpc-id $VPC
aws ec2 delete-internet-gateway --region $R --internet-gateway-id <igw>
aws ec2 describe-subnets --region $R --filters Name=vpc-id,Values=$VPC \
  --query 'Subnets[].SubnetId' --output text            # delete-subnet --subnet-id each
aws ec2 describe-route-tables --region $R --filters Name=vpc-id,Values=$VPC \
  --query 'RouteTables[?!(Associations[?Main])].RouteTableId' --output text   # delete-route-table each
aws ec2 describe-security-groups --region $R --filters Name=vpc-id,Values=$VPC \
  --query 'SecurityGroups[?GroupName!=`default`].GroupId' --output text       # delete-security-group each
aws ec2 delete-vpc --region $R --vpc-id $VPC
```

## The state file

`state_file` (default `awsproviderstate.json`) records the VPC, security group, subnet, internet
gateway and route table IDs. With the fixes it also records the `infrastructureID`, which teardown uses
to recognise the provider's resources. The released provider records only the first three IDs.
The provider manages this file: don't write or edit it by hand. Teardown never deletes resources that
lack the infrastructure ID the file records.

- **Relative paths.** A relative path, including the default, is relative to the **current working
  directory**. A run from another directory doesn't find the file and creates another VPC. Use an
  absolute path. With the fixes, the constructor logs the absolute path it reads or writes.
- **Account and region.** A state file is specific to one account and region. Pointing a provider at
  a file from another region fails at the first launch (`InvalidSubnetID.NotFound`).
- **Deleting it.** Deleting the state file does not delete anything in AWS. It only makes the next run
  create another VPC. Tear down first.
- **Corrupt files.**
  - **Released:** a file that cannot be parsed is silently treated as missing, and a new VPC is
    created ([#10](https://github.com/scttfrdmn/aws-elastic-parsl/issues/10)).
  - **With the fixes:** it raises `ConfigurationError`.
- **Copying.** Copying the file to another host makes that host reuse the same infrastructure. The
  in-AWS quickstart relies on this.

## Leaks

| What leaks | Typical cause | Cost |
|---|---|---|
| Running instances | Released provider and any failure (no self-shutdown). `linger=True`. Submit process killed. Executor in a bad state. | Instance-hours, EBS root volume, public IPv4 address |
| VPC, subnets, IGW, route table, SG | Every run on the released provider. Any run with the fixes that does not tear down. Corrupt or missing state file. SIGKILL during infrastructure creation, which leaves no state file ([#10](https://github.com/scttfrdmn/aws-elastic-parsl/issues/10), a remaining gap). With the fixes the resources are tagged from creation, so the cleanup script still finds them. | None directly, but they use the VPC and internet gateway quotas |
| Detached internet gateways | Interrupted manual or released teardown | None, but they count against the quota |

EBS root volumes are deleted with their instances: the AMIs above set `DeleteOnTermination`. Each
running instance has a public IPv4 address, which AWS bills per hour.

To check for leaks, see [Finding leaked resources](troubleshooting.md#finding-leaked-resources).

## Quotas

| Quota (default) | Error | Notes |
|---|---|---|
| VPCs per region: 5 | `VpcLimitExceeded` on construction | Every leaked provider VPC uses one. The default VPC counts too. |
| Internet gateways per region: 5 | `InternetGatewayLimitExceeded` | Matches the VPC quota |
| Running On-Demand Standard instances, in vCPUs | `VcpuLimitExceeded` on launch | New accounts can have low limits |
| Spot vCPUs | `MaxSpotInstanceCountExceeded` | Separate from on-demand |

- **With the fixes:** a failed launch raises `SubmitException` with the EC2 error.
- **Released:** the error is only in `parsl.log`, and Parsl reports `ScalingFailed`.

Quota increases are requested through Service Quotas.
