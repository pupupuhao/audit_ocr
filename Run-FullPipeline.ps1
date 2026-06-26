<#
.SYNOPSIS
Run the full audit OCR pipeline: Auto-VL, Qwen extraction, one JSON bundle.

.DESCRIPTION
This script orchestrates the existing project entrypoints:
  1. run_batch_ocr.py
  2. run_vl_llm_extract_transformers.py
  3. one combined business JSON file

Per-PDF business JSON files are still kept under:
  <Output>\business_json_vl_llm\<PDF folder>\business_extract.json

The script also writes one combined JSON file:
  <Output>\business_extract_all.json
#>
[CmdletBinding()]
param(
    [Alias("Input")]
    [string]$InputPath = ".\input",
    [string]$VlOutput = ".\output_auto_vl_batch",
    [string]$Output = ".\audit_ocr_vl_llm_json",
    [string]$CombinedJson = "",
    [int]$Dpi = 180,
    [string[]]$File,
    [string]$FileList = "",
    [string[]]$Page,
    [int]$StartPage = 0,
    [int]$EndPage = 0,
    [int]$MaxPages = 0,
    [ValidateSet("auto", "html", "md", "json", "all")]
    [string]$PreferSource = "md",
    [string]$OcrPython = "python",
    [string]$QwenPython = "D:\Users\LEE\anaconda3\envs\qwen-transformers\python.exe",
    [switch]$DebugLlm,
    [switch]$SkipOcr,
    [switch]$SkipExtract
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSCommandPath
$ocrRunner = Join-Path $repoRoot "run_batch_ocr.py"
$extractRunner = Join-Path $repoRoot "run_vl_llm_extract_transformers.py"
$businessJsonRoot = Join-Path $Output "business_json_vl_llm"

if ([string]::IsNullOrWhiteSpace($CombinedJson)) {
    $CombinedJson = Join-Path $Output "business_extract_all.json"
}

function Assert-File {
    param(
        [string]$Path,
        [string]$Label
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label not found: $Path"
    }
}

function Invoke-Step {
    param(
        [string]$Label,
        [string]$Exe,
        [string[]]$Args
    )
    Write-Host ""
    Write-Host "### $Label"
    Write-Host "$Exe $($Args -join ' ')"
    & $Exe @Args
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

function Add-CommonPageArgs {
    param([string[]]$Args)
    $next = @($Args)
    if ($Page) {
        foreach ($pageItem in $Page) {
            if (-not [string]::IsNullOrWhiteSpace($pageItem)) {
                $next += @("--page", $pageItem)
            }
        }
    }
    if ($StartPage -gt 0) { $next += @("--start-page", [string]$StartPage) }
    if ($EndPage -gt 0) { $next += @("--end-page", [string]$EndPage) }
    if ($MaxPages -gt 0) { $next += @("--max-pages", [string]$MaxPages) }
    return $next
}

function Get-SelectedExtractFileNames {
    $selected = @()
    if ($File) {
        foreach ($fileItem in $File) {
            if (-not [string]::IsNullOrWhiteSpace($fileItem)) {
                $selected += [System.IO.Path]::GetFileNameWithoutExtension($fileItem)
            }
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($FileList)) {
        if (-not (Test-Path -LiteralPath $FileList -PathType Leaf)) {
            throw "File list not found: $FileList"
        }
        $lines = Get-Content -LiteralPath $FileList -Encoding UTF8
        foreach ($line in $lines) {
            $trimmed = [string]$line
            $trimmed = $trimmed.Trim()
            if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
                continue
            }
            $selected += [System.IO.Path]::GetFileNameWithoutExtension($trimmed)
        }
    }
    return @($selected | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique)
}

function Write-CombinedBusinessJson {
    param(
        [string]$InputRoot,
        [string]$OutputPath
    )
    if (-not (Test-Path -LiteralPath $InputRoot -PathType Container)) {
        throw "Business JSON folder not found: $InputRoot"
    }

    $jsonFiles = @(Get-ChildItem -LiteralPath $InputRoot -Recurse -File -Filter "business_extract.json" | Sort-Object FullName)
    if ($jsonFiles.Count -eq 0) {
        throw "No business_extract.json files found under: $InputRoot"
    }

    $items = @()
    foreach ($jsonFile in $jsonFiles) {
        $payload = Get-Content -LiteralPath $jsonFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        $item = [ordered]@{
            source_json = $jsonFile.FullName
        }
        foreach ($property in $payload.PSObject.Properties) {
            $item[$property.Name] = $property.Value
        }
        $items += [PSCustomObject]$item
    }

    $bundle = [PSCustomObject]@{
        file_count = $items.Count
        generated_at = (Get-Date).ToString("s")
        files = $items
    }

    $parent = Split-Path -Parent $OutputPath
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    $bundle | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
    Write-Host "Combined JSON: $OutputPath"
}

Assert-File -Path $ocrRunner -Label "OCR runner"
Assert-File -Path $extractRunner -Label "Qwen extraction runner"
if (-not $SkipExtract) {
    Assert-File -Path $QwenPython -Label "Qwen Python"
}

Write-Host "Full pipeline"
Write-Host "Input:         $InputPath"
Write-Host "VL output:     $VlOutput"
Write-Host "JSON output:   $Output"
Write-Host "Combined JSON: $CombinedJson"

if (-not $SkipOcr) {
    $ocrArgs = @(
        $ocrRunner,
        "--input", $InputPath,
        "--output", $VlOutput,
        "--dpi", [string]$Dpi
    )
    if ($File) {
        foreach ($fileItem in $File) {
            if (-not [string]::IsNullOrWhiteSpace($fileItem)) {
                $ocrArgs += @("--file", $fileItem)
            }
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($FileList)) {
        $ocrArgs += @("--file-list", $FileList)
    }
    $ocrArgs = Add-CommonPageArgs -Args $ocrArgs
    Invoke-Step -Label "1/3 Auto-VL OCR" -Exe $OcrPython -Args $ocrArgs
}

if (-not $SkipExtract) {
    $selectedExtractFiles = Get-SelectedExtractFileNames
    if ($selectedExtractFiles.Count -gt 0) {
        $itemIndex = 0
        foreach ($selectedFile in $selectedExtractFiles) {
            $itemIndex += 1
            $extractArgs = @(
                $extractRunner,
                "--vl-output", $VlOutput,
                "--output", $Output,
                "--prefer-source", $PreferSource,
                "--file", $selectedFile
            )
            $extractArgs = Add-CommonPageArgs -Args $extractArgs
            if ($DebugLlm) { $extractArgs += "--debug-llm" }
            Invoke-Step -Label "2/3 Qwen business extraction ($itemIndex/$($selectedExtractFiles.Count))" -Exe $QwenPython -Args $extractArgs
        }
    } else {
        $extractArgs = @(
            $extractRunner,
            "--vl-output", $VlOutput,
            "--output", $Output,
            "--prefer-source", $PreferSource
        )
        $extractArgs = Add-CommonPageArgs -Args $extractArgs
        if ($DebugLlm) { $extractArgs += "--debug-llm" }
        Invoke-Step -Label "2/3 Qwen business extraction" -Exe $QwenPython -Args $extractArgs
    }
}

Write-Host ""
Write-Host "### 3/3 Build one JSON bundle"
Write-CombinedBusinessJson -InputRoot $businessJsonRoot -OutputPath $CombinedJson

Write-Host ""
Write-Host "Done."
Write-Host "Per-PDF JSON:   $businessJsonRoot"
Write-Host "Combined JSON:  $CombinedJson"
