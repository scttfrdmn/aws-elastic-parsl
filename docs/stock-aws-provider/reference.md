# Reference: `AWSProvider` constructor

```python
from parsl.providers import AWSProvider     # also parsl.providers.aws.aws.AWSProvider

AWSProvider(
    image_id,                     # required
    key_name,                     # required (positional, no default)
    init_blocks=1,
    min_blocks=0,
    max_blocks=10,
    nodes_per_block=1,
    parallelism=1,
    worker_init='',
    instance_type='t2.small',     # with the fixes: 't3.small' (#11)
    region='us-east-2',           # with the fixes: None (#11)
    spot_max_bid=0,
    key_file=None,
    profile=None,
    iam_instance_profile_arn='',
    state_file=None,
    walltime='01:00:00',
    linger=False,
    launcher=SingleNodeLauncher(),
    parsl_requirement=None,       # with the fixes only (#6)
    ssh_cidr=None,                # with the fixes only (#12)
    teardown_on_shutdown=False,   # with the fixes only (#5)
)
```

<!-- PENDING(#5): teardown_on_shutdown is on a separate branch; confirm its name and position. -->

The parameters added by the fixes come after `launcher`. Their order relative to each other depends
on how the fix branches are merged, so pass everything after `key_name` by keyword.

- **Released Parsl.** Passing `parsl_requirement`, `ssh_cidr` or `teardown_on_shutdown` to Parsl
  ≤ 2026.11.09 raises `TypeError: … unexpected keyword argument`. To write code that runs on both,
  check `inspect.signature(AWSProvider).parameters`, as `examples/stock_provider_smoke.py` does.
- **Side effects.** The constructor has side effects. It creates a boto3 session and checks
  credentials, then reads the state file or creates the VPC infrastructure.

## Parameters

### `image_id` (str, required)

The AMI ID for every instance. It must be in the provider's region and match the architecture of
`instance_type`.

- **Released:** the user data supports apt-based images only.
- **With the fixes:** it supports apt-based (Debian, Ubuntu) and dnf-based (Amazon Linux 2023) images.

Construction does not use it, so a placeholder is fine when you construct a provider only to create or
tear down infrastructure. See [worker-environment.md](worker-environment.md).

### `key_name` (str or None, required)

The name of an EC2 key pair in the region. Instances get it for SSH, which you need for the laptop
tunnel and for reading logs on an instance.

- **Released:** `None` is sent as `KeyName=None`, which botocore rejects, so every launch fails
  ([#9](https://github.com/scttfrdmn/aws-elastic-parsl/issues/9)).
- **With the fixes:** `None` launches without a key pair.

### `init_blocks` (int, default 1), `min_blocks` (int, default 0), `max_blocks` (int, default 10)

These are the standard Parsl elasticity settings. One block is one instance. The scaling strategy
launches `init_blocks` at start, scales between `min_blocks` and `max_blocks`, and cancels idle blocks
after `Config.max_idletime`.

The provider separately refuses launches beyond `max_blocks * nodes_per_block` instances:

- **Released:** this counts every instance launched by this provider object and not cancelled,
  including ones that terminated on their own. The check is `>`, so it allows one more than the limit
  ([#9](https://github.com/scttfrdmn/aws-elastic-parsl/issues/9)).
- **With the fixes:** it counts only non-terminal instances, using `>=`.

### `nodes_per_block` (int, default 1)

Leave this at 1. The provider always launches **one instance per block**. A larger value does not add
instances. It makes the default `SingleNodeLauncher` start `nodes_per_block` worker pools on the one
instance, and raises the capacity limit above.

### `parallelism` (float, default 1)

The standard Parsl scaling ratio of blocks to outstanding tasks, used by the `simple` strategy.

### `worker_init` (str, default `''`)

Shell commands inlined into the user data, run as root before the worker starts.

- **Released:** they run after the (failing) `pip3 install`.
- **With the fixes:** they run after Parsl is installed into `/opt/parsl-venv`, with that venv first on
  `PATH`.

The text is not templated. EC2's 16 KB user-data limit applies to the whole script. See
[worker-environment.md](worker-environment.md#worker_init).

### `instance_type` (str)

- **Default.**
  - **Released:** `'t2.small'`, a previous-generation type that some newer availability zones don't
    offer.
  - **With the fixes ([#11](https://github.com/scttfrdmn/aws-elastic-parsl/issues/11)):** `'t3.small'`,
    a current-generation burstable x86_64 type. It was checked to be offered in every region enabled
    on the test account (17 regions). An arm64 AMI needs a Graviton type, such as `'t4g.small'`.
- **Placement.** The provider looks up which zones offer the type and launches into the first subnet in
  one of them. If no zone offers it:
  - **Released:** logs an error and returns `None`.
  - **With the fixes:** raises `SubmitException`.

Pass it explicitly to get the same behaviour on both versions.

### `region` (str or None)

- **Released:** default `'us-east-2'`, always passed to boto3. It overrides `AWS_DEFAULT_REGION` and
  the profile's region.
- **With the fixes ([#11](https://github.com/scttfrdmn/aws-elastic-parsl/issues/11)):** default `None`.
  - **Resolution:** boto3 uses `AWS_DEFAULT_REGION`, then the `region` of the selected profile in
    `~/.aws/config`. botocore does **not** read `AWS_REGION`.
  - **Fallback:** if neither is set, the provider uses `us-east-2` and logs this at INFO.
  - **After construction:** `provider.region` holds the region actually used.

### `spot_max_bid` (float, default 0)

If greater than 0, each block is a one-time Spot Instance with this maximum price, in USD per hour.
The interruption behaviour is `terminate`. `0` means on-demand.

- **Released:** an empty `InstanceMarketOptions` is sent for on-demand launches.
- **With the fixes:** it is omitted.

See [lifecycle-and-cost.md](lifecycle-and-cost.md#spot-instances).

### `key_file` (str or None, default None)

Path to a JSON credentials file. `~` and `$VARS` are expanded. It takes precedence over `profile`.

- **Released:** only boto3's argument names work (`aws_access_key_id`, `aws_secret_access_key`,
  `aws_session_token`). The documented `AWSAccessKeyId`/`AWSSecretKey` raise `TypeError`
  ([#3](https://github.com/scttfrdmn/aws-elastic-parsl/issues/3)).
- **With the fixes:** `AWSAccessKeyId`, `AWSSecretKey`, optional `AWSSessionToken`, or the boto3 names.
  Any other key raises `ConfigurationError`.

### `profile` (str or None, default None)

A named AWS profile. It is used when `key_file` is not set.

- **Released:** a missing profile raises botocore's `ProfileNotFound`.
- **With the fixes:** it raises `ConfigurationError`.

When neither `key_file` nor `profile` is set:

- **Released:** the constructor requires both `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` in the
  environment ([#2](https://github.com/scttfrdmn/aws-elastic-parsl/issues/2)).
- **With the fixes:** it uses boto3's default chain, and raises `ConfigurationError` only if that finds
  nothing.

See [credentials.md](credentials.md).

### `iam_instance_profile_arn` (str, default `''`)

The ARN of an **instance profile** (`arn:aws:iam::<acct>:instance-profile/<name>`) to attach to every
instance, giving apps on the workers AWS credentials. The caller needs `iam:PassRole`.

- **Released:** `IamInstanceProfile={'Arn': ''}` is sent when it is empty. EC2 accepts this.
- **With the fixes:** it is omitted.

### `state_file` (str or None, default None, which means `'awsproviderstate.json'`)

The JSON file recording the infrastructure IDs. A relative path is relative to the **current working
directory**.

- **If the file exists,** its infrastructure is reused, and nothing is created.
- **If it doesn't,** new infrastructure is created and recorded.

How the versions differ:

- **Released:**
  - It records the VPC, security group and subnets.
  - It restores instance IDs from the file.
  - An unreadable file is treated as missing.
  - A failed creation writes no file and leaks what was created
    ([#10](https://github.com/scttfrdmn/aws-elastic-parsl/issues/10)).
- **With the fixes:**
  - It also records the internet gateway and route table, and the `infrastructureID` with which every
    resource is tagged. Teardown deletes only resources carrying that ID. A file without it, for
    example one written by released Parsl, is still reused, but teardown deletes nothing it records.
  - It never restores instances.
  - An unreadable file raises `ConfigurationError`.
  - A failed creation is rolled back.
  - It logs the absolute path.

See [lifecycle-and-cost.md](lifecycle-and-cost.md#the-state-file).

### `walltime` (str `HH:MM:SS`, default `'01:00:00'`)

- **Released:** ignored ([#8](https://github.com/scttfrdmn/aws-elastic-parsl/issues/8)).
- **With the fixes:** each instance schedules `shutdown -h +<minutes>` at boot, unless `linger`.
  The walltime is rounded up to whole minutes, with a minimum of 1. **The default now ends instances
  after one hour.**

### `linger` (bool, default False)

`True` keeps instances running after their worker exits.

- **All versions:** `cancel()` is ignored and returns `False`, so Parsl cannot stop the instances.
- **With the fixes:** additionally, there is no exit trap and no walltime shutdown, and
  `teardown_on_shutdown` is skipped with a warning. Terminate the instances yourself.

### `launcher` (Launcher, default `SingleNodeLauncher()`)

This wraps the worker command. The default sets `set -e` and ends with `exit`.

- **Released:** the shutdown code after it is unreachable
  ([#7](https://github.com/scttfrdmn/aws-elastic-parsl/issues/7)).
- **With the fixes:** it runs in a subshell. `SimpleLauncher()` also works.

### `parsl_requirement` (str or None, default None; with the fixes only)

The pip requirement for Parsl on the workers ([#6](https://github.com/scttfrdmn/aws-elastic-parsl/issues/6)).

- **Default:** `parsl==<parsl.version.VERSION>`. For a development version (one containing `dev`), the
  default is `parsl @ https://github.com/Parsl/parsl/archive/refs/heads/master.tar.gz`, with a warning.
- **Override:** set it to install a release, a fork or a wheel URL. Whatever it installs must report
  the same `parsl.version.VERSION` as the submit side.

See [worker-environment.md](worker-environment.md#with-the-fixes-pending-upstream-prs-6-7-8).

### `ssh_cidr` (str, list of str, or None, default None; with the fixes only)

The IPv4 range or ranges from which SSH (TCP 22) and ICMP to the instances are allowed
([#12](https://github.com/scttfrdmn/aws-elastic-parsl/issues/12)).

- **Accepted values.** A CIDR string such as `'203.0.113.0/24'`, or a list of them. A bare address
  such as `'203.0.113.7'` is treated as `/32`. Duplicates are dropped.
- **Rejected values.** IPv6 networks and CIDRs with host bits set (`'203.0.113.7/24'`) raise
  `ConfigurationError`.
- **Default `None`.** Nothing is open from outside the VPC except ICMP "fragmentation needed"
  (type 3 code 4) from `0.0.0.0/0`, which path-MTU discovery needs and which admits no ping. Traffic
  within `10.0.0.0/16` (TCP, UDP, ICMP) is always allowed.
- **Released:** SSH and all ICMP are always open to `0.0.0.0/0`.
- **Laptop tunnel:** set `ssh_cidr='<your public IPv4>/32'`.

`ssh_cidr` applies only when the provider creates the security group. A group reused from the state
file is **not modified**, and the constructor logs a WARNING naming it. That includes a group created
by released Parsl, which still allows SSH from anywhere. Edit a reused group's rules with the AWS CLI,
or tear down and recreate. See [networking.md](networking.md#security-group-rules).

### `teardown_on_shutdown` (bool, default False; with the fixes only)

When `True`, the executor's shutdown calls `teardown()` after Parsl has cancelled the blocks
([#5](https://github.com/scttfrdmn/aws-elastic-parsl/issues/5)). With `linger=True` it is skipped, with a
warning. The default keeps the infrastructure for reuse through the state file.

<!-- PENDING(#5): confirm the name and the hook it uses. -->

## Methods and attributes

| Name | Behaviour |
|---|---|
| `teardown()` | **Released:** always fails ([#4](https://github.com/scttfrdmn/aws-elastic-parsl/issues/4)). **With the fixes:** terminates the instances tagged with this infrastructure ID (and any this object launched), waits for them, and deletes only resources whose `parsl:infrastructure` tag matches. Returns `True` if everything was removed and the state file deleted. Returns `False` if anything was left (foreign instances, untagged resources, a state file without `infrastructureID`): the state file is kept, and a WARNING lists what was left and why. Idempotent. See [lifecycle-and-cost.md](lifecycle-and-cost.md#teardown). |
| `goodbye()` | Calls `teardown()`. Parsl itself never calls it ([#5](https://github.com/scttfrdmn/aws-elastic-parsl/issues/5)). |
| `submit(command, tasks_per_node, job_name)` | Launches one instance and returns its instance ID, which is the Parsl job ID. **Released:** returns `None` on failure. **With the fixes:** raises `SubmitException`. |
| `status(job_ids)` | **Released:** raises `KeyError` when called with no job IDs in a region with other instances ([#1](https://github.com/scttfrdmn/aws-elastic-parsl/issues/1)). **With the fixes:** describes only the given instances and returns statuses in `job_ids` order. Instances EC2 no longer reports count as completed. |
| `cancel(job_ids)` | Terminates the instances. Ignored with `linger=True`. |
| `status_polling_interval` | 60 seconds. |
| `label` | `'ec2'`. |
| `vpc_id`, `sg_id`, `sn_ids` | IDs of the VPC, security group and subnets in use. |
| `infrastructure_id` | With the fixes: the `parsl.aws.<timestamp>` ID used in the `parsl:infrastructure` tag. It is `None` for a state file without one. |
| `region` | With the fixes: the region actually used, after boto3 resolution. |
| `internet_gateway`, `route_table` | IDs. **Released:** set only on the run that created them. |
| `resources` | A dict from instance ID to `{'job_id', 'instance', 'status'}` for the instances this object launched. The tunnel helper reads it. |
| `client`, `session` | The boto3 EC2 client and session. |

## Related HighThroughputExecutor settings

| Setting | Use with `AWSProvider` |
|---|---|
| `address` | What the workers dial. `'127.0.0.1'` for the laptop tunnel, the private IP for a submit host in the provider's VPC. |
| `worker_port` | Fix it (for example 54000) when a tunnel or a security-group rule names it. |
| `encrypted` | Must be `False`. The provider does not copy the CurveZMQ certificates to the workers. |
| `address_probe_timeout` | Seconds a worker spends finding the interchange before exiting (default 30). |
| `drain_period` | Seconds after which workers stop taking tasks. Set it below `walltime`. |
| `worker_logdir_root` | Where worker logs go on the instance. |
| `max_workers_per_node`, `cores_per_worker` | Workers per instance. |
| `interchange_launch_cmd` | Full path to `interchange.py`, if the venv is not on `PATH`. |
