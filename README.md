# 审价 PDF OCR 与结构化提取

本项目将审价 PDF 转为页面图像和 OCR 文本，仅对需要的计价表调用 PaddleOCR-VL，再使用本地 Qwen 生成业务 JSON。Excel 可用单独脚本按需导出。

当前推荐流程：

```text
PDF 批量扫描（RapidOCR + Auto-VL）
    → 已有 OCR/VL 结果批量 Qwen 提取
    → business_extract.json
    → 单文件 JSON 汇总
```

## 部署指南（Windows + NVIDIA GPU）

为避免 PaddleOCR 与 PyTorch/Qwen 的 CUDA 依赖冲突，使用两个 Conda 环境。

| 环境 | 用途 |
| --- | --- |
| `audit-ocr` | PDF 渲染、RapidOCR、PaddleOCR-VL、JSON 汇总、Excel 导出 |
| `qwen-transformers` | Qwen2.5-7B-Instruct 4-bit GPU 提取 |

### 1. 获取代码

```powershell
git clone https://github.com/pupupuhao/audit_ocr.git audit-ocr
Set-Location .\audit-ocr
```

### 2. 创建 OCR 环境

建议使用 Python 3.10：

```powershell
conda create -n audit-ocr python=3.10 -y
conda activate audit-ocr
python -m pip install --upgrade pip
python -m pip install -r .\requirements.txt
```

`requirements.txt` 使用 PaddlePaddle CUDA 12.9 wheel。Blackwell 显卡需要 NVIDIA 驱动支持 CUDA 12.9 或更新版本。

### 3. 放置 RapidOCR 模型

模型文件不进入 Git，需要放到以下目录：

```text
.\models\onnx\ppocrv5_mobile_det.onnx
.\models\onnx\ppocrv5_mobile_rec.onnx
.\models\onnx\ppocrv5_mobile_rec_keys.txt
```

也可以使用 server 模型，但需在执行命令时通过 `--det-model-path`、`--rec-model-path`、`--rec-keys-path` 指定。

PaddleOCR-VL 模型会在首次运行时自动下载并缓存到：

```text
%USERPROFILE%\.paddlex\official_models\PaddleOCR-VL-1.5
```

### 4. 创建 Qwen GPU 环境

```powershell
conda create -n qwen-transformers python=3.10 -y
conda activate qwen-transformers

# CUDA 12.8 PyTorch；与 RTX 50 系列环境配套。
python -m pip install torch==2.11.0+cu128 --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r .\requirements_transformers_llm.txt
```

验证 Qwen 环境是否能看到 GPU：

```powershell
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

预期第二行输出 `True`。Qwen 模型会在第一次提取时自动下载到 Hugging Face 缓存；需预留约 6 GB 或更多磁盘空间。

批处理脚本需要指向 `qwen-transformers` 环境的 Python；可通过 `-QwenPython` 参数指定：

```text
<Conda安装目录>\envs\qwen-transformers\python.exe
```

### 5. 运行前检查

```powershell
conda activate audit-ocr
Set-Location .\audit-ocr
python -c "import paddle; import onnxruntime as ort; print(paddle.__version__); print(ort.get_available_providers())"
```

`CUDAExecutionProvider` 出现在 ONNX Runtime providers 中，表示 RapidOCR 可使用 GPU。运行 VL/Qwen 时可另开窗口执行 `nvidia-smi -l 1` 观察显存和 GPU 利用率。

PowerShell 若禁止运行批处理脚本，可仅在当前窗口放行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## 0. 一键完整链路

运行 Auto-VL、Qwen 提取和单文件 JSON 汇总：

```powershell
conda activate audit-ocr
Set-Location <项目目录>

.\Run-FullPipeline.ps1 `
  -Input ".\input" `
  -VlOutput ".\output_auto_vl_batch" `
  -Output ".\audit_ocr_vl_llm_json" `
  -QwenPython "<Conda安装目录>\envs\qwen-transformers\python.exe"
```

默认产出：

```text
.\audit_ocr_vl_llm_json\business_json_vl_llm\<PDF名>\business_extract.json
.\audit_ocr_vl_llm_json\business_extract_all.json
```

指定页码可直接贯穿 VL 和提取：

```powershell
.\Run-FullPipeline.ps1 `
  -Input ".\input" `
  -Page 14 `
  -QwenPython "<Conda安装目录>\envs\qwen-transformers\python.exe"
```

Linux 服务器可通过环境变量调整 PaddleOCR-VL 后端：

```bash
export AUDIT_OCR_VL_DEVICE=gpu:0
export AUDIT_OCR_VL_USE_QUEUES=0
# 如 paddle_dynamic 推理异常，可切换官方支持的 transformers 引擎
export AUDIT_OCR_VL_ENGINE=transformers
```

## 1. 批量 OCR / Auto-VL

推荐使用批量入口。它固定使用 Auto-VL：每页都会输出 RapidOCR、`page_texts` 和框图；只有命中目标表的页面才会调用耗时的 PaddleOCR-VL。

```powershell
conda activate audit-ocr
Set-Location <项目目录>

python run_batch_ocr.py `
  --input ".\input" `
  --output ".\output_auto_vl_batch" `
  --dpi 180
