# Worker environment

Each block is one instance. The provider gives the instance a user-data script, which cloud-init runs
once, as root, on first boot. The script installs Parsl, runs `worker_init`, and starts the HTEX worker
pool through the launcher. Its output goes to `/var/log/cloud-init-output.log` on the instance.

## Version matching

When a worker pool registers, the interchange compares two things with the submit side:

- **Python `major.minor`.** For example `3.12`. The patch version may differ.
- **`parsl.version.VERSION`.** The exact string, for example `2026.11.09` or `1.3.0-dev`.

If either differs, the interchange sends a `VersionMismatch` and shuts down, and every outstanding task
fails with:

```
VersionMismatch: Manager version info py.v=3.12 parsl.v=2026.11.09 does not match interchange version info py.v=3.13 parsl.v=2026.11.09
```

The executor is then in a bad state, and Parsl does not cancel its blocks at shutdown. Released
instances keep running. With the fixes, the instances power off once their worker gives up on the
dead interchange.

So the worker image's Python must match the submit side's, and the worker must install the same Parsl.

| Image | Default `python3` | Other Python versions | Login user |
|---|---|---|---|
| Ubuntu 24.04 | 3.12 | none in the Ubuntu archive | `ubuntu` |
| Ubuntu 22.04 | 3.10 | only what the image's apt sources offer | `ubuntu` |
| Debian 12 | 3.11 | none in the Debian archive | `admin` |
| Amazon Linux 2023 | 3.9 | `python3.11` and `python3.12` from dnf | `ec2-user` |

With the fixes, the provider installs `python3.X` for the submit side's `3.X` whenever the image's
package manager offers it, and otherwise uses the default `python3`. This happens silently, so a
mismatch shows up only as `VersionMismatch`. The released provider always uses the default `python3`.

The easiest choice is to match the submit side to the image: Python 3.12 on the submit side and Ubuntu 24.04 workers.

AMI IDs are per region. Look up the current ones through the public SSM parameters:

```bash
# Ubuntu 24.04, x86_64
aws ssm get-parameter --region us-west-2 --query Parameter.Value --output text \
  --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id
# Ubuntu 24.04, arm64 (Graviton instance types such as t4g, m7g)
aws ssm get-parameter --region us-west-2 --query Parameter.Value --output text \
  --name /aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id
# Amazon Linux 2023, x86_64
aws ssm get-parameter --region us-west-2 --query Parameter.Value --output text \
  --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64
```

The AMI's architecture must match `instance_type`.

## What the user data does

### With the fixes (pending upstream PRs, [#6](https://github.com/scttfrdmn/aws-elastic-parsl/issues/6), [#7](https://github.com/scttfrdmn/aws-elastic-parsl/issues/7), [#8](https://github.com/scttfrdmn/aws-elastic-parsl/issues/8))

In order:

1. **Shutdown.** Unless `linger=True`, it sets `trap 'shutdown -h now' EXIT` and schedules
   `shutdown -h +<walltime in minutes, rounded up>`. The instance is launched with
   `InstanceInitiatedShutdownBehavior=terminate`, so it terminates when the worker exits, when any
   later step fails, and at walltime, whichever comes first.
2. **Python.**
   - **apt images:** installs `python3 python3-pip python3-venv libffi-dev g++ libssl-dev`, then tries
     `python3.X` and `python3.X-venv` for the submit side's `3.X`.
   - **dnf images:** tries `python3.X python3.X-pip`, then falls back to `python3 python3-pip`.

   It uses `python3.X` if that exists after this step, and otherwise `python3`.
3. **Parsl.** Creates the venv `/opt/parsl-venv` and runs
   `/opt/parsl-venv/bin/python -m pip install <parsl_requirement>`. It then puts `/opt/parsl-venv/bin`
   first on `PATH`.
4. **`worker_init`.** Runs your commands. `pip install` here installs into the worker venv.
5. **The worker.** Runs the launcher script, which starts `process_worker_pool.py` from the venv, in a
   subshell. The `set -e` and `exit` in the launcher therefore do not affect the shutdown handling.

`parsl_requirement` defaults to:

- `parsl==<parsl.version.VERSION>` on a released submit side;
- `parsl @ https://github.com/Parsl/parsl/archive/refs/heads/master.tar.gz` on a development install
  (a version containing `dev`, such as `1.3.0-dev`). The constructor logs a warning in this case.
  Master reports the same version string, but it may not contain your exact code.

Set it explicitly to install anything pip accepts. It is passed to `pip install` as one shell-quoted
argument:

```python
# The same fork and branch as the submit side
parsl_requirement='parsl @ https://github.com/scttfrdmn/parsl/archive/refs/heads/aws-provider-integration.tar.gz'
# A specific release
parsl_requirement='parsl==2026.11.09'
# A wheel you host
parsl_requirement='parsl @ https://example.org/wheels/parsl-2026.11.9-py3-none-any.whl'
```

