#!/usr/bin/env python3
"""Build a shell script to rerun Qwen for selected file/page pairs."""

from __future__ import annotations

import argparse
import csv
import shlex
from collections import defaultdict
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Qwen rerun shell script from qwen_misaligned_pages.tsv.")
    parser.add_argument("--input", required=True, help="TSV from find_qwen_misaligned_pages.py.")
    parser.add_argument("--output-script", default="rerun_qwen_misaligned_pages.sh", help="Generated shell script.")
    parser.add_argument("--vl-output", default="output_auto_vl_batch", help="Existing VL output root.")
    parser.add_argument("--output", default="audit_ocr_vl_llm_json_repair", help="Repair JSON output root.")
    parser.add_argument("--prefer-source", default="md", choices=["auto", "html", "md", "json", "all"])
    parser.add_argument("--debug-llm", action="store_true")
    args = parser.parse_args()

    grouped: dict[str, set[int]] = defaultdict(set)
    with Path(args.input).expanduser().open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            file_name = (row.get("file_name") or "").strip()
            page_text = (row.get("page_no") or "").strip()
            if not file_name or not page_text:
                continue
            try:
                page_no = int(page_text)
            except ValueError:
                continue
            if page_no > 0:
                grouped[file_name].add(page_no)

    script_path = Path(args.output_script).expanduser()
    script_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "#!/usr/bin/env bash",
        "set -eo pipefail",
        "",
        'cd "$(dirname "$0")"',
        "",
        ': "${QWEN_CMD:=conda run -n qwen-transformers python}"',
        "",
    ]
    for index, file_name in enumerate(sorted(grouped), start=1):
        pages = ",".join(str(page) for page in sorted(grouped[file_name]))
        cmd = [
            "./run_full_pipeline_gpu.sh",
            "--skip-ocr",
            "--vl-output",
            args.vl_output,
            "--output",
            args.output,
            "--prefer-source",
            args.prefer_source,
            "--file",
            file_name,
            "--page",
            pages,
        ]
        if args.debug_llm:
            cmd.append("--debug-llm")
        lines.append(f"echo '### {index}/{len(grouped)} {file_name} pages={pages}'")
        lines.append("QWEN_CMD=\"$QWEN_CMD\" " + " ".join(shlex.quote(part) for part in cmd))
        lines.append("")

    script_path.write_text("\n".join(lines), encoding="utf-8")
    script_path.chmod(0o755)

    print(f"文件数: {len(grouped)}")
    print(f"页数: {sum(len(pages) for pages in grouped.values())}")
    print(f"重跑脚本: {script_path}")
    print(f"修复输出目录: {args.output}")


if __name__ == "__main__":
    main()
