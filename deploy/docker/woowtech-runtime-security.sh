#!/bin/sh
set -eu

# Keep security updates limited to known fixed versions from Debian's signed repo.
apt-get update
apt-get install -y --no-install-recommends --only-upgrade     apt=3.0.4 libapt-pkg7.0=3.0.4     zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1
rm -rf /var/lib/apt/lists/*

# Both Python installations are inherited from the upstream images.
/usr/local/bin/python -m pip install     --no-cache-dir --no-deps --only-binary=:all: --require-hashes     -r /build/woowtech-pip-security.txt
/usr/local/bin/python -m pip --python /opt/venv/bin/python install     --no-cache-dir --no-deps --only-binary=:all: --require-hashes     -r /build/woowtech-pip-security.txt
