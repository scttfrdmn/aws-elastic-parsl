# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/) (0.x until further notice).

## [Unreleased]

### Added
- `examples/stock_provider_smoke.py`: runs Parsl's stock `AWSProvider` from a laptop using
  `AWS_PROFILE`, bridging worker traffic over an SSH reverse tunnel.
- `examples/cleanup_stock_provider.py`: removes the VPCs the stock provider leaves behind.
