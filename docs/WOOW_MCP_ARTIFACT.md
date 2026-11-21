# WOOW native MCP portable artifact

Task 2 contract for HA, generic server and Kubernetes server. Core metadata stays
`omnigent==0.21.0`; the WOOW build identity is independent of the PyPI version.

The artifact directory contains:

- `omnigent-0.21.0-py3-none-any.whl`: identical bytes for every platform.
- `WOOW_BUILD_RECEIPT.json`: source commit, build ID, wheel SHA-256, UI SHA-256,
  dependency lock hashes, tool versions, migration head and CI provenance.
- `WOOW_CONTENT_MANIFEST.json`: SHA-256 of every wheel payload file (except the
  self-referential wheel RECORD), plus the source input manifest.
- `SHA256SUMS`: hashes of all three files above.

HA must copy the wheel from the receipt's immutable `wheelImage`, check
`wheelSha256`, verify the receipt and
install the wheel with `--no-deps --force-reinstall`; its existing locked Alpine
runtime dependency assembly remains authoritative. Do not use a bare
`omnigent==0.21.0` reinstall after this step. Keep the receipt alongside the image
and use `scripts/woowtech_mcp_artifact.py runtime` to verify installed files.

`uiSha256` is SHA-256 of canonical JSON (sorted keys, compact separators, ASCII)
mapping wheel paths under `omnigent/server/static/web-ui/` to their SHA-256.
`contentSha256` uses the same algorithm for all wheel payload files. The external
receipt avoids a circular wheel-hash dependency. The wheel embeds the matching
source identity at `omnigent/_woow_build.json` and normalized `_build_info.py`.

## Implementation and execution

Build prerequisites: uv 0.13.4, Node 22.23.3 and pnpm 11.15.1. The builder
uses uv-managed CPython 3.12.14 and records the tool versions.

1. Export immutable commit S to a temporary tree, install frozen pnpm dependencies,
   build the SPA, and run `uv build --wheel` using hash-pinned builder dependencies.
2. Normalize generated build metadata and wheel ZIP timestamps/ordering; regenerate
   RECORD; validate native MCP, migration, UI and inherited security markers.
3. Build both image variants from the exact artifact and immutable upstream base
   index. Preserve the existing security script and validate the installed wheel.
4. CI checks images on native amd64 and arm64 runners before optional publication.
   Receipts include the resulting per-architecture and manifest digests.

No commit, push, dispatch, release, merge or deployment is performed by the local
build command. Local snapshot mode is explicitly unpublished: sourceCommit is
null, sourceBaseCommit records HEAD and sourceTreeSha256 identifies the copied
inputs. It cannot be used by the image publication workflow.

```sh
uv run --no-project --python 3.12.14 python scripts/woowtech_mcp_artifact.py build --source-commit "$S" --output /tmp/mcp-wheel
python3 scripts/woowtech_mcp_artifact.py verify --artifact /tmp/mcp-wheel --source-commit "$S"
```

For concurrent development, build a frozen copy without modifying the shared
checkout or claiming its HEAD contains uncommitted native MCP work:

```sh
uv run --no-project --python 3.12.14 python scripts/woowtech_mcp_artifact.py build --snapshot --output /tmp/mcp-snapshot
```

The parent freezes S and coordinates push/build execution. Actions artifacts
carry receipts between jobs; HA obtains wheel bytes from the public candidate
image by digest. No release asset or raw wheel URL is required.

## HA Docker consumption without an expiring URL

Both server variants contain the same verified portable artifact under
`/opt/woow-artifact/`. The parent's `candidate-image.json` supplies the immutable
`wheelImage`, `wheelPath`, `wheelSha256`, `sourceCommit`, and both architecture
digests. Use either candidate as a Docker stage and copy its wheel into the
existing Alpine dependency assembly; never execute Debian binaries in Alpine.

```dockerfile
ARG WOOW_MCP_WHEEL_IMAGE
FROM ${WOOW_MCP_WHEEL_IMAGE} AS woow-mcp-wheel
# Existing HA Alpine build stages continue here.
# COPY --from=woow-mcp-wheel /opt/woow-artifact/ /tmp/woow-artifact/
# Verify expected wheel hash/source, install --no-deps --force-reinstall, then
# run verify.py runtime with Alpine's Python and retain receipt/manifest.
```

Both variants publish into the existing public package
`ghcr.io/woowtech/omnigent-server-kubernetes`. The package name is the shared
artifact namespace; select runtime behavior from the receipt's `variant` and
`baseImage`, not the package name. Candidate tags are unique:

- Generic: `0.21.0-mcp.1-server-sha-<S>-run-<runId>-<attempt>`.
- Kubernetes: `0.21.0-mcp.1-server-kubernetes-sha-<S>-run-<runId>-<attempt>`.
- Architecture tags append `-amd64` or `-arm64` to the matching variant tag.

Each image carries `io.woowtech.omnigent.variant`, immutable base-image and source
revision labels, checked before push. Each receipt includes explicit `variant`,
compatibility `component`, `baseImage`, source, wheel hash and immutable image
reference. Manifest assembly rejects mixed variants, tags, bases or wheel hashes.

Use `candidate-image.json` from `woow-mcp-candidate-<variant>-<runId>-<attempt>`.
HA may retain its Kubernetes-variant receipt choice; Podman uses `variant=server`.
The shared package permits anonymous pulls (verified 2026-11-21). Verify each new
digest can be pulled anonymously before downstream consumption. No new package,
visibility configuration or release publication is needed.

The existing `release/woowtech-overlay/**` branch push invokes the reusable MCP
workflow. It builds one wheel and four native image candidates, verifies each,
pushes unique architecture tags, and joins each variant's two digests into a unique
manifest tag. PR runs execute the same builds/verification without registry writes.
The existing host build remains independent. Both core package and host stay 0.21.0.

If a publication attempt fails, rerun all jobs so architecture receipts share
the same run attempt; mixed-attempt manifest assembly is deliberately rejected.