Whatever you install must report the same `parsl.version.VERSION` as the submit side.

### Parsl ≤ 2026.11.09 (released)

The released user data is:

```bash
cd ~
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-pip libffi-dev g++ libssl-dev
pip3 install numpy scipy parsl
$worker_init
$user_script        # the launcher, ending in `exit`
if ! $linger; then halt; fi   # never reached
```

Problems:

- **apt only.** On dnf images (Amazon Linux 2023), nothing is installed.
- **PEP 668.** On Ubuntu 24.04 and Debian 12, `pip3 install` fails with `externally-managed-environment`.
  `--break-system-packages` fails too, with `Cannot uninstall typing_extensions … installed by debian`.
  The worker then fails with `process_worker_pool.py: command not found`.
- **Unpinned.** On older images where `pip3 install` works (Ubuntu 22.04), it installs the **latest**
  Parsl, plus numpy and scipy, which nothing needs. A submit side on any other version gets
  `VersionMismatch`.
- **No shutdown.** Nothing shuts the instance down. See
  [lifecycle-and-cost.md](lifecycle-and-cost.md#instance-shutdown).

The workaround builds the venv yourself in `worker_init`. `worker_init` is inlined in the same shell,
before the worker command, so a `PATH` export carries over:

```python
worker_init = '\n'.join([
    "trap 'shutdown -h now' EXIT",      # shut down when the worker exits (released #7)
    'shutdown -h +60',                  # and after 60 minutes regardless (released #8)
    'apt-get install -y python3-venv',
    'python3 -m venv /opt/parsl-venv',
    "/opt/parsl-venv/bin/pip install 'parsl==2026.11.09'",
    'export PATH="/opt/parsl-venv/bin:$PATH"',
])
```

`released_worker_init()` in [released-workarounds.md](released-workarounds.md) builds this string with the
submit side's version. For Amazon Linux 2023 on the released provider, replace the `apt-get` line with
`dnf install -y python3.12 python3.12-pip` and use `python3.12 -m venv`.

## `worker_init`

`worker_init` is a string of shell commands, run as root from `/root`.

- **Order and environment.**
  - **Fixed provider:** runs after Parsl is installed, with `/opt/parsl-venv/bin` first on `PATH`.
  - **Released provider:** runs after the failing `pip3 install`.

  In both, its environment changes (`export`, `cd`) carry over to the worker.
- **Templating.** It is substituted into the script verbatim. `$VAR` in `worker_init` reaches the
  shell unchanged, and you do not need to escape `$`.
- **Failure.** `worker_init` is not run in a subshell.
  - **Fixed provider:** a failing command does not stop the script by itself; the worker is started
    anyway. If the worker then fails, the instance shuts down. An `exit` in `worker_init` ends the
    script, and the instance shuts down without starting a worker. A `set -e` applies to the rest of
    the script.
  - **Released provider:** the instance keeps running after any failure unless you added the trap.
- **Size.** EC2 limits user data to 16 KB before base64 encoding, and the provider's own script and
  the launcher count towards that. For anything large, download a script in `worker_init`
  (for example from S3, using `iam_instance_profile_arn`) and run it.
- **Secrets.** User data is readable from instance metadata, so don't put secrets in it.

Examples (fixed provider):

```python
worker_init = 'pip install numpy pandas'
```

```python
worker_init = '\n'.join([
    'apt-get install -y rsync',
    'pip install numpy scipy',
    'export OMP_NUM_THREADS=1',
])
```

## Custom AMIs

Baking dependencies into an AMI saves boot time. With the fixed provider, the user data still runs
`apt-get update`/`install` and creates `/opt/parsl-venv`. If the AMI already contains that venv with
the right Parsl, `python3 -m venv` keeps the installed packages, and `pip install` finds the requirement
already satisfied. With the released provider, put the environment anywhere and export its `bin` in
`worker_init`.

The root volume size is the AMI's default (8 GB for Ubuntu). The provider has no block-device option,
so build an AMI with a larger root volume if you need the space.

## Where the worker logs are

- **cloud-init.** `/var/log/cloud-init-output.log` has the output of the whole user-data script:
  package installs, `worker_init`, and the worker's stdout and stderr.
- **Worker pool.** The worker pool writes `manager.log` and `worker_<n>.log` under
  `<logdir>/block-<n>/<id>/`. Here `<logdir>` is the submit side's run directory path, for example
  `/Users/you/project/runinfo/000/aws_htex`, created on the instance. To put the logs somewhere
  predictable, set `worker_logdir_root='/var/log/parsl'` on the HighThroughputExecutor.

See [troubleshooting.md](troubleshooting.md#reading-logs-on-an-instance) for how to read them.
