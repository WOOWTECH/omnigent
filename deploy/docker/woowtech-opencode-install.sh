#!/bin/sh
set -eu

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

# v0.21.0 的 opencode-native harness 只接受 OpenCode >=2.0.18,<2.1.0；2.0.32 是 2.0.x 的最後一版，
# 也是上游 harness_install 的預設版本。2.x 改以 @opencode/cli-<platform> 發佈。
# baseline 版不需要 AVX2（2026-11-10 實查：叢集四台節點都有 AVX2，沿用 baseline 只是保守選擇）。
curl -fsSL --retry 3 https://registry.npmjs.org/@opencode/cli-linux-x64-baseline/-/cli-linux-x64-baseline-2.0.32.tgz -o "$work_dir/opencode.tgz"
printf '%s  %s\n' 36b0788a09496fbbc665627a074a951f510c1e1581678003b001bd5b4c4b6a7ce0c49e9e2c412c181bf1457d1c9bf28f6a0e44fcdd32646b03543658727cff39 "$work_dir/opencode.tgz" | sha512sum -c -
tar -xzf "$work_dir/opencode.tgz" -C "$work_dir"
install -m 0755 "$work_dir/package/bin/opencode" /usr/local/bin/opencode
mkdir "$work_dir/fresh-home"
# 2.x 印的是 `opencode v2.0.32`（TTY 時帶顏色），去掉 ANSI 再比對；用一次性的 HOME／XDG，
# 不在 image 裡留下 OpenCode 的狀態檔。
version_output=$(HOME="$work_dir/fresh-home" XDG_DATA_HOME="$work_dir/fresh-home/data" \
    XDG_CONFIG_HOME="$work_dir/fresh-home/config" XDG_STATE_HOME="$work_dir/fresh-home/state" \
    XDG_CACHE_HOME="$work_dir/fresh-home/cache" OPENCODE_DISABLE_AUTOUPDATE=1 \
    opencode --version 2>&1 | sed 's/\x1b\[[0-9;]*m//g')
echo "$version_output"
printf '%s\n' "$version_output" | grep -Eq '(^|[^0-9.])v?2\.0\.32([^0-9.]|$)'
