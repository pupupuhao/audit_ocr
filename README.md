# 审价 PDF OCR 提取工具

本项目用于处理审价类 PDF：

1. PDF 转图片；
2. RapidOCR 快速识别每页文字；
3. 按表头黑白名单筛选目标页；
4. 对复杂表格页调用 PaddleOCR-VL；
5. 用规则做流程控制，用本地 Qwen 做表格字段提取；
6. 输出业务 JSON；
7. 将业务 JSON 汇总为 Excel。

当前主链路是：

```text
run_auto_vl_eval.py
→ run_vl_llm_extract.py
→ run_json_to_excel.py
```

## 环境

```bash
conda activate audit-ocr
pip install -r requirements.txt
```

Qwen/MLX 提取使用本地 `qwen-mlx` 环境运行时，请按本机已有环境配置执行。

## 主要入口

### 1. Auto VL OCR

先用 RapidOCR 快速扫页面，再只对命中目标表格的页面调用 PaddleOCR-VL。

```bash
python run_auto_vl_eval.py \
  --input input \
  --output output_auto_vl \
  --file "文件名.pdf" \
  --dpi 220
```

只跑指定页：

```bash
python run_auto_vl_eval.py \
  --input input \
  --output output_auto_vl \
  --file "文件名.pdf" \
  --dpi 220 \
  --start-page 23 \
  --end-page 23
```

输出：

```text
output_auto_vl/pages/{pdf_name}/page_001.png
output_auto_vl/rapid_screen_ocr/{pdf_name}/page_001_rapid_ocr.json
output_auto_vl/page_texts/{pdf_name}/page_001_text.txt
output_auto_vl/visual/{pdf_name}/page_001_ocr_boxes.png
output_auto_vl/vl/{pdf_name}/page_001_vl.json
output_auto_vl/vl/{pdf_name}/page_001_vl.md
output_auto_vl/vl/{pdf_name}/page_001_vl.html
output_auto_vl/reports/{pdf_name}_auto_vl_summary.json
```

### 2. 补齐 RapidOCR 文本

如果某些旧输出目录缺少 `page_texts/` 或 `rapid_screen_ocr/`，用这个脚本补齐。

```bash
python run_fill_rapid_texts.py \
  --input input \
  --output output_auto_vl \
  --file "文件名.pdf" \
  --dpi 200
```

默认不会覆盖已有文本。需要覆盖时加：

```bash
--overwrite
```

### 3. VL + Qwen 提取业务 JSON

读取已有 `output_auto_vl`，不重新跑 OCR。

```bash
conda run -n qwen-mlx python run_vl_llm_extract.py \
  --vl-output output_auto_vl \
  --output output_business_json \
  --file "文件名" \
  --prefer-source md \
  --debug-llm
```

只更新某一页：

```bash
conda run -n qwen-mlx python run_vl_llm_extract.py \
  --vl-output output_auto_vl \
  --output output_business_json \
  --file "文件名" \
  --prefer-source md \
  --start-page 23 \
  --end-page 23 \
  --debug-llm
```

注意：每个续行参数前一行末尾要保留 `\`，否则 `--start-page` 不会传入脚本。

输出：

```text
output_business_json/business_json_vl_llm/{pdf_name}/business_extract.json
output_business_json/reports/{pdf_name}_vl_llm_extract_summary.json
output_business_json/debug_llm/{pdf_name}/...
```

### 4. JSON 转 Excel

一行对应一个 `sub_project_id`。同一份 JSON 内，文件级字段会重复填充到每个子项目行。

```bash
python run_json_to_excel.py \
  --input output_business_json/business_json_vl_llm \
  --output output_business_json/audit_ocr_export.xlsx
```

也可以只转单个 JSON：

```bash
python run_json_to_excel.py \
  --input output_business_json/business_json_vl_llm/文件名/business_extract.json \
  --output output_business_json/one_file.xlsx
```

Excel 主要字段：

```text
file_name
sub_project_name
consultation_project_name
renovation_content
sub_project_id
parent_project
unit_project_fee_rows
sub_item_project_rows
specialty_fee_rows
quantity_confirm_rows
labor_rows
material_rows
machine_rows
construction_processes_content
source_json
```

## 目标表格

白名单：

```text
单位（专业）工程招标控制价费用表
分部分项工程清单与计价表
专业工程费用表
专业费用表
主要工日一览表
主要材料和工程设备一览表
主要机械台班一览表
工程量确认单
```

黑名单：

```text
咨询报告书目录
工程造价审定单
工程结算审核造价汇总表
现场踏勘记录表
工程现场勘察签到单
```

## 提取结果结构

`business_extract.json` 主要包含：

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

`sub_projects` 按表头里的工程名称归类：

```text
相同 sub_project_id 的单位费用表和分部分项表归到同一子项目；
不同 sub_project_id 分成不同子项目；
parent_project 写入 PDF 级 consultation_project_name。
```

## 当前边界

当前只做 OCR/VL 结果提取和结构化整理，不做：

```text
金额校验
公式重算
人工复核清单
向量库入库
RAG 检索
业务规则自动修正金额
```

部分规则会清理 OCR 结构噪声，例如表头断行、页码/表头字段过滤、施工工艺目录误识别过滤；不会对金额和业务含义做推断补全。

## 保留脚本说明

```text
run_auto_vl_eval.py       当前 OCR/VL 主入口
run_vl_llm_extract.py     当前业务 JSON 主入口
run_json_to_excel.py      JSON 转 Excel
run_fill_rapid_texts.py   补齐 page_texts / rapid_screen_ocr
run_batch_ocr.py          批量跑 Auto VL / Direct VL 的辅助入口
run_vl_eval.py            Direct VL 调试入口
```

`src/` 内保留的是上述入口仍在使用的公共模块。
