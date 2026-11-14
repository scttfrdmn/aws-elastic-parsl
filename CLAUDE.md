# CLAUDE.md — aws-elastic-parsl

Ephemeral AWS execution for Parsl. Sister project: ../aws-elastic-gcs.

## Phases
1. Fix Parsl's stock `AWSProvider` (parsl/providers/aws/aws.py) and upstream the fixes as PRs to
   Parsl/parsl from the fork scttfrdmn/parsl. Local clone: ../parsl-upstream (one branch per fix,
   worktrees under ../parsl-upstream-wt/). Open upstream PRs only after the user reviews them.
2. Detailed docs and guides for the stock provider.
3. `AWSEphemeralProvider`: same constructor interface as `AWSProvider`; all resources per run,
   torn down on exit; workers reach the submit host without standing compute.
4. Globus Compute support.

## Conventions
- AWS: `AWS_PROFILE=aws`, account 942542972736, region us-west-2. EC2 key pair `aws-elastic-parsl`
  (private key ~/.ssh/aws-elastic-parsl.pem).
- The account has unrelated instances (e.g. aws-elastic-gcs DTNs) and about 4 free VPC slots. Never touch
  resources this project didn't create; the stock provider tags its VPC `Name=parsl.aws.<ts>`.
- Dev venv: `.venv` (Python 3.12, which Ubuntu 24.04 workers match). Put `.venv/bin` on PATH
  when running Parsl, because HTEX launches `interchange.py` from PATH.
- Upstream Parsl checks: `make flake8`, isort, mypy, `pytest --config local` on new tests.
- Version stays 0.x. Status lives in GitHub issues/milestones/project board; no local plan files.
