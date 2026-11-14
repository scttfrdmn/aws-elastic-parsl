# Networking

## What the provider builds

When no state file exists, the constructor creates the following in the session's region:

| Resource | Details |
|---|---|
| VPC | CIDR `10.0.0.0/16`, no IPv6, tagged `Name=parsl.aws.<timestamp>`. DNS hostnames are not enabled, so instances get public IPs but no public DNS names. |
| Internet gateway | Attached to the VPC. With the fixes, tagged `Name=parsl.aws.<timestamp>`; released: untagged. |
| Route table | `0.0.0.0/0` → the internet gateway, associated with every subnet. With the fixes, tagged `Name=parsl.aws.<timestamp>`; released: untagged. |
| Subnets | One per *available* availability zone, `10.0.<16·n>.0/20`, where `n` is the zone's index in the region's zone list. Public IPv4 on launch. Tagged `Name=parsl.aws.<timestamp>.<zone>`. |
| Security group | Named `private-subnet`, tagged `Name=parsl.aws.<timestamp>`. Rules are below. The VPC's default security group is tagged `Name=parsl.aws.<timestamp>.default` and is not used. |

With the fixes, every resource the provider creates (VPC, subnets, security group, internet gateway,
route table and instances) also carries `parsl:provider=AWSProvider` and
`parsl:infrastructure=<infrastructure ID>`, applied at creation time. The infrastructure ID is the VPC's
`parsl.aws.<timestamp>` name, and it is recorded in the state file as `infrastructureID`. Teardown
uses these tags to decide what it may delete. See [lifecycle-and-cost.md](lifecycle-and-cost.md#teardown).

Instances launch into the first subnet whose zone offers `instance_type`, which is always the same
subnet. The provider does not spread blocks across zones, and it does not retry in another zone when one
is out of capacity.

### Security group rules

Ingress:

| Protocol | Ports | Source | Released | With the fixes ([#12](https://github.com/scttfrdmn/aws-elastic-parsl/issues/12)) |
|---|---|---|---|---|
| TCP | all | `10.0.0.0/16` | yes | yes |
| UDP | all | `10.0.0.0/16` | yes | yes |
| ICMP | all | `10.0.0.0/16` | yes (via `0.0.0.0/0`) | yes |
| ICMP | type 3 code 4 ("fragmentation needed") | `0.0.0.0/0` | yes (via all ICMP) | yes |
| TCP | 22 | `0.0.0.0/0` | yes | no |
| ICMP | all | `0.0.0.0/0` | yes | no |
| TCP 22 and all ICMP | | each `ssh_cidr` range | no such option | only if `ssh_cidr` is set |

With the fixes, nothing else is open from outside the VPC.

- **The ICMP "fragmentation needed" rule.** This rule is for path-MTU discovery. The VPC's MTU is 9001,
  but traffic leaving through the internet gateway is limited to 1500. Without this rule, large
  packets to an off-VPC submit host could silently stall. It admits no ping (echo request) or other
  ICMP.
- **`ssh_cidr`.** It takes an IPv4 CIDR string or a list of them. A bare address such as
  `203.0.113.7` becomes `/32`. IPv6 networks and CIDRs with host bits set (`203.0.113.7/24`) raise
  `ConfigurationError`.
- **Reused security groups.** `ssh_cidr` is applied only when the provider creates the security
  group. A group reused from the state file is not modified: the constructor logs a WARNING naming
  it. That includes a group created by released Parsl, which still allows SSH and ICMP from anywhere.
  Change a reused group's rules with the AWS CLI, or tear down and recreate.

Egress: all TCP to anywhere, plus TCP and UDP within the VPC. **UDP to the internet is blocked.** The
Amazon DNS resolver (`10.0.0.2`), the Amazon Time Sync Service and instance metadata still work, but an
app that sends UDP to public servers does not.

The security group is created once and reused through the state file. Rules you add or remove with the
AWS CLI persist across runs.

## How workers connect

HTEX starts an interchange process on the submit host. Each instance runs `process_worker_pool.py`,
which connects **out** to the interchange on the HTEX worker port. Nothing connects in to the
workers. These HighThroughputExecutor settings control it:

- **`address`.** The address the workers are told to dial, and the address the interchange binds the
  worker port on.
  - If `None` (the default), the interchange binds every interface. The workers try every address
    Parsl can find for the submit host: interface addresses, the hostname, the address of the default
    route, and the public address reported by `api.ipify.org`. They use the first that answers.
  - Setting it explicitly is more predictable.
- **`worker_port`.** A fixed port. Otherwise a random one is picked from `worker_port_range`
  (default 54000–55000). Fix it whenever a tunnel or a firewall rule must name the port.
- **`address_probe_timeout`.** How long a worker tries to find a reachable address before it gives up
  (default 30 s). A worker that gives up exits. With the fixes, the instance then shuts down.
- **`encrypted`.** Must be `False`; see below.

Before using any connection, the interchange checks each worker's Python and Parsl versions. See
[worker-environment.md](worker-environment.md#version-matching).

## Encryption: `encrypted=False` is required

`encrypted=True` (the HTEX default) creates CurveZMQ certificates under the run directory on the submit
host. It launches the workers with `--cert_dir <that path>`. The provider does not copy the
certificates to the instances, so each worker fails to load them and never connects. This is true
with and without the fixes.

With `encrypted=False`, the traffic between the workers and the interchange is not encrypted or
authenticated. **The interchange and the submit process unpickle what they receive on the worker
port.** Anyone who can connect to that port can therefore run code on the submit host. Make sure only
your workers can reach it:

- **Laptop:** the interchange binds `127.0.0.1` and the traffic goes through SSH.
- **Submit host in the provider's VPC:** allow the worker port only from `10.0.0.0/16`.
- **Never** open the worker port to `0.0.0.0/0`.

## Topology 1: laptop behind NAT, SSH reverse tunnels

A laptop has no address the workers can reach. The workaround, used by
`examples/stock_provider_smoke.py` and [quickstart-laptop.md](quickstart-laptop.md):

```python
htex = HighThroughputExecutor(
    label='aws_htex',
    address='127.0.0.1',     # workers dial their own loopback
    worker_port=54000,       # the port the tunnels forward
    encrypted=False,         # certificates never reach the workers; SSH protects the traffic
    provider=provider,
)
```

For each running instance, the laptop runs:

```bash
ssh -N -i ~/.ssh/my-key.pem \
  -o ControlMaster=no -o ControlPath=none \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 \
  -R 54000:127.0.0.1:54000 ubuntu@<instance public IP>
```

The instance's `sshd` listens on its own `127.0.0.1:54000` and forwards each connection to the laptop's
`127.0.0.1:54000`, where the interchange is bound. Points to get right:

- **One tunnel per instance.** The workers on an instance can reach only their own loopback. The
  helper in [quickstart-laptop.md](quickstart-laptop.md#the-tunnel-helper) polls
  `provider.resources` and opens a tunnel for each running instance.
- **`ControlMaster=no` and `ControlPath=none`.** If `~/.ssh/config` has `ControlMaster auto` for these
  hosts, a second `ssh` becomes a client of an existing master. The forward is then owned by the master
  connection, the `ssh -N` process exits immediately, and a supervising loop keeps reopening it. The
  tunnel silently does not work.
- **SSH access.** Released: the security group allows SSH from anywhere. With the fixes, pass
  `ssh_cidr='<your public IPv4>/32'` ([#12](https://github.com/scttfrdmn/aws-elastic-parsl/issues/12)).
  Find your address with `curl https://checkip.amazonaws.com`. `ssh_cidr` only takes effect when the
  security group is created. If your address changes (VPN, roaming), the reused group still names the
  old one, and changing `ssh_cidr` does not update it. Add the new address with
  `aws ec2 authorize-security-group-ingress --group-id <sgID> --protocol tcp --port 22 --cidr <ip>/32`,
  or tear down and recreate.
- **Login user.** `ubuntu` on Ubuntu, `ec2-user` on Amazon Linux, `admin` on Debian.
- **Timing.** SSH is usually ready long before the worker install finishes. If the worker probes before
  the tunnel is up, it gives up after `address_probe_timeout`. Raise the timeout if your `worker_init`
  is short or your network is slow.
- **Bandwidth.** All task inputs and results go through SSH and leave AWS as internet egress, which is
  billed per GB.

## Topology 2: submit host in the provider's VPC

Launch the submit host into one of the provider's subnets. Then:

```python
from parsl.addresses import address_by_route

htex = HighThroughputExecutor(
    label='aws_htex',
    address=address_by_route(),   # the submit host's private IP, inside 10.0.0.0/16
    worker_port=54000,
    encrypted=False,
    provider=provider,
)
```

The submit host's security group must allow the worker port from `10.0.0.0/16`. The provider's own
security group already allows all TCP from `10.0.0.0/16`, so a submit host launched with that group
needs no extra rule. If you give the submit host its own security group instead, add the rule:

```bash
aws ec2 authorize-security-group-ingress --group-id <submit host SG> \
  --protocol tcp --port 54000 --cidr 10.0.0.0/16
```

A security group you create inside the provider's VPC is not the provider's. Delete it yourself before
teardown, or the VPC deletion fails with `DependencyViolation`.

[quickstart-in-aws.md](quickstart-in-aws.md) walks through this topology.

## Submit host in another VPC

A submit host in your default VPC, or any other VPC, has no private route to the provider's VPC. The
provider creates no peering, transit gateway or endpoint, so by default the workers can reach that
host **only through its public IP**.

- **Source addresses.** Traffic between public IPs leaves through the provider's internet gateway, so
  it arrives from the **workers' public IPs**, not from `10.0.0.0/16`. A rule allowing the worker port
  from `10.0.0.0/16` on the submit host's security group never matches this traffic.
- **Unpredictable worker IPs.** EC2 assigns the workers' public IPs at launch, so no fixed list of
  addresses can cover them. The only rule that matches them all is `0.0.0.0/0` on the worker port,
  which, with `encrypted=False`, exposes the submit host as described above. Do not do this.
- **Peering.** VPC peering gives a private path. The provider's `10.0.0.0/16` must not overlap your
  VPC's CIDR. The default VPC uses `172.31.0.0/16`, so the two are compatible. You need:
  - a peering connection;
  - a route to your VPC's CIDR in the provider's route table;
  - a route to `10.0.0.0/16` in your VPC's route table;
  - a rule on the submit host's security group allowing the worker port from `10.0.0.0/16`.

  The provider knows nothing about these. Remove the peering connection and the route in your own VPC
  before tearing down, and expect to redo them if the infrastructure is recreated.

For an ad hoc run, Topology 2 is less work. A submit host elsewhere can also use the SSH reverse
tunnels of Topology 1, exactly like a laptop.

## Outbound access from the workers

The user data installs packages from the distribution mirrors and from PyPI, so the instances need
internet access. They get it through their public IPs and the internet gateway. The provider has no
option for private subnets or a NAT gateway.
