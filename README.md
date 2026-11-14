# aws-elastic-parsl

Ephemeral AWS execution for [Parsl](https://parsl-project.org): run workflows on EC2 from a laptop with
nothing left running in your account afterwards.

Parsl's stock `AWSProvider` predates most of today's AWS and Parsl. It assumes the workers can reach
the submit host directly, creates a VPC it never removes, and has several defects that stop it working
at all in a typical account. This project:

1. **Fixed the stock `AWSProvider` upstream.** Each defect has an issue here and a PR to
   [Parsl/parsl](https://github.com/Parsl/parsl) (#4182–#4190), with tests and a live EC2 check.
2. **Documented the stock provider**: setup, credentials, networking, and cleanup.
3. **Ran Globus Compute on it.** [`globus-compute/`](globus-compute/README.md) runs an HTEX-based
   endpoint from a laptop, with workers on EC2.

**Scope change (2026-11-14):** the goal is Globus Compute, not Parsl, and HTEX's model (workers
connecting in to the submit host) isn't cloud friendly. The ephemeral-provider plans are superseded by
[aws-elastic-globus-compute](https://github.com/scttfrdmn/aws-elastic-globus-compute), a Globus Compute
engine that runs tasks on ephemeral AWS capacity without Parsl. This repo stays as the home of the
Parsl fixes, their docs, and the HTEX reference setup.

It is a sister project to [aws-elastic-gcs](https://github.com/scttfrdmn/aws-elastic-gcs).

## Status

Pre-release (0.x), maintenance only: responding to reviews of the upstream Parsl PRs. Tracked in this
repo's issues.

## Running the stock provider today

`examples/stock_provider_smoke.py` runs one block of the stock provider from a laptop. Credentials come
from `AWS_PROFILE`. Worker traffic goes over an SSH reverse tunnel, so the provider itself is not
patched. The script detects which provider version is installed.

With the fixes, from the `aws-provider-integration` branch of
[scttfrdmn/parsl](https://github.com/scttfrdmn/parsl), which merges all the fix branches until they
are released upstream:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv 'parsl[aws] @ git+https://github.com/scttfrdmn/parsl@aws-provider-integration'
PATH=$PWD/.venv/bin:$PATH AWS_PROFILE=aws python examples/stock_provider_smoke.py --teardown
AWS_PROFILE=aws .venv/bin/python examples/cleanup_stock_provider.py   # dry run: should find nothing
```

With released Parsl (`uv pip install --python .venv 'parsl[aws]'`), the smoke test fails in any region
where the account has other instances, with `KeyError: 'i-…'`
([#1](https://github.com/scttfrdmn/aws-elastic-parsl/issues/1)). Use the workarounds in
[docs/stock-aws-provider/released-workarounds.md](docs/stock-aws-provider/released-workarounds.md),
and clean up with `cleanup_stock_provider.py --yes --include-untagged`.

The submit side must run Python 3.12 so that the workers (Ubuntu 24.04, Python 3.12) pass Parsl's version check.

## Globus Compute

[globus-compute/](globus-compute/README.md) runs a Globus Compute endpoint from a laptop, with the
fixed stock `AWSProvider` as its engine's provider. Workers are created on demand, and the VPC is
removed when the endpoint goes idle.

## Documentation

[docs/stock-aws-provider/](docs/stock-aws-provider/index.md) is a guide to Parsl's stock `AWSProvider`.
It covers released Parsl (≤ 2026.11.09) and the pending fixes: quickstarts for a laptop and for a
submit host in AWS, credentials, networking, worker environments, cost and cleanup, troubleshooting,
and a parameter reference.

## License

Apache-2.0
