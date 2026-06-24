<#
.SYNOPSIS
Batch-extract business JSON from existing OCR/VL output folders.

.DESCRIPTION
Processes every direct child folder under <VlOutput>\vl sequentially. Each PDF
folder gets its own output at:
  <Output>\business_json_vl_llm\<PDF folder>\business_extract.json

It does not run PDF rendering, RapidOCR, or PaddleOCR-VL. It only consumes
existing OCR/VL output and runs the local Transformers Qwen backend.
#>
[CmdletBinding()]
param(
    [string]$VlOutput = "E:\output_auto_vl",
    [string]$Output = "E:\audit_ocr_vl_llm_json",
    [ValidateSet("auto", "html", "md", "json", "all")]
    [string]$PreferSource = "md",
    [int]$StartPage = 0,
    [int]$EndPage = 0,
    [int]$MaxPages = 0,
    [switch]$DebugLlm,
    [string]$QwenPython = "D:\Users\LEE\anaconda3\envs\qwen-transformers\python.exe"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSCommandPath
$runner = Join-Path $repoRoot "run_vl_llm_extract_transformers.py"
$vlFolder = Join-Path $VlOutput "vl"

if (-not (Test-Path -LiteralPath $QwenPython -PathType Leaf)) {
    throw "Qwen Python not found: $QwenPython"
}
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw "Batch runner not found: $runner"
}
if (-not (Test-Path -LiteralPath $vlFolder -PathType Container)) {
    throw "VL output folder not found: $vlFolder"
}

$pdfFolders = @(Get-ChildItem -LiteralPath $vlFolder -Directory | Sort-Object Name)
if ($pdfFolders.Count -eq 0) {
    throw "No OCR/VL PDF folders found under: $vlFolder"
}

Write-Host "Batch VL extraction: $($pdfFolders.Count) OCR result folder(s)"
Write-Host "Input:  $vlFolder"
Write-Host "Output: $Output"

# No --file argument: run_vl_llm_extract.py processes every folder above.
$commandArgs = @(
    $runner,
    "--vl-output", $VlOutput,
    "--output", $Output,
    "--prefer-source", $PreferSource
)
if ($StartPage -gt 0) { $commandArgs += @("--start-page", $StartPage) }
if ($EndPage -gt 0) { $commandArgs += @("--end-page", $EndPage) }
if ($MaxPages -gt 0) { $commandArgs += @("--max-pages", $MaxPages) }
if ($DebugLlm) { $commandArgs += "--debug-llm" }

& $QwenPython @commandArgs
if ($LASTEXITCODE -ne 0) {
    throw "Batch VL extraction failed with exit code $LASTEXITCODE"
}

Write-Host "Done. Per-PDF JSON files are under: $(Join-Path $Output 'business_json_vl_llm')"
