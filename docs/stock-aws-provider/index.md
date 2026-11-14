# Parsl's stock `AWSProvider`

This guide covers `parsl.providers.AWSProvider`, the EC2 provider that ships with Parsl. It documents
what the provider really does, both in the current Parsl release and with the fixes this project is
upstreaming. It also covers how to get a working run from a laptop or from a host in AWS, and how to
avoid leaving resources and charges behind.

## What the provider does

- **Infrastructure.** On construction, if its state file (`awsproviderstate.json` in the current
  working directory by default) does not exist, the provider creates a VPC (`10.0.0.0/16`), an internet
  gateway, a route table, one public subnet per available availability zone, and a security group. It
  records their IDs in the state file. Later runs that find the state file reuse these resources.
  Parsl never removes them. See [lifecycle-and-cost.md](lifecycle-and-cost.md).
- **Blocks.** Each block is one on-demand or spot EC2 instance, launched with a user-data script. The
  script installs Parsl, runs your `worker_init`, then starts the HighThroughputExecutor (HTEX) worker
  pool, `process_worker_pool.py`.
- **Connectivity.** The worker pool connects out to the interchange on the submit host. The submit host
  must therefore be reachable from the instances on the HTEX worker port. A laptop behind NAT is not
  reachable, so it needs a tunnel. See [networking.md](networking.md).
- **Version matching.** The interchange rejects workers whose Python `major.minor` or Parsl version
  differs from the submit side's. See [worker-environment.md](worker-environment.md).

## Which Parsl versions work

Throughout this guide:

- **Parsl ≤ 2026.11.09 (released)** means every Parsl release up to and including 2026.11.09, the
  latest at the time of writing. The provider code has not changed materially in years, so all of
  these releases behave the same way.
