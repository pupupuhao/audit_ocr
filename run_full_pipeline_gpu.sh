#!/usr/bin/env bash
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

INPUT="input"
VL_OUTPUT="output_auto_vl_batch"
OUTPUT="audit_ocr_vl_llm_json"
COMBINED_JSON=""
DPI="180"
OCR_PYTHON="${OCR_PYTHON:-python}"
QWEN_CMD=(${QWEN_CMD:-conda run -n qwen-transformers python})
PREFER_SOURCE="md"
DEBUG_LLM=0
SKIP_OCR=0
SKIP_EXTRACT=0
FILES=()
FILE_LIST=""
PAGES=()
START_PAGE=""
END_PAGE=""
MAX_PAGES=""

usage() {
  cat <<'EOF'
Usage:
  ./run_full_pipeline_gpu.sh [options]

Options:
  --input DIR              Input PDF directory. Default: input
  --vl-output DIR          OCR/VL output root. Default: output_auto_vl_batch
  --output DIR             Business JSON output root. Default: audit_ocr_vl_llm_json
  --combined-json FILE     Combined JSON file. Default: <output>/business_extract_all.json
  --dpi N                  PDF render DPI. Default: 180
  --file PDF               One PDF path/name. Can be repeated.
  --file-list FILE         Text file with one PDF path/name per line.
  --page N                 Specific 1-based page(s), comma-separated or repeated.
  --start-page N           First 1-based page number.
  --end-page N             Last 1-based page number.
  --max-pages N            Max pages after page filtering.
  --prefer-source VALUE    auto/html/md/json/all. Default: md
  --ocr-python CMD         Python for OCR and JSON bundling. Default: python
  --qwen-cmd CMD           Command for Qwen extraction. Default: conda run -n qwen-transformers python
  --debug-llm              Save Qwen prompts and responses.
  --skip-ocr               Do not run OCR/VL.
  --skip-extract           Do not run Qwen extraction.
  -h, --help               Show this help.

Examples:
  ./run_full_pipeline_gpu.sh
  ./run_full_pipeline_gpu.sh --page 14
  ./run_full_pipeline_gpu.sh --file "sample.pdf" --debug-llm
EOF
}

require_value() {
  local option="$1"
  local value="${2:-}"
  if [[ -z "$value" ]]; then
    echo "Missing value for $option" >&2
    exit 2
  fi
}

basename_no_ext() {
  local value="$1"
  value="${value##*/}"
  value="${value%.pdf}"
  echo "$value"
}

selected_extract_files() {
  local selected=()
  local item line
  for item in "${FILES[@]}"; do
    selected+=("$(basename_no_ext "$item")")
  done
  if [[ -n "$FILE_LIST" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line#"${line%%[![:space:]]*}"}"
      line="${line%"${line##*[![:space:]]}"}"
      [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
      selected+=("$(basename_no_ext "$line")")
    done < "$FILE_LIST"
  fi
  printf '%s\n' "${selected[@]}" | awk 'NF && !seen[$0]++'
}

write_combined_json() {
  local input_root="$1"
  local output_path="$2"
  "$OCR_PYTHON" - "$input_root" "$output_path" <<'PY'
import json
import sys
from datetime import datetime
from pathlib import Path

input_root = Path(sys.argv[1])
output_path = Path(sys.argv[2])
if not input_root.exists():
    raise SystemExit(f"Business JSON folder not found: {input_root}")

files = []
for path in sorted(input_root.rglob("business_extract.json")):
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        payload = {"data": payload}
    item = {"source_json": str(path)}
    item.update(payload)
    files.append(item)

if not files:
    raise SystemExit(f"No business_extract.json files found under: {input_root}")

output_path.parent.mkdir(parents=True, exist_ok=True)
with output_path.open("w", encoding="utf-8") as fh:
    json.dump(
        {
            "file_count": len(files),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "files": files,
        },
        fh,
        ensure_ascii=False,
        indent=2,
    )
print(f"Combined JSON: {output_path}")
PY
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input) require_value "$1" "${2:-}"; INPUT="$2"; shift 2 ;;
    --vl-output) require_value "$1" "${2:-}"; VL_OUTPUT="$2"; shift 2 ;;
    --output) require_value "$1" "${2:-}"; OUTPUT="$2"; shift 2 ;;
    --combined-json) require_value "$1" "${2:-}"; COMBINED_JSON="$2"; shift 2 ;;
    --dpi) require_value "$1" "${2:-}"; DPI="$2"; shift 2 ;;
    --file) require_value "$1" "${2:-}"; FILES+=("$2"); shift 2 ;;
    --file-list) require_value "$1" "${2:-}"; FILE_LIST="$2"; shift 2 ;;
    --page) require_value "$1" "${2:-}"; PAGES+=("$2"); shift 2 ;;
    --start-page) require_value "$1" "${2:-}"; START_PAGE="$2"; shift 2 ;;
    --end-page) require_value "$1" "${2:-}"; END_PAGE="$2"; shift 2 ;;
    --max-pages) require_value "$1" "${2:-}"; MAX_PAGES="$2"; shift 2 ;;
    --prefer-source) require_value "$1" "${2:-}"; PREFER_SOURCE="$2"; shift 2 ;;
    --ocr-python) require_value "$1" "${2:-}"; OCR_PYTHON="$2"; shift 2 ;;
    --qwen-cmd) require_value "$1" "${2:-}"; read -r -a QWEN_CMD <<< "$2"; shift 2 ;;
    --debug-llm) DEBUG_LLM=1; shift ;;
    --skip-ocr) SKIP_OCR=1; shift ;;
    --skip-extract) SKIP_EXTRACT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -z "$COMBINED_JSON" ]] && COMBINED_JSON="$OUTPUT/business_extract_all.json"
