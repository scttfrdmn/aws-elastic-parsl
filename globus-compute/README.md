# Globus Compute on Parsl's stock AWSProvider

Run a [Globus Compute](https://www.globus.org/compute) endpoint from a laptop. Its
`GlobusComputeEngine` launches EC2 workers through Parsl's `AWSProvider` with the fixes from this
project. The workers exist only while there is work, and the endpoint's VPC is removed when the
endpoint goes idle.

```
 laptop                                                       AWS (one VPC, created on demand)
 ┌───────────────────────────────────────┐                    ┌──────────────────────────────┐
 │ docker: aws-elastic-parsl-gce         │  ssh -R 54000      │ EC2 worker (Ubuntu 24.04)    │
 │  globus-compute-endpoint (manager)    │ ◀───────────────── │  /opt/parsl-venv:            │
 │   └ user endpoint: GlobusComputeEngine│  workers dial      │   parsl==2026.11.9           │
 │       └ HTEX interchange 127.0.0.1    │  127.0.0.1:54000   │   globus-compute-endpoint    │
 │       └ AWSProvider (fixed)  ─────────┼── EC2 API ───────▶ │  process_worker_pool         │
 │  aws_elastic_parsl.tunnel             │                    └──────────────────────────────┘
 └───────────────┬───────────────────────┘
                 │ AMQPS :5671
                 ▼
        Globus Compute service  ◀── SDK: Executor(endpoint_id=...).submit(f)
```

## Requirements

- Docker. Globus Compute endpoints need Linux and refuse to configure on macOS, so the endpoint runs
  in a container.
- An AWS profile that can manage EC2 and VPCs, and an EC2 key pair. The defaults are
  `aws-elastic-parsl` and `~/.ssh/aws-elastic-parsl.pem`.
- A Globus account.

## Use

```bash
cd globus-compute
export AWS_PROFILE=aws
./gce.sh build        # image: globus-compute-endpoint 4.21.0 + the fixed Parsl
./gce.sh login        # once; interactive Globus login
./gce.sh configure    # writes the endpoint config (region, key, ssh_cidr=<your IP>/32)
./gce.sh start        # prints the endpoint ID
./gce.sh test         # submits a few functions and prints the results
./gce.sh stop
```

From any machine with the SDK:

```python
from globus_compute_sdk import Executor
with Executor(endpoint_id="<id from ./gce.sh start>", amqp_port=5671) as ex:
    print(ex.submit(sum, [1, 2, 3]).result())
```

## What happens

1. **First task.** The endpoint starts a per-user endpoint process, which constructs `AWSProvider`.
   That creates a VPC whose resources are all tagged `parsl:provider=AWSProvider` and
   `parsl:infrastructure=<id>`, and records it in the state file.
2. **Worker launch.** `GlobusComputeEngine` scales out one block: an EC2 instance from the latest
   Ubuntu 24.04 AMI, resolved through SSM at launch. The instance installs `parsl==2026.11.9` and
   `globus-compute-endpoint==4.21.0` into `/opt/parsl-venv` and starts the worker pool.
3. **Tunnel.** `aws_elastic_parsl.tunnel` follows the state file and opens an `ssh -R` to each
   instance carrying that infrastructure's tag. The security group allows SSH only from your IP
   (`ssh_cidr`).
4. **Idle worker.** An idle block is scaled in after about 2 minutes, and the instance terminates.
   The instance also shuts itself down if the worker exits or `walltime` (1 h) expires.
5. **Idle endpoint.** After about 5 idle minutes (`idle_heartbeats_soft: 10`) the user endpoint shuts
   down. With `teardown_on_shutdown: true` the provider then deletes its VPC, and nothing is left in
   AWS. The next task starts again at step 1, which takes about 2–3 minutes to the first result.

## Why these settings

- **Parsl from `scttfrdmn/parsl@aws-provider-fixes-2026.11.09`.** This is Parsl 2026.11.09 plus the
  AWSProvider fixes (Parsl/parsl#4182–#4189), with `VERSION='2026.11.09'`. That satisfies
  `globus-compute-endpoint`'s `parsl>=2026.7.27` and matches the stock wheel the workers install.
  Only the endpoint side needs the fixes.
- **`address: 127.0.0.1`, `worker_port: 54000`, `encrypted: false`.** The endpoint is behind NAT, so
  workers reach it through the reverse tunnel. The transport is SSH. CurveZMQ certificates would
  exist only inside the container.
- **`amqp_port: 5671`.** On port 443 the endpoint's AMQPS connections were cut client-side every
  ~90 s (`SSLEOFError`). The endpoint reconnected faster than the broker noticed the old connection
  had gone, got `403 ACCESS_REFUSED … in exclusive use` for its task queue, and the task subscriber
  gave up after 3 attempts in 3 s. The endpoint stayed up but never received another task. On 5671
  there were no drops. Use `amqp_port=5671` in the SDK too. Reported upstream as
  [globus/globus-compute#2232](https://github.com/globus/globus-compute/issues/2232).
- **Python 3.12 everywhere.** HTEX requires the same Python minor version on the endpoint and the
  workers, and Ubuntu 24.04 ships 3.12. The SDK warns about micro-version differences; that's
  harmless.
- **Non-root container user.** A root endpoint is a multi-user endpoint.

## Troubleshooting

- **Logs.** The user endpoint's log is in the container at
  `~/.globus_compute/uep.<endpoint>.<user>/endpoint.log`. The tunnel and the manager log to
  `./gce.sh logs`.
- **Tasks are never picked up.** Look for `Unable to sustain channel` in the user endpoint log (see
  `amqp_port` above). Restart with `./gce.sh stop && ./gce.sh start`.
- **The worker never connects.** The tunnel logs `opening reverse tunnel` while sshd boots. If it
  keeps retrying, check that your public IP still matches `ssh_cidr`, then rerun `./gce.sh configure`.
- **Leftover resources.** These can remain after a crash. Run
  `python examples/cleanup_stock_provider.py` (dry run), then with `--yes`.