- **With the fixes (pending upstream PRs)** means the provider with this project's fixes applied.
  Each fix is tracked in an issue here and goes to [Parsl/parsl](https://github.com/Parsl/parsl) as a
  PR. Until the PRs merge, the combined fixes are on the `aws-provider-integration` branch of
  [scttfrdmn/parsl](https://github.com/scttfrdmn/parsl).

| Area | Parsl ≤ 2026.11.09 (released) | With the fixes | Issue |
|---|---|---|---|
| First status poll | `KeyError: 'i-…'` in any region where the account has an instance this provider did not start. No block is ever launched. This is Parsl/parsl#3764. | Status is scoped to the provider's own instances. | [#1](https://github.com/scttfrdmn/aws-elastic-parsl/issues/1) |
| Credentials | Only `profile=`, `key_file=`, or both `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are accepted. `AWS_PROFILE`, SSO and instance roles are rejected. | boto3's default credential chain. | [#2](https://github.com/scttfrdmn/aws-elastic-parsl/issues/2) |
| `key_file` | The documented keys raise `TypeError`. | The documented keys work. | [#3](https://github.com/scttfrdmn/aws-elastic-parsl/issues/3) |
| Worker install | `pip3 install numpy scipy parsl` fails on Ubuntu 24.04 (PEP 668). Where it succeeds, it installs an unpinned Parsl. | Pinned Parsl in a venv at `/opt/parsl-venv`. | [#6](https://github.com/scttfrdmn/aws-elastic-parsl/issues/6) |
| Instance shutdown | Instances never shut themselves down. They stop only when Parsl cancels the block. | The instance powers off (and terminates) when the worker exits or fails. | [#7](https://github.com/scttfrdmn/aws-elastic-parsl/issues/7) |
| `walltime` | Ignored. | Enforced on the instance. The default of 1 hour is a behaviour change. | [#8](https://github.com/scttfrdmn/aws-elastic-parsl/issues/8) |
| Launch errors | `submit()` returns `None`, and `key_name=None` fails botocore validation. | `SubmitException`. `key_name=None` launches without a key pair. | [#9](https://github.com/scttfrdmn/aws-elastic-parsl/issues/9) |
| `teardown()` | Always fails. | Deletes only resources tagged as this provider's infrastructure, returns `True`/`False`, and is idempotent. | [#4](https://github.com/scttfrdmn/aws-elastic-parsl/issues/4) |
| State file | A corrupt file silently creates a new VPC. A failed creation leaks resources. | `ConfigurationError` for a corrupt file. Failed creation is rolled back. | [#10](https://github.com/scttfrdmn/aws-elastic-parsl/issues/10) |
| Cleanup on exit | Parsl never calls `provider.goodbye()`, so the VPC is permanent. | Optional `teardown_on_shutdown=True`. | [#5](https://github.com/scttfrdmn/aws-elastic-parsl/issues/5) |
| `region` default | Always `us-east-2`, which overrides `AWS_DEFAULT_REGION` and the profile's region. | Resolved by boto3 from `AWS_DEFAULT_REGION` or the profile, with `us-east-2` only as a last resort. | [#11](https://github.com/scttfrdmn/aws-elastic-parsl/issues/11) |
| `instance_type` default | `t2.small` (previous generation). | `t3.small`. | [#11](https://github.com/scttfrdmn/aws-elastic-parsl/issues/11) |
| Security group | SSH (22/tcp) and all ICMP open to `0.0.0.0/0`. | Nothing open from outside the VPC except ICMP "fragmentation needed". SSH and ICMP only from `ssh_cidr`, if set. | [#12](https://github.com/scttfrdmn/aws-elastic-parsl/issues/12) |

<!-- PENDING(#5): teardown_on_shutdown is on a separate branch; adjust its name here, in
     reference.md and in the configs if it changes. -->

In short, the released provider cannot launch a block in a region where your account already has
other instances. Where it does launch, the worker install fails on current Ubuntu. Nothing shuts
down on its own. [released-workarounds.md](released-workarounds.md) collects the workarounds that make
the released provider usable.

### Installing the fixes before they are released

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv 'parsl[aws] @ git+https://github.com/scttfrdmn/parsl@aws-provider-integration'
```

This installs a development version of Parsl (`parsl.version.VERSION` is `1.3.0-dev`). By default, the
fixed provider then installs Parsl on the workers from the Parsl master branch on GitHub, which reports
the same version string. To make the workers run exactly the submit side's code, set
`parsl_requirement` to the same branch, for example as a tarball URL:
`'parsl @ https://github.com/scttfrdmn/parsl/archive/refs/heads/aws-provider-integration.tar.gz'`. See [worker-environment.md](worker-environment.md).

## Choosing a topology

| You run the Parsl script on | Workers reach the interchange through | Guide |
|---|---|---|
| A laptop or workstation behind NAT | An SSH reverse tunnel to each instance | [quickstart-laptop.md](quickstart-laptop.md) |
| An EC2 instance in the provider's VPC | The submit host's private IP | [quickstart-in-aws.md](quickstart-in-aws.md) |

## Checklist for any run

1. The submit side runs the same Python `major.minor` as the worker image (Python 3.12 for Ubuntu 24.04).
2. The submit-side virtual environment's `bin` directory is on `PATH`, because HTEX starts
   `interchange.py` from `PATH`.
3. HTEX has `encrypted=False`. The CurveZMQ certificates exist only on the submit host, and the provider
   does not copy them to the workers. Secure the transport some other way (SSH, or private VPC traffic).
4. The workers can reach the submit host on the HTEX worker port.
5. `image_id` belongs to the provider's region. Pass `region=` explicitly unless you have the fixes.
6. You have a plan for removing the VPC afterwards: `provider.teardown()` with the fixes, or
   `examples/cleanup_stock_provider.py`.

## Pages

- [quickstart-laptop.md](quickstart-laptop.md): run from a laptop over SSH reverse tunnels.
- [quickstart-in-aws.md](quickstart-in-aws.md): run from an EC2 submit host.
- [credentials.md](credentials.md): how credentials are found, and IAM permissions.
- [networking.md](networking.md): the VPC the provider builds, connectivity, encryption and exposure.
- [worker-environment.md](worker-environment.md): AMIs, version matching, `worker_init` and `parsl_requirement`.
- [lifecycle-and-cost.md](lifecycle-and-cost.md): walltime, linger, spot, teardown, cleanup, leaks and quotas.
- [released-workarounds.md](released-workarounds.md): making Parsl ≤ 2026.11.09 work.
- [troubleshooting.md](troubleshooting.md): symptoms, causes and fixes.
- [reference.md](reference.md): every constructor parameter.