BUSINESS_JSON_ROOT="$OUTPUT/business_json_vl_llm"
COMMON_PAGE_ARGS=()
for page in "${PAGES[@]}"; do
  COMMON_PAGE_ARGS+=(--page "$page")
done
[[ -n "$START_PAGE" ]] && COMMON_PAGE_ARGS+=(--start-page "$START_PAGE")
[[ -n "$END_PAGE" ]] && COMMON_PAGE_ARGS+=(--end-page "$END_PAGE")
[[ -n "$MAX_PAGES" ]] && COMMON_PAGE_ARGS+=(--max-pages "$MAX_PAGES")

echo "Full GPU pipeline"
echo "Input:         $INPUT"
echo "VL output:     $VL_OUTPUT"
echo "JSON output:   $OUTPUT"
echo "Combined JSON: $COMBINED_JSON"

if [[ "$SKIP_OCR" -eq 0 ]]; then
  ocr_args=(run_batch_ocr.py --input "$INPUT" --output "$VL_OUTPUT" --dpi "$DPI")
  for item in "${FILES[@]}"; do
    ocr_args+=(--file "$item")
  done
  [[ -n "$FILE_LIST" ]] && ocr_args+=(--file-list "$FILE_LIST")
  ocr_args+=("${COMMON_PAGE_ARGS[@]}")
  echo
  echo "### 1/3 Auto-VL OCR"
  "$OCR_PYTHON" "${ocr_args[@]}"
fi

if [[ "$SKIP_EXTRACT" -eq 0 ]]; then
  selected_files=()
  while IFS= read -r selected_file; do
    [[ -n "$selected_file" ]] && selected_files+=("$selected_file")
  done < <(selected_extract_files)
  if [[ "${#selected_files[@]}" -gt 0 ]]; then
    index=0
    for selected in "${selected_files[@]}"; do
      index=$((index + 1))
      extract_args=(run_vl_llm_extract_transformers.py --vl-output "$VL_OUTPUT" --output "$OUTPUT" --prefer-source "$PREFER_SOURCE" --file "$selected")
      extract_args+=("${COMMON_PAGE_ARGS[@]}")
      [[ "$DEBUG_LLM" -eq 1 ]] && extract_args+=(--debug-llm)
      echo
      echo "### 2/3 Qwen business extraction ($index/${#selected_files[@]})"
      "${QWEN_CMD[@]}" "${extract_args[@]}"
    done
  else
    extract_args=(run_vl_llm_extract_transformers.py --vl-output "$VL_OUTPUT" --output "$OUTPUT" --prefer-source "$PREFER_SOURCE")
    extract_args+=("${COMMON_PAGE_ARGS[@]}")
    [[ "$DEBUG_LLM" -eq 1 ]] && extract_args+=(--debug-llm)
    echo
    echo "### 2/3 Qwen business extraction"
    "${QWEN_CMD[@]}" "${extract_args[@]}"
  fi
fi

echo
echo "### 3/3 Build one JSON bundle"
write_combined_json "$BUSINESS_JSON_ROOT" "$COMBINED_JSON"

echo
echo "Done."
echo "Per-PDF JSON:   $BUSINESS_JSON_ROOT"
echo "Combined JSON:  $COMBINED_JSON"