```

也可以只扫描一份 PDF：

```powershell
python run_auto_vl_eval.py `
  --file ".\input\文件名.pdf" `
  --output ".\output_auto_vl" `
  --dpi 180
```

指定页码：

```powershell
python run_auto_vl_eval.py `
  --file ".\input\文件名.pdf" `
  --output ".\output_auto_vl" `
  --dpi 180 `
  --start-page 14 `
  --end-page 14
```

每份 PDF 的输出结构：

```text
<output>/pages/<PDF名>/page_001.png
<output>/rapid_screen_ocr/<PDF名>/page_001_rapid_ocr.json
<output>/rapid_screen_ocr/<PDF名>/page_001_rapid_ocr.txt
<output>/page_texts/<PDF名>/page_001_text.json
<output>/page_texts/<PDF名>/page_001_text.txt
<output>/visual/<PDF名>/page_001_ocr_boxes.png
<output>/vl/<PDF名>/page_001_vl.json        # 仅 VL 命中页
<output>/vl/<PDF名>/page_001_vl.md          # 仅 VL 命中页
<output>/vl/<PDF名>/page_001_vl.html        # 仅 VL 命中页
<output>/reports/<PDF名>_auto_vl_summary.json
<output>/reports/all_files_auto_vl_summary.json
```

旧输出缺少 RapidOCR 或 `page_texts` 时，可用：

```powershell
python run_fill_rapid_texts.py --input ".\input" --output ".\output_auto_vl" --dpi 180
```

## 2. 批量提取已有 VL 结果

不重新扫描 PDF。脚本读取 `<VlOutput>\vl` 下的所有 PDF 文件夹，逐份运行 Qwen，并写入同一个业务 JSON 根目录。

```powershell
Set-Location <项目目录>

.\Run-VlLlmBatch.ps1 `
  -VlOutput ".\output_auto_vl_batch" `
  -Output ".\audit_ocr_vl_llm_json" `
  -QwenPython "<Conda安装目录>\envs\qwen-transformers\python.exe"
```

每份 PDF 生成：

```text
.\audit_ocr_vl_llm_json\business_json_vl_llm\<PDF名>\business_extract.json
.\audit_ocr_vl_llm_json\reports\<PDF名>_vl_llm_extract_summary.json
```

调试 Qwen 提示词与响应：

```powershell
.\Run-VlLlmBatch.ps1 -DebugLlm
```

单文件提取也可以直接运行 Windows/NVIDIA 版本：

```powershell
conda run -n qwen-transformers python .\run_vl_llm_extract_transformers.py `
  --vl-output ".\output_auto_vl" `
  --output ".\audit_ocr_vl_llm_json" `
  --file "文件名" `
  --prefer-source md `
  --debug-llm
```

运行时 Qwen 会在 CUDA 可用时使用 GPU、4-bit NF4 量化和 `device_map="auto"`。可用 `nvidia-smi -l 1` 观察显存与 GPU 利用率。

## 3. JSON 转 Excel

```powershell
conda activate audit-ocr

python run_json_to_excel.py `
  --input ".\audit_ocr_vl_llm_json\business_json_vl_llm" `
  --output ".\audit_ocr_vl_llm_json\audit_ocr_export.xlsx"
```

## 当前 VL 与提取范围

当前会送入 VL、并参与 Qwen 表格提取的核心表格：

```text
分部分项工程清单与计价表
```

以下类型仍保留 RapidOCR 与 `page_texts`，但当前不跑 VL，也不送入 Qwen 表格提取：

```text
单位（专业）工程招标控制价费用表 / 单位（专业）工程费用表
专业工程费用表 / 专业费用表
主要工日一览表
主要材料和工程设备一览表
主要机械台班一览表
工程量确认单
施工工艺
```

表格提取禁用项位于 `run_vl_llm_extract.py` 的 `DISABLED_RULE_TABLE_TYPES`。恢复某一类时，将该类型对应行注释掉即可。施工工艺提取由同文件的 `ENABLE_CONSTRUCTION_PROCESS_EXTRACTION = False` 控制；设为 `True` 可恢复。

## 结果结构

`business_extract.json` 主要字段：

```text
file_name
document_info
sub_projects
specialty_fee_rows
quantity_confirm_rows
labor_rows
material_rows
machine_rows
construction_processes
pages
```

`sub_projects` 以分部分项工程清单与计价表中的工程名称/楼栋编号归并。工程名称后的表头行会跳过标段、页码与列名，并兼容 `42#`、`1-15幢`、`1101室` 等独立编号行。

## 主要脚本

```text
Run-FullPipeline.ps1                  一键 Auto-VL、Qwen 提取、汇总 JSON
run_full_pipeline_gpu.sh              Linux 服务器一键 Auto-VL、Qwen 提取、汇总 JSON
run_batch_ocr.py                     批量 Auto-VL OCR
run_auto_vl_eval.py                  单文件/指定页 Auto-VL OCR
Run-VlLlmBatch.ps1                   批量提取已有 OCR/VL 结果
run_vl_llm_extract_transformers.py  Windows/NVIDIA Qwen 提取入口
run_vl_llm_extract.py                提取核心逻辑（保留原 MLX 路径）
run_fill_rapid_texts.py              补齐旧输出的 RapidOCR/page_texts
run_json_to_excel.py                 业务 JSON 转 Excel
```
