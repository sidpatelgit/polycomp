# PolyComp

PolyComp is the release package for the 120-problem BlockSplit benchmark. It
contains the exact raster images used in the reported evaluation, normalized
problem manifests, provider request builders for all three visual
presentations, and the minimal 1,080-row result table used to compute the paper
scores.

The command-line tool is deliberately local-only. It does not read API keys,
make network requests, submit jobs, or contain provider-recovery machinery.

## Setup

Install [uv](https://docs.astral.sh/uv/), select Python 3.11, and create the
environment:

```bash
uv python install 3.11
uv sync --frozen --group dev
```

Verify the complete release before using it:

```bash
uv run --frozen polycomp verify
```

Verification checks all 120 problem manifests, the 24 proper cube rotations,
6-neighbor connectivity and the frozen target partition, an exactly-one-valid-
option assembly proof for every problem, 120 composite images, 600 committed
crops, 120 reconstructed reference SVGs, 1,080 request identities, nine complete
historical batch-input hashes, the 1,080 unique result cells, the one empty
refusal, and every reported model/presentation score.

## Generate the problem presentations

Materialize all three presentations for all 120 problems:

```bash
uv run --frozen polycomp generate --all --presentation all --output generated
```

Generate only one problem and one presentation:

```bash
uv run --frozen polycomp generate \
  --problem block_split_006 \
  --presentation multi_image_descriptive \
  --output generated
```

The output layout is:

```text
generated/
  single_image/<problem_identifier>/image.png
  multi_image_generic/<problem_identifier>/image_1.png ... image_5.png
  multi_image_descriptive/<problem_identifier>/target_object_views.png ...
```

Generation copies and re-verifies the frozen image bytes. This intentionally
materializes the exact model-visible rasters instead of rerendering a merely
equivalent scene. The single-image files are the actual 2500x2500 `resvg` PNGs
used for the run; they were not resized or downsampled. The five-image
presentations use exact crops of those same PNGs. Keeping the submitted rasters
as release assets avoids renderer, font, and platform drift. Independently,
`polycomp verify` reads the integer-cell geometry in every normalized manifest
and proves connectivity, the target partition, and exactly one valid option
under all 24 orientation-preserving cube rotations plus integer translations.

## Rerender from normalized geometry

`generate` above is the reproducibility path for the evaluation: it always
copies the frozen submitted PNG bytes and does not need a renderer or font. For
people who want to inspect image construction, the separate `rerender` command
rebuilds each explicit SVG from the normalized integer-cell geometry, recorded
view matrices, and recorded shared scale; rasterizes the SVG with `resvg`; and
makes both five-crop presentation layouts with Pillow.

Install `resvg`, then reconstruct one problem and require an exact match with
the frozen submission:

```bash
resvg --version
uv run --frozen polycomp rerender \
  --problem block_split_006 \
  --output rerendered \
  --verify-frozen
```

The reference rasterizer reports exactly `0.47.0`. A different version may
still be useful for inspection, but should be expected to report drift rather
than treated as the reference replay.

Rerender all 120 problems in the same way:

```bash
uv run --frozen polycomp rerender \
  --all \
  --output rerendered \
  --verify-frozen
```

The command writes 120 SVGs, 120 single-image PNGs, and 1,200 presentation crop
files (the generic and descriptive presentations use different filenames for
the same five pixels), plus `rerender-report.json`. It reconstructs into a
temporary directory first. With `--verify-frozen`, any SVG, PNG, dimension,
decoded-pixel, or byte mismatch fails without publishing the output directory.

Without `--verify-frozen`, the command still compares every output with the
frozen reference but retains the reconstruction for inspection. Its JSON status
and `rerender-report.json` say either `matched_frozen` or
`rendered_with_drift`. The latter is not an evaluation reproduction; use the
committed PNGs through `generate` when exact model-visible bytes matter.

The successful release replay observed during QA used macOS 26.5.2 (build
25F84) on arm64,
CPython 3.11.14, Pillow 11.3.0, and `resvg` 0.47.0. The SVG requests
`Arial, Helvetica, sans-serif`; the reference machine resolved Arial and Arial
Bold from `/System/Library/Fonts/Supplemental/Arial.ttf` and
`/System/Library/Fonts/Supplemental/Arial Bold.ttf`. This repository does not
redistribute or hash-pin those font files. A different font installation,
renderer version, Pillow/zlib build, or platform may change PNG bytes. On that
documented replay environment, the extracted pipeline reproduced all 1,440
render files byte-for-byte; `--verify-frozen` is the definitive local check.
The current command publishes its staged output with the macOS no-replace rename
primitive, so this first release of `rerender` is supported on macOS. The pure
SVG reconstruction checked by `polycomp verify` remains platform-independent.

## Generate API request payloads

Create one direct request body:

```bash
uv run --frozen polycomp payload \
  --provider openai \
  --presentation single_image \
  --problem block_split_006 \
  --output payload.json
```

`--provider` accepts `openai`, `anthropic`, or `gemini`.
`--presentation` accepts `single_image`, `multi_image_generic`, or
`multi_image_descriptive`.

Create 120 separate direct request bodies:

```bash
uv run --frozen polycomp payload \
  --provider anthropic \
  --presentation multi_image_generic \
  --all \
  --output generated-payloads
```

Recreate the exact historical 120-request Batch input, including its recorded
custom IDs and canonical serialization:

```bash
uv run --frozen polycomp payload \
  --provider gemini \
  --presentation multi_image_descriptive \
  --all \
  --batch \
  --output gemini-multi-image-descriptive.jsonl
```

For Anthropic, use a `.json` output name; OpenAI and Gemini use JSONL. These
commands only write payload files. Supplying credentials and submitting the
files is intentionally outside this repository.

## The three presentations

All providers received the main prompt after the image content. No system
instruction or temperature parameter was supplied.

- `single_image`: one composite image, with no model-facing image label.
- `multi_image_generic`: five crops labeled `Image 1:` through `Image 5:`.
- `multi_image_descriptive`: five crops labeled `Target:` and `Option A:`
  through `Option D:`.

The exact prompts, content order, model configuration, image-detail settings,
and provider body shapes are frozen in `data/protocol.json`.

## Integrity and provenance

`data/assets.json` is the verifier-authoritative record for the rasters submitted
to the providers. It binds each single image and crop to its release path,
dimensions, file SHA-256, and decoded-pixel SHA-256. `polycomp verify` checks
those bindings against the committed files and uses them when reconstructing
requests.

`data/rendering_reference.json` separately binds the optional reconstruction
pipeline to the release-local renderer and all 120 expected SVG byte hashes. It
records the observed replay environment and the fact that no font resource is
bundled. The normalized manifests supply the exact geometry, option cells, view
matrices, and scale. This reference does not supersede `data/assets.json` for
submitted rasters.

`block_split_120_2026-07-09` is the stable dataset-version label used throughout
the release. It is useful for joining and identifying PolyComp records, but is
not an external repository locator; cite a public PolyComp release or DOI along
with it. Private source-repository snapshot names, commits, paths, job IDs, and
retry/recovery accounting are intentionally not published.

The normalized manifests contain the public problem identity, geometry,
answer, rendering inputs, and release-asset link. Internal construction-wave,
selection, and legacy validation-source metadata are intentionally omitted.
The current verifier recomputes the geometry and asset checks from the public
records instead of relying on those historical annotations.

The historical runner recorded several complementary hashes:

1. Each submitted image had its own SHA-256.
2. Each cell had a canonical hash of an audit payload in which image bytes were
   replaced by a fixed redaction marker.
3. A logical request hash bound that audit payload to every submitted filename
   and image SHA-256.
4. Each complete 120-request Batch input file had a SHA-256 over its exact
   bytes.

It did **not** originally store a separate full-body hash for every cell. During
release preparation, `data/request_hashes.csv` added a clearly labeled,
per-cell hash derived from the transmitted body inside each hash-verified
historical Batch input. `polycomp verify` reconstructs every body and all nine
complete Batch inputs, then compares both the derived per-cell hashes and the
original whole-file hashes. This proves byte-for-byte recreation without
publishing the large historical job files.

## Data files

- `data/problems.jsonl`: ordered 120-problem index and manifest hashes.
- `data/manifests/`: normalized geometry, choices, answers, rendering metadata,
  and release-asset links.
- `data/assets.json`: the verifier-authoritative submitted-image paths,
  dimensions, crop boxes, and file and pixel hashes.
- `data/rendering_reference.json`: release renderer binding, reference
  environment, reconstruction policy, and expected hashes/sizes for all 120
  SVGs.
- `assets/single_image/`: the actual frozen single-image PNGs.
- `assets/crops/`: one byte-identical crop set shared by the two multi-image
  naming/label presentations.
- `data/protocol.json`: exact prompts, settings, presentation order, and request
  body contract.
- `data/request_hashes.csv`: original request/cell identities plus the
  release-derived transmitted-body hashes.
- `data/results.csv`: exactly five columns: `model`, `problem_identifier`,
  `presentation`, `model_response`, and `correct_response`.
- `data/results_provenance.json`: result-table identity, public extraction and
  response-text policies, refusal declaration, and release hashes.
- `data/reported_scores.json`: the score values reconciled by the tests.
- `data/provenance.json`: release-file hashes and the nine public historical
  batch size/hash bindings.

`model_response` preserves the visible response text exactly. The single
Claude refusal is intentionally represented by an empty field, rather than by
invented placeholder text.

## Development checks

The lockfile pins the canonical Ruff version used by this release. Run the
tests, lint checks, and formatter check with:

```bash
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ruff format --check .
```

## License

Software is licensed under Apache-2.0. Benchmark assets, data, and
documentation are licensed under CC BY 4.0, subject to the third-party-rights
boundary described in [LICENSE.md](LICENSE.md). See [ATTRIBUTION.md](ATTRIBUTION.md)
and [CITATION.cff](CITATION.cff) for attribution and citation guidance.
