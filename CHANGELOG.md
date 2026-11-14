# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/) (0.x until further notice).

## [Unreleased]

### Added
- `globus-compute/`: Globus Compute endpoint (4.21.0) on the fixed stock `AWSProvider`, run in a
  container on a laptop, with `gce.sh` to build, log in, configure, start, test and stop.
- `aws_elastic_parsl.tunnel`: keeps an SSH reverse tunnel open to every instance carrying an
  AWSProvider's `parsl:infrastructure` tag, so workers can reach a submit host behind NAT.
- `examples/stock_provider_smoke.py`: runs Parsl's stock `AWSProvider` from a laptop using
  `AWS_PROFILE`, bridging worker traffic over an SSH reverse tunnel.
- `examples/cleanup_stock_provider.py`: removes the VPCs the stock provider leaves behind.
- `docs/stock-aws-provider/`: guide to Parsl's stock `AWSProvider`, covering released Parsl and the
  pending fixes. It has quickstarts (laptop, in-AWS), credentials, networking, worker environment,
  lifecycle and cost, released-version workarounds, troubleshooting and a parameter reference.
