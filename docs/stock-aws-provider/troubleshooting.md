# Troubleshooting

Look in the submit side's `runinfo/<NNN>/parsl.log` first. Then read the instance's
`/var/log/cloud-init-output.log` (see [below](#reading-logs-on-an-instance)). "Released" means
Parsl ≤ 2026.11.09. "Fixed" means with the fixes (pending upstream PRs).

## Quick index

| Symptom | Go to |
|---|---|
| `KeyError: 'i-…'` every few seconds, no instance launched | [KeyError](#keyerror-on-the-first-poll-no-instance-launched) |
| `ConfigurationError` about credentials, `TypeError … 'AWSAccessKeyId'`, `ProfileNotFound` | [Credentials](#credential-errors) |
| `FileNotFoundError: … 'interchange.py'` | [interchange.py](#interchangepy-not-found) |
| Instance running, but tasks never start | [Worker never connects](#the-worker-never-connects) |
| `process_worker_pool.py: command not found` in cloud-init log | [command not found](#process_worker_poolpy-command-not-found) |
| `VersionMismatch` | [VersionMismatch](#versionmismatch) |
| `ScalingFailed`, `SubmitException`, EC2 error codes | [Launch failures](#launch-failures) |
| Instances still running after the script ended | [Instances still running](#instances-still-running) |
| Instances end after an hour | [Walltime](#instances-end-after-an-hour) |
| A new VPC every run, or `VpcLimitExceeded` | [Extra VPCs](#a-new-vpc-every-run-or-vpclimitexceeded) |
| `InvalidSubnetID.NotFound` or `InvalidGroup.NotFound` | [Stale state file](#invalidsubnetidnotfound-or-invalidgroupnotfound) |
| `teardown()` returns `False`, state file kept | [teardown False](#teardown-returns-false) |
| `DependencyViolation` on teardown or cleanup | [DependencyViolation](#dependencyviolation-on-teardown) |
| The tunnel opens and closes over and over | [Tunnel](#a-tunnel-keeps-reconnecting) |

## KeyError on the first poll, no instance launched

```
KeyError: 'i-0b3f9e64a85567a66'
```

This repeats on every strategy pass (every 5 s) in `parsl.log`, and no instance is ever launched.

**Cause.** This happens on released Parsl (Parsl/parsl#3764,
[#1](https://github.com/scttfrdmn/aws-elastic-parsl/issues/1)). Before the first block exists, `status([])` calls
`describe_instances(InstanceIds=[])`, which returns every instance in the region. It then raises on the
first one it did not start. The instance in the message is not one of yours: it may be any instance in
the region, a submit host, or an instance from an earlier run.

**Fix.** Use the fixed provider. On released Parsl, use `ScopedStatusAWSProvider` from
[released-workarounds.md](released-workarounds.md).

## Credential errors

| Message | Version | Cause and fix |
|---|---|---|
| `ConfigurationError: Must specify either profile', 'key_file', or 'AWS_ACCESS_KEY_ID' and 'AWS_SECRET_ACCESS_KEY' environment variables.` | released | `AWS_PROFILE`, `[default]` and instance roles are not accepted ([#2](https://github.com/scttfrdmn/aws-elastic-parsl/issues/2)). Pass `profile=`. See [credentials.md](credentials.md). |
| `TypeError: Session.__init__() got an unexpected keyword argument 'AWSAccessKeyId'` | released | `key_file` with the documented keys ([#3](https://github.com/scttfrdmn/aws-elastic-parsl/issues/3)). Use the boto3 key names. |
| `botocore.exceptions.ProfileNotFound` | released | Misspelled `profile=`, or the profile is missing on this host. |
| `ConfigurationError: AWS profile '…' not found` | fixed | As above. |
| `ConfigurationError: No AWS credentials found. …` | fixed | Nothing in boto3's chain. Check `aws sts get-caller-identity` in the same shell. |
| `ExpiredToken`, `UnauthorizedSSOTokenError` mid-run | both | Run `aws sso login`, or refresh your session credentials, and restart. |

## interchange.py not found

```
FileNotFoundError: [Errno 2] No such file or directory: 'interchange.py'
```

**Cause.** HTEX starts the interchange as the command `interchange.py`, found through `PATH`. Running
`.venv/bin/python script.py` without activating the venv leaves `.venv/bin` off `PATH`. If a different
Parsl's `interchange.py` is on `PATH`, it may start instead and behave unpredictably.

**Fix.** `source .venv/bin/activate`, or `PATH=$PWD/.venv/bin:$PATH python script.py`. Alternatively,
pass the full path:
`HighThroughputExecutor(interchange_launch_cmd=[os.path.join(sys.prefix, 'bin', 'interchange.py')], ...)`.

## The worker never connects

The instance is `running`, but no tasks run, and after a while the block may be replaced. Read
`/var/log/cloud-init-output.log`, then work through this list.

1. **Install failed.** Look for `pip` errors or `process_worker_pool.py: command not found`. See
   [below](#process_worker_poolpy-command-not-found).
2. **Wrong address.** The worker command is in the instance's user data, which you can read without
   logging in:

   ```bash
   aws ec2 describe-instance-attribute --region us-west-2 --instance-id i-0123456789abcdef0 \
     --attribute userData --query UserData.Value --output text | base64 --decode | grep process_worker_pool
   # ... process_worker_pool.py ... -a 127.0.0.1 ... --port=54000 ...
   ```

   The worker must be able to reach that address and port from the instance:
   - **Laptop:** `127.0.0.1` through a working tunnel.
   - **Submit host in the provider's VPC:** its private `10.0.x.y` address.
   - **Submit host in another VPC:** this is not reachable through private addresses. See
     [networking.md](networking.md#submit-host-in-another-vpc).
3. **Port not allowed.** The submit host's security group must allow the worker port from
   `10.0.0.0/16`. A host-level firewall (`ufw`, macOS firewall) can also block it.
4. **Probe timed out.** The log shows a `ConnectionError` from the address probe. The worker found no
   reachable address within `address_probe_timeout` (30 s) and exited. With the fixes, the instance
   then shuts down. On a laptop, this usually means the tunnel was not up yet, or not working. See
   [Tunnel](#a-tunnel-keeps-reconnecting).
5. **`encrypted=True`.** The worker fails to load its certificates (the log names a missing
   `.../certificates` path from the submit host). Set `encrypted=False`. See
   [networking.md](networking.md#encryption-encryptedfalse-is-required).
6. **Version mismatch.** The worker connects, but the run fails with `VersionMismatch`. See
   [below](#versionmismatch).

## `process_worker_pool.py: command not found`

**Released ([#6](https://github.com/scttfrdmn/aws-elastic-parsl/issues/6)).** The bare `pip3 install` failed:

- **Ubuntu 24.04, Debian 12:** fails with `error: externally-managed-environment` (PEP 668).
- **dnf images:** the `apt-get` lines fail and nothing is installed.

Use `released_worker_init()` from [released-workarounds.md](released-workarounds.md). Adding
`--break-system-packages` does not work either: it fails with `Cannot uninstall typing_extensions …`.

**Fixed.** The venv install failed. Look just above the error in the log:

- **No network:** the instance needs its public IP and the internet gateway route.
- **Bad `parsl_requirement`:** for example, a version that is not on PyPI. A development submit side
  defaults to the GitHub master tarball, so GitHub must be reachable.
- **No suitable Python:** the image has no `python3` with `venv`.

## `VersionMismatch`

```
parsl.executors.high_throughput.errors.VersionMismatch: Manager version info py.v=3.12 parsl.v=2026.11.09 does not match interchange version info py.v=3.13 parsl.v=2026.11.09
```

`Manager` is the worker; `interchange` is the submit side. Every outstanding task fails, and the
executor stops.

| Differs | Fix |
|---|---|
| `py.v` | Run the submit side on the image's Python: 3.12 for Ubuntu 24.04. With the fixes, an Amazon Linux 2023 image installs `python3.11`/`python3.12` to match. Released Parsl always uses the image's default `python3`. See [worker-environment.md](worker-environment.md#version-matching). |
| `parsl.v` | **Released:** the bare `pip3 install parsl` installed the latest release; pin it with `released_worker_init()`. **Fixed:** check `parsl_requirement`. A development submit side (`1.3.0-dev`) and Parsl master both report `1.3.0-dev`; a release reports its date. |

After a `VersionMismatch`, the executor is in a bad state, and Parsl skips scaling it in at
shutdown. It therefore never cancels the executor's blocks.

- **Released:** these instances run until you terminate them. Check for running instances (see
  [below](#instances-still-running)).
- **With the fixes:** each instance still ends itself without Parsl. The interchange has shut down,
  so the worker exits after `heartbeat_threshold` (120 s by default), and the EXIT trap powers the
  instance off. Failing that, the walltime shutdown does. This applies unless `linger=True`.

## Launch failures

- **With the fixes ([#9](https://github.com/scttfrdmn/aws-elastic-parsl/issues/9)):** a failed launch
  raises `SubmitException`, with the EC2 error in the message.
- **Released:** `submit()` logs the error and returns `None`, so Parsl reports only
  `ScalingFailed: Attempt to provision nodes did not return a job ID`. Search `parsl.log` for the EC2
  error just before it.

| Error | Cause |
|---|---|
| `InvalidAMIID.NotFound` / `InvalidAMIID.Malformed` | The AMI is from another region. On released Parsl, `region` defaults to `us-east-2` ([#11](https://github.com/scttfrdmn/aws-elastic-parsl/issues/11)). With the fixes and no `region=`, check `provider.region`: boto3 ignores `AWS_REGION` and reads only `AWS_DEFAULT_REGION` or the profile, so the provider may have fallen back to `us-east-2`. |
| `InvalidKeyPair.NotFound` | `key_name` does not exist in this region. |
| `ParamValidationError … KeyName … None` | Released provider with `key_name=None` ([#9](https://github.com/scttfrdmn/aws-elastic-parsl/issues/9)). Pass a key pair name. |
| `No subnet in region … supports instance type …` | No availability zone in the region offers `instance_type`. Released Parsl logs this and returns `None`. |
| `Unsupported` / `InsufficientInstanceCapacity` | The chosen zone can't run the type right now. The provider always uses the first subnet whose zone offers the type. Try another type. |
| `VcpuLimitExceeded`, `MaxSpotInstanceCountExceeded` | Account quota. See [lifecycle-and-cost.md](lifecycle-and-cost.md#quotas). |
| `SpotMaxPriceTooLow` | `spot_max_bid` is below the current Spot price. |
| `Invalid IAM Instance Profile ARN` | Pass the *instance profile* ARN, not the role ARN, and allow `iam:PassRole`. |
| `UnauthorizedOperation` | Missing IAM permission. See [credentials.md](credentials.md#iam-permissions). |
| Released: capacity reached after a few replacement blocks | The released capacity check counts instances that terminated on their own. Set `provider.max_nodes` high ([released-workarounds.md](released-workarounds.md)). |

## Instances still running

First list them:

```bash
aws ec2 describe-instances --region us-west-2 \
  --filters 'Name=tag:Name,Values=parsl.aws.*' 'Name=instance-state-name,Values=pending,running' \
  --query 'Reservations[].Instances[].[InstanceId,LaunchTime,PublicIpAddress,VpcId]' --output text
```

Causes:

- **Released ([#7](https://github.com/scttfrdmn/aws-elastic-parsl/issues/7)).** Instances never shut
  themselves down. Whenever Parsl does not cancel a block, the instance runs until you terminate it. That
  happens if the submit process was killed, the executor hit a bad state (`VersionMismatch`, failed
  blocks), or the worker failed to install. Use the trap in `released_worker_init()`.
- **`linger=True`.** Cancel is ignored in all versions.
- **Fixed, still running.** The worker is still connected and running, or still installing, or
  `walltime` has not yet expired. An instance whose worker exits powers off within a minute or two.

Terminate them with `aws ec2 terminate-instances --instance-ids …`, or with `provider.teardown()` (fixed)
or the cleanup script, which also remove the VPC.

## Instances end after an hour

With the fixes, `walltime` is enforced, and the default is `01:00:00`
([#8](https://github.com/scttfrdmn/aws-elastic-parsl/issues/8)). This is a behaviour change from released
Parsl, where `walltime` was ignored. Set `walltime` and `drain_period`; see
[lifecycle-and-cost.md](lifecycle-and-cost.md#walltime).

## A new VPC every run, or `VpcLimitExceeded`

Causes:

- **Location.** The state file is relative to the current directory, so a run from elsewhere creates
  another VPC.
- **Released, unreadable state file.** A state file that couldn't be parsed was ignored
  ([#10](https://github.com/scttfrdmn/aws-elastic-parsl/issues/10)).
- **Deleted state file.** Someone deleted the state file without tearing down.

Use an absolute `state_file`. Remove old VPCs with the cleanup script, after checking its dry run.
The default quota is 5 VPCs per region, including the default VPC.

With the fixes, a corrupt state file raises
`ConfigurationError: Could not load AWS provider state file …` instead. Fix the file, or tear down the
resources it lists by hand, then remove it.

## `InvalidSubnetID.NotFound` or `InvalidGroup.NotFound`

The state file names resources that no longer exist, for example because the cleanup script removed the
VPC from another directory, or because the file belongs to another region or account. Remove the stale
state file. The next run creates new infrastructure.

## `teardown()` returns `False`

With the fixes, `teardown()` returns `False` when it left something in place. Nothing is raised. The
state file is kept, and a WARNING in `parsl.log` (or your logging output) lists what was left and why:

| WARNING says | Cause | Fix |
|---|---|---|
| `contains instances which are not tagged parsl:infrastructure=…` | An instance the provider did not launch is in the VPC, for example a submit host. It is left running and blocks the deletion of the VPC. | Terminate it if you no longer need it, then run `teardown()` again. |
| `the state file has no infrastructure ID` | The state file was written by released Parsl, an earlier version of the fixes, or by hand. Nothing is deleted except instances this provider object launched in this session. | Check, then use `examples/cleanup_stock_provider.py --include-untagged`, or delete the listed resources by hand. Then remove the state file. |
| `the VPC is not tagged parsl:infrastructure=…` | The state file points at a VPC this provider did not create, or one with another infrastructure ID. | Make sure you have the right state file. Remove that VPC deliberately, if at all. |
| `these resources … are not tagged … left in place` | Untagged resources, or resources with another ID, are recorded with the VPC. | Delete them by hand if they are yours, then run `teardown()` again. |

## `DependencyViolation` on teardown

| Where | Cause | Fix |
|---|---|---|
| Released `teardown()` | It deletes the internet gateway before detaching it, and subnets before the instances are gone ([#4](https://github.com/scttfrdmn/aws-elastic-parsl/issues/4)). | Use the cleanup script. |
| Fixed `teardown()`, deleting the subnets, security group or VPC | A non-instance resource you created in the VPC still holds a network interface or depends on the VPC: a VPC endpoint, a load balancer, a NAT gateway, an ENI, or a security group you created. Teardown finds instances it does not own before deleting anything, and stops with `False`. These other resources are not instances, so EC2 reports `DependencyViolation` and teardown raises it. The state file is kept. | Delete those resources yourself, then run `teardown()` again. |
| `DeleteSecurityGroup` | Another security group (for example on a peered VPC) references this group in a rule. | Remove the referencing rule. |
| Detaching the gateway: `… has some mapped public address(es)` | Instances with public IPs are still running or terminating. | Wait for termination, then retry. |
| Cleanup script | A non-instance resource of yours in the VPC, as above. Instances, subnets and security groups it can't attribute make it skip the VPC instead (`NOT OURS` in the dry run). | As above. |

To see what is left in a VPC:

```bash
VPC=vpc-0123456789abcdef0
aws ec2 describe-network-interfaces --region us-west-2 --filters Name=vpc-id,Values=$VPC \
  --query 'NetworkInterfaces[].[NetworkInterfaceId,InterfaceType,Status,Description]' --output text
aws ec2 describe-security-groups --region us-west-2 --filters Name=vpc-id,Values=$VPC \
  --query 'SecurityGroups[].[GroupId,GroupName]' --output text
```

## A tunnel keeps reconnecting

The laptop log shows `opening reverse tunnel to i-…` every few seconds, and workers never connect.

- **`ControlMaster` in `~/.ssh/config`.** With `ControlMaster auto` (often under `Host *`), the tunnel's
  `ssh` hands the session and the forward to a master connection and exits at once. The supervising
  loop sees the exit and reopens it, over and over. The forward does not work. Pass
  `-o ControlMaster=no -o ControlPath=none`, as the helper in
  [quickstart-laptop.md](quickstart-laptop.md#the-tunnel-helper) does.
- **`ExitOnForwardFailure`: remote port already bound.** A previous tunnel to the same instance is
  still holding the port on the instance. It clears when the old `sshd` session times out.
- **SSH not allowed.** With the fixes, SSH is allowed only from `ssh_cidr`
  ([#12](https://github.com/scttfrdmn/aws-elastic-parsl/issues/12)). Check that it covers your current
  public IPv4 address (`curl https://checkip.amazonaws.com`). Note that `ssh_cidr` applies only to a
  newly created security group. If the constructor logged
  `Re-using security group … its ingress rules are not modified`, add your address to that group with
  `aws ec2 authorize-security-group-ingress`.
- **Wrong key or user.** Use the private key of `key_name`, and the image's login user (`ubuntu`,
  `ec2-user`, `admin`).
- **Not ready yet.** SSH refuses connections for the first 20–60 s after `running`. A few retries are
  normal.

To test one tunnel by hand:

```bash
ssh -v -N -o ControlMaster=no -o ControlPath=none -o ExitOnForwardFailure=yes \
  -i ~/.ssh/my-key.pem -R 54000:127.0.0.1:54000 ubuntu@<public ip>
# in another terminal:
ssh -i ~/.ssh/my-key.pem ubuntu@<public ip> 'nc -vz 127.0.0.1 54000'
```

## Reading logs on an instance

Find the instance's public IP:

```bash
aws ec2 describe-instances --region us-west-2 --filters 'Name=tag:Name,Values=parsl.aws.*' \
  --query 'Reservations[].Instances[].[InstanceId,State.Name,PublicIpAddress]' --output text
```

Read the user-data output and the worker logs over SSH. This needs `key_name`, and with the fixes an
`ssh_cidr` that covers you:

```bash
IP=<public ip>
ssh -i ~/.ssh/my-key.pem ubuntu@$IP 'cloud-init status --long'
ssh -i ~/.ssh/my-key.pem ubuntu@$IP 'sudo tail -n 200 /var/log/cloud-init-output.log'
ssh -i ~/.ssh/my-key.pem ubuntu@$IP 'sudo find / -xdev -name manager.log 2>/dev/null'
ssh -i ~/.ssh/my-key.pem ubuntu@$IP 'sudo tail -n 100 $(sudo find / -xdev -name manager.log 2>/dev/null | head -1)'
```

From a submit host in the provider's VPC, use the instance's private IP instead.

With the fixes, an instance whose worker fails powers off within a couple of minutes, often before you
can log in. You have two options:

- **Console output.** Read it while the instance still exists. It includes the cloud-init output, and
  needs no SSH:

  ```bash
  aws ec2 get-console-output --region us-west-2 --instance-id i-0123456789abcdef0 --latest --output text
  ```

- **`linger=True`.** Debug with `linger=True`, so that the instance stays up. Terminate it yourself
  afterwards.

## Finding leaked resources

The provider names its VPC, subnets, security groups and instances `parsl.aws.<timestamp>…`. With the
fixes, it also names the internet gateway and route table that way. Released Parsl leaves those two
untagged; find them through the VPC.

In one region:

```bash
R=us-west-2
aws ec2 describe-vpcs --region $R --filters 'Name=tag:Name,Values=parsl.aws.*' \
  --query 'Vpcs[].[VpcId,Tags[?Key==`Name`]|[0].Value]' --output text
aws ec2 describe-instances --region $R --filters 'Name=tag:Name,Values=parsl.aws.*' \
  'Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down' \
  --query 'Reservations[].Instances[].[InstanceId,State.Name,LaunchTime,VpcId]' --output text
aws ec2 describe-internet-gateways --region $R --filters Name=attachment.vpc-id,Values=<vpc-id> \
  --query 'InternetGateways[].InternetGatewayId' --output text
# Detached gateways (from interrupted teardowns). These may also be someone else's; check before deleting.
aws ec2 describe-internet-gateways --region $R \
  --query 'InternetGateways[?length(Attachments)==`0`].InternetGatewayId' --output text
```

In every region (the released default region is `us-east-2`, wherever you meant to run):

```bash
for R in $(aws ec2 describe-regions --query 'Regions[].RegionName' --output text); do
  aws ec2 describe-vpcs --region $R --filters 'Name=tag:Name,Values=parsl.aws.*' \
    --query 'Vpcs[].VpcId' --output text | sed "s/^/$R /"
done
```

With the fixes, everything the provider creates also carries `parsl:provider=AWSProvider` and
`parsl:infrastructure=<infrastructure ID>`, including the internet gateway and route table. The Resource
Groups Tagging API can list them all at once. It needs `tag:GetResources`:

```bash
aws resourcegroupstaggingapi get-resources --region us-west-2 \
  --tag-filters Key=parsl:provider,Values=AWSProvider \
  --query 'ResourceTagMappingList[].[ResourceARN,Tags[?Key==`parsl:infrastructure`]|[0].Value]' --output text
# EC2 instances only, by infrastructure ID:
aws ec2 describe-instances --region us-west-2 \
  --filters 'Name=tag:parsl:infrastructure,Values=parsl.aws.1763000000.123' \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text
```

Remove what you find with `examples/cleanup_stock_provider.py` (dry run first), or by hand in the
order given in [lifecycle-and-cost.md](lifecycle-and-cost.md#cleaning-up).
