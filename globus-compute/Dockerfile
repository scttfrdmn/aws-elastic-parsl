# Globus Compute endpoint whose engine uses the (fixed) stock AWSProvider.
# Globus Compute endpoints need Linux; this runs one on a laptop. Python must match
# the workers' (Ubuntu 24.04: 3.12) for Parsl's version check.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client \
    && rm -rf /var/lib/apt/lists/*

ARG GCE_VERSION=4.21.0
# scttfrdmn/parsl aws-provider-fixes-2026.11.09, pinned for reproducible builds
ARG PARSL_SPEC="parsl[aws] @ git+https://github.com/scttfrdmn/parsl@d0e77e925e89f73a71c9d4498a8165c4cf42c944"
RUN pip install --no-cache-dir "globus-compute-endpoint==${GCE_VERSION}" "${PARSL_SPEC}"

COPY src/aws_elastic_parsl /opt/aws_elastic_parsl/aws_elastic_parsl
ENV PYTHONPATH=/opt/aws_elastic_parsl
COPY globus-compute/entrypoint.sh /usr/local/bin/entrypoint.sh
# A root endpoint is a multi-user endpoint; this is a single-user one.
RUN useradd --create-home --uid 1000 gc && install -d -o gc -g gc /home/gc/.globus_compute
USER gc
WORKDIR /home/gc
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
