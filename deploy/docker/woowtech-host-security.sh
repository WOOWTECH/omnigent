#!/bin/sh
set -eu

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

curl -fsSL --retry 3 https://registry.npmjs.org/npm/-/npm-12.2.2.tgz -o "$work_dir/npm.tgz"
printf '%s  %s\n' 2a09606d9c9a71fee31fe147b8d46a56e0b3422f15848d9aec9f3b231e3716c3d3b99a065a62175c6759b4150e03bf6fc0bf8825112a511573ba903b3ec41370 "$work_dir/npm.tgz" | sha512sum -c -
npm install --global --engine-strict --ignore-scripts --no-audit --no-fund "$work_dir/npm.tgz"

# Preserve Pi itself; undici has no dependencies and stays on major version 8.
curl -fsSL --retry 3 https://registry.npmjs.org/undici/-/undici-8.12.3.tgz -o "$work_dir/undici.tgz"
printf '%s  %s\n' f9f7677544f004ff3a7158e2853a76d61647a3b77aa23d5cff8ed73c0de52fc98cd171ecf82e0491c8e6811b059c59367ff50e3460f2f48b4122d997b0b3901f "$work_dir/undici.tgz" | sha512sum -c -
mkdir "$work_dir/undici"
tar -xzf "$work_dir/undici.tgz" -C "$work_dir/undici" --strip-components=1
pi_modules=/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/node_modules
test -f "$pi_modules/undici/package.json"
rm -rf "$pi_modules/undici"
mv "$work_dir/undici" "$pi_modules/undici"

sh /build/woowtech-gh-security.sh

test "$(npm --version)" = 12.2.2
mkdir "$work_dir/npm-smoke"
printf '%s\n' '{"name":"overlay-smoke","version":"1.0.0","scripts":{"verify":"node --version"}}' > "$work_dir/npm-smoke/package.json"
npm --prefix "$work_dir/npm-smoke" install --offline --ignore-scripts --no-audit --no-fund
npm --prefix "$work_dir/npm-smoke" run verify --offline
gh --version
pi --version
node -e 'const u = require("/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/node_modules/undici/package.json"); if (u.version !== "8.12.3") process.exit(1)'
