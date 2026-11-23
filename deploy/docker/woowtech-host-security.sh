#!/bin/sh
set -eu

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

# v0.21.0 host image 內建 npm 10.9.9，它附帶的 brace-expansion、sigstore、ip-address、pacote 等
# 有多個 HIGH；npm 12.2.2（2026-11-10 仍是最新版）只剩 minimatch／glob 兩個 MODERATE（尚無修補版）。
curl -fsSL --retry 3 https://registry.npmjs.org/npm/-/npm-12.2.2.tgz -o "$work_dir/npm.tgz"
printf '%s  %s\n' 2a09606d9c9a71fee31fe147b8d46a56e0b3422f15848d9aec9f3b231e3716c3d3b99a065a62175c6759b4150e03bf6fc0bf8825112a511573ba903b3ec41370 "$work_dir/npm.tgz" | sha512sum -c -
npm install --global --engine-strict --ignore-scripts --no-audit --no-fund "$work_dir/npm.tgz"

# 0.20 overlay 替 Pi 換上 undici 8.12.3；v0.21.0 的 Pi 1.5.0 已自帶 8.12.3，這裡只確認沒退版。
pi_undici=/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/node_modules/undici/package.json
node -e 'const u = require(process.argv[1]); if (u.version !== "8.12.3") { console.error(`undici ${u.version}`); process.exit(1); }' "$pi_undici"

sh /build/woowtech-gh-security.sh

test "$(npm --version)" = 12.2.2
mkdir "$work_dir/npm-smoke"
printf '%s\n' '{"name":"overlay-smoke","version":"1.0.0","scripts":{"verify":"node --version"}}' > "$work_dir/npm-smoke/package.json"
npm --prefix "$work_dir/npm-smoke" install --offline --ignore-scripts --no-audit --no-fund
npm --prefix "$work_dir/npm-smoke" run verify --offline
gh --version
pi --version
