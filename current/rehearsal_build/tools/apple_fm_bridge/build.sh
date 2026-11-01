#!/bin/sh
# Build the Apple on-device Foundation Models bridge ONCE, at build/install time.
#
#   tools/apple_fm_bridge/build.sh [output-path]
#
# Default output: tools/apple_fm_bridge/bin/apple_fm_bridge
#
# The Python provider only ever *invokes* the compiled binary; it never compiles
# at request time (compiling costs seconds and needs the toolchain, so a missing
# binary must fail closed as `not_compiled`, not silently build mid-request).
#
# Requirements: macOS 26+, Xcode CommandLineTools (`swiftc`), and the
# FoundationModels SDK shipped with them. No third-party dependency, no network.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
out=${1:-"$here/bin/apple_fm_bridge"}
source_file="$here/main.swift"

if [ ! -f "$source_file" ]; then
    echo "build.sh: bridge source missing: $source_file" >&2
    exit 1
fi

swiftc_path=$(command -v swiftc || true)
if [ -z "$swiftc_path" ]; then
    echo "build.sh: swiftc not found; install Xcode CommandLineTools (xcode-select --install)" >&2
    exit 1
fi

sdk=$(xcrun --sdk macosx --show-sdk-path 2>/dev/null || true)
if [ -z "$sdk" ] || [ ! -d "$sdk/System/Library/Frameworks/FoundationModels.framework" ]; then
    echo "build.sh: FoundationModels.framework not found in the macOS SDK; macOS 26+ toolchain required" >&2
    exit 1
fi

mkdir -p "$(dirname "$out")"
# NOTICE: write to a temp path first, then move, so a failed build never leaves
# a half-written binary that the provider would happily execute.
tmp="$out.build.$$"
trap 'rm -f "$tmp"' EXIT INT TERM
"$swiftc_path" -sdk "$sdk" -O "$source_file" -o "$tmp"
mv "$tmp" "$out"
trap - EXIT INT TERM
echo "built $out"
