#!/bin/sh
set -eu

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

# Rebuild the same CLI release with fixed Go modules; retain upstream OAuth defaults.
curl -fsSL --retry 3 https://go.dev/dl/go1.27.3.linux-amd64.tar.gz -o "$work_dir/go.tgz"
printf '%s  %s\n' 65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40 "$work_dir/go.tgz" | sha256sum -c -
curl -fsSL --retry 3 https://codeload.github.com/cli/cli/tar.gz/refs/tags/v2.103.3 -o "$work_dir/cli.tgz"
printf '%s  %s\n' bb148138a3a2fe023e57c78042fe3b42fc09171c0e8fff4cf983b227b7a2eade "$work_dir/cli.tgz" | sha256sum -c -
tar -xzf "$work_dir/go.tgz" -C "$work_dir"
tar -xzf "$work_dir/cli.tgz" -C "$work_dir"
(
    cd "$work_dir/cli-2.103.3"
    export PATH="$work_dir/go/bin:$PATH"
    export GOPATH="$work_dir/modules" GOCACHE="$work_dir/cache"
    export GOTOOLCHAIN=local GOENV=off CGO_ENABLED=0
    go get golang.org/x/net@v0.62.0 golang.org/x/crypto@v0.58.1-0.20261009181718-2f1f834330b2
    go mod verify
    go build -trimpath -ldflags '-s -w -X github.com/cli/cli/v2/internal/build.Version=2.103.3-woowtech.1 -X github.com/cli/cli/v2/internal/build.Date=2026-11-08' -o "$work_dir/gh" ./cmd/gh
)
install -m 0755 "$work_dir/gh" /usr/local/bin/gh
gh --version
gh completion -s bash > /dev/null
