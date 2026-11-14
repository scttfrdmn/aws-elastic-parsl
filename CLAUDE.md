# CLAUDE.md — aws-elastic-parsl

Ephemeral AWS execution for Parsl. Sister project: ../aws-elastic-gcs.

## Phases
1. Done: fixed Parsl's stock `AWSProvider` (parsl/providers/aws/aws.py) and opened upstream PRs
   Parsl/parsl#4182–#4190 from the fork scttfrdmn/parsl. The local clone is ../parsl-upstream, with
   one branch per fix and worktrees under ../parsl-upstream-wt/. `aws-provider-integration` merges all
   the fixes; `aws-provider-fixes-2026.11.09` is that branch with VERSION set to the release.
2. Done: docs and guides for the stock provider (docs/stock-aws-provider/, upstream PR #4190).
3. Done: Globus Compute on the fixed provider (globus-compute/; HTEX with reverse tunnels).
4. **Superseded:** the ephemeral provider and Globus Compute work moved to
   ../aws-elastic-globus-compute (no Parsl). Here, only respond to reviews on the Parsl PRs.

## Conventions
- AWS: `AWS_PROFILE=aws`, account 942542972736, region us-west-2. EC2 key pair `aws-elastic-parsl`
  (private key ~/.ssh/aws-elastic-parsl.pem).
- The account has unrelated instances (e.g. aws-elastic-gcs DTNs) and about 4 free VPC slots. Never touch
  resources this project didn't create; the stock provider tags its VPC `Name=parsl.aws.<ts>`.
- Dev venv: `.venv` (Python 3.12, which Ubuntu 24.04 workers match). Put `.venv/bin` on PATH
  when running Parsl, because HTEX launches `interchange.py` from PATH.
- Upstream Parsl checks: `make flake8`, isort, mypy, `pytest --config local` on new tests.
- Version stays 0.x. Status lives in GitHub issues/milestones/project board; no local plan files.
