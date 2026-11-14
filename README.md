# aws-elastic-parsl

Ephemeral AWS execution for [Parsl](https://parsl-project.org): run workflows on EC2 from a laptop with
nothing left running in your account afterwards.

Parsl's stock `AWSProvider` predates most of today's AWS and Parsl. It assumes the submit host is
directly reachable by the workers, creates a VPC it never removes, and has several defects that keep it
from working at all in a typical account. This project:

1. **Fixes the stock `AWSProvider` upstream.** Each defect gets an issue here and a PR to
   [Parsl/parsl](https://github.com/Parsl/parsl), with tests and a live EC2 check.
2. **Documents the stock provider**: setup, credentials, networking, and cleanup.
3. **Builds `AWSEphemeralProvider`**: the same constructor interface as `AWSProvider`, but every AWS
   resource is created per run and torn down on exit, and workers reach the submit host without a
   standing head node.
4. **Adds Globus Compute support** after that.

It is a sister project to [aws-elastic-gcs](https://github.com/scttfrdmn/aws-elastic-gcs).

## Status

Pre-release (0.x). Work is tracked in this repo's issues, milestones and project board.

## Running the stock provider today

`examples/stock_provider_smoke.py` runs one block of the stock provider from a laptop. Credentials come
from `AWS_PROFILE`. Worker traffic goes over an SSH reverse tunnel, so the provider itself is not patched:

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv 'parsl[aws]'
PATH=$PWD/.venv/bin:$PATH AWS_PROFILE=aws python examples/stock_provider_smoke.py
AWS_PROFILE=aws .venv/bin/python examples/cleanup_stock_provider.py --yes   # the VPC is left behind
```

The submit side must run Python 3.12 so that the workers (Ubuntu 24.04, Python 3.12) pass Parsl's version check.

## License

Apache-2.0
