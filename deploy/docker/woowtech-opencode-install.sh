#!/bin/sh
set -eu

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

# The baseline binary avoids requiring AVX2 on older tenant worker nodes.
curl -fsSL --retry 3 https://registry.npmjs.org/opencode-linux-x64-baseline/-/opencode-linux-x64-baseline-1.18.35.tgz -o "$work_dir/opencode.tgz"
printf '%s  %s\n' 33848efc0de2da97cec70065d82e30e99823531d421f4635fb222b73639d6a01f764e62e842885476155c613b033103f0b194cad2dd9eb1c58f164990867ee63 "$work_dir/opencode.tgz" | sha512sum -c -
tar -xzf "$work_dir/opencode.tgz" -C "$work_dir"
install -m 0755 "$work_dir/package/bin/opencode" /usr/local/bin/opencode
mkdir "$work_dir/fresh-home"
test "$(HOME="$work_dir/fresh-home" opencode --version)" = 1.18.35
