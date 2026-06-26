from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MODEL_PATH = "mlx-community/Qwen2.5-7B-Instruct-4bit"
EXTRACT_TEXT_LIMIT = 12000
CONSTRUCTION_PROCESS_TEXT_LIMIT = 9000
LONG_CELL_TABLE_TYPES = {"sub_item_project_table", "quantity_confirm_table"}

_model = None
_tokenizer = None
_debug_dir: Path | None = None
_debug_counter = 0


def set_debug_dir(path: str | Path | None) -> None:
    global _debug_dir, _debug_counter
    _debug_counter = 0
    _debug_dir = Path(path) if path else None
    if _debug_dir:
        _debug_dir.mkdir(parents=True, exist_ok=True)


def _format_qwen_cell(value: Any, max_cell_chars: int = 500) -> str:
    text = str(value or "").strip().replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.replace("\n", "\\n")
    if len(text) > max_cell_chars:
        text = text[:max_cell_chars] + "...[cell_truncated]"
    return text.replace('"', '\\"')


def _top_level_seq_from_cell(value: Any) -> str:
    text = str(value or "").strip()
    match = re.match(r"^([1-7])(?:$|[^\d.])", text)
    return match.group(1) if match else ""


def _max_cell_chars_for_table(table_type: str) -> int:
    return 1200 if table_type in LONG_CELL_TABLE_TYPES else 500


def _render_qwen_row(row_index: int, row: list[Any], max_cols: int, max_cell_chars: int = 500) -> str:
    cells = []
    for col_index in range(1, max_cols + 1):
        value = row[col_index - 1] if col_index - 1 < len(row) else ""
        cells.append(f'col_{col_index:03d}="{_format_qwen_cell(value, max_cell_chars=max_cell_chars)}"')
    return f"row_{row_index:03d}: " + " | ".join(cells)


def _unit_fee_candidate_lines(grid: list[Any], max_cols: int, max_cell_chars: int = 500) -> list[str]:
    lines = [
        "TOP_LEVEL_CANDIDATE_ROWS_FOR_UNIT_FEE",
        "说明：以下候选行是从原始表格中按col_001筛出的顶层序号行；字段提取时优先输出这些行，完整表格在后面仅用于核对列内容。",
    ]
    for row_index, row in enumerate(grid, start=1):
        if not isinstance(row, list):
            row = [row]
        first_cell = row[0] if row else ""
        if _top_level_seq_from_cell(first_cell):
            lines.append(_render_qwen_row(row_index, row, max_cols, max_cell_chars=max_cell_chars))
    if len(lines) == 2:
        return []
    return lines


def table_to_qwen_text(
    table: dict[str, Any],
    limit: int = EXTRACT_TEXT_LIMIT,
    table_type: str = "",
) -> str:
    """Render a table for Qwen while preserving row/column positions and empty cells."""
    grid = table.get("grid", [])
    if not isinstance(grid, list) or not grid:
        return ""

    max_cols = max((len(row) for row in grid if isinstance(row, list)), default=0)
    max_cell_chars = _max_cell_chars_for_table(table_type)
    header = [
        "TABLE_GRID_FOR_EXTRACTION",
        f"source_type={table.get('source_type', table.get('source', ''))}",
        f"source_path={table.get('source_path', '')}",
        f"table_index={table.get('table_index', table.get('table_no', ''))}",
        f"row_count={len(grid)}",
        f"column_count={max_cols}",
        "说明：以下内容保留原表格的行号、列号和空单元格；空值表示该列原本为空，不要自行左移或合并列。",
    ]
    if table_type == "unit_project_fee_table":
        header.extend(_unit_fee_candidate_lines(grid, max_cols, max_cell_chars=max_cell_chars))
    elif table_type == "sub_item_project_table":
        header.extend(
            [
                "SUB_ITEM_AMOUNT_COLUMN_GUIDE",
                "金额列从左到右通常为：综合单价、合价、人工费、机械费、暂估价、备注。",
                "人工费必须进入labor_cost；机械费必须进入machinery_cost；暂估价必须进入provisional_estimate。不要把人工费或机械费填入provisional_estimate。",
            ]
        )
    lines: list[str] = []
    budget = max(limit, 1000)
    used = 0

    for line in header:
        if used + len(line) + 1 > budget:
            break
        lines.append(line)
        used += len(line) + 1

    for row_index, row in enumerate(grid, start=1):
        if not isinstance(row, list):
            row = [row]
        line = _render_qwen_row(row_index, row, max_cols, max_cell_chars=max_cell_chars)
        if used + len(line) + 1 > budget:
            marker = f"...[table_truncated_before_row_{row_index:03d}]"
            if used + len(marker) + 1 <= budget:
                lines.append(marker)
            break
        lines.append(line)
        used += len(line) + 1

    return "\n".join(lines)


SYSTEM_PROMPT = (
    "你是审计报告OCR字段提取器。输入来自OCR表格grid、VL表格文本或页面OCR文本。"
    "你的任务是把已有OCR内容映射到指定JSON字段，不要根据常识补字段，不要纠正金额，"
    "当输入包含row_xxx/col_xxx时，必须按列号理解表格，空列也表示真实位置，不要把后续列左移。"
    "不要合并不存在的行，不要从说明文字推断数据。只输出纯JSON数组或对象，"
    "不要输出markdown标记、解释或其他内容。字段缺失填空字符串\"\"。"
)


def _get_model():
    global _model, _tokenizer
    if _model is None:
        from mlx_lm import load
        _model, _tokenizer = load(MODEL_PATH)
    return _model, _tokenizer


def _debug_write(kind: str, text: str) -> None:
    global _debug_counter
    if not _debug_dir:
        return
    _debug_counter += 1
    safe_kind = re.sub(r"[^a-zA-Z0-9_-]+", "_", kind).strip("_") or "llm"
    path = _debug_dir / f"{_debug_counter:04d}_{safe_kind}.txt"
    path.write_text(text, encoding="utf-8")


def call_llm(prompt: str, system: str = SYSTEM_PROMPT, max_tokens: int = 4096, debug_label: str = "llm") -> str:
    model, tokenizer = _get_model()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    formatted = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    _debug_write(f"{debug_label}_prompt", formatted)
    from mlx_lm import generate
    response = generate(model, tokenizer, prompt=formatted, max_tokens=max_tokens)
    response = response.strip()
    _debug_write(f"{debug_label}_response", response)
    return response


EXTRACT_PROMPTS = {
    "unit_project_fee_table": """从以下单位（专业）工程费用表或单位（专业）工程招标控制价费用表OCR表格行中提取字段。

表格结构说明：
- 当前表格可能是：《单位（专业）工程费用表》或《单位（专业）工程招标控制价费用表》
- 表格首行通常依次包含：
  1. 工程名称：[具体工程名称文本]
  2. 标段：通常为空
  3. 页码：例如“第1页 共1页”
- 列结构从左到右通常为：
  1. 序号
  2. 费用名称
  3. 计算公式
  4. 金额（元）
  5. 备注
- 序号采用数字及点号层级表示费用父子关系，例如：1、1.1、2.1.1。
- “其中”行表示该层级费用的子项或组成部分，不作为顶层费用输出。
- 计算公式列可能包含数学表达式、引用其他序号或固定百分比。
- 招标控制价费用表的计算公式可能带有带圈数字编号，例如①、②、③、㉑、㉔；这些编号属于公式原文，应保留在formula中，不要删除。
- 备注列可能引用外部表格编号，例如“见表10.2.2-16”，也可能为空。

常见顶层数据行：
- 1：分部分项工程费，计算公式通常为“Σ（分部分项工程量×综合单价）”
- 2：措施项目费，计算公式通常为“(2.1+2.2)”
- 3：其他项目费，计算公式通常为“(3.1+3.2+3.3+3.4)”
- 4：规费，计算公式通常包含固定费率
- 普通费用表中，5通常是税金。
- 招标控制价费用表中，5可能是单列费用，6可能是税金，7可能是下浮。
- 不是每张表都包含1-7全部顶层行；只提取OCR表格中实际出现的顶层序号，不要补齐缺失序号。
- 末行“招标控制价合计”或类似无序号汇总行不是费用明细行，默认不要输出。

招标控制价费用表的完整结构示例：
- 1：分部分项工程费；1.1通常是“其中：人工费+机械费”，不输出。
- 2：措施项目费；2.1、2.1.1、2.2、2.2.1是子项，不输出。
- 3：其他项目费；3.1、3.1.1、3.1.2、3.1.3、3.2、3.2.1、3.2.2、3.2.3、3.3、3.4、3.4.1、3.4.2是子项，不输出。
- 4：规费。
- 5：单列费用。
- 6：税金。
- 7：下浮。
- 后续无序号“其中”、其下“甲供（不计入造价）”、末行“招标控制价合计”均不是顶层费用明细，不输出。

输出JSON数组，每个元素：
{{"seq": "序号", "fee_name": "费用名称", "formula": "计算公式", "amount": "金额（纯数字）", "remark": "备注"}}

注意：
- 序号可能是纯数字"1", "2"或是"2 措施项目费"合并格式，需分开为seq和fee_name
- seq字段只允许输出以下7个值之一："1"、"2"、"3"、"4"、"5"、"6"、"7"。
- 禁止输出任何带点号的seq，例如"1.1"、"2.1"、"2.2"、"3.1"、"3.2"、"3.3"、"3.4"、"3.4.1"都不能出现在JSON里。
- fee_name来自“费用名称”列
- formula来自“计算公式”列
- amount来自“金额（元）”列
- remark来自“备注”列
- 金额只保留数字和小数点
- 只提取序号1-7的大类行，跳过1.1、2.1、2.2、3.1、3.2、3.3、3.4等子行和"其中"行
- 如果输入中包含 TOP_LEVEL_CANDIDATE_ROWS_FOR_UNIT_FEE，必须优先按该候选区中的行输出；后面的完整表格只用于核对列内容，不能从完整表格追加3.1、3.2、3.3、3.4等子行。
- 顶层序号行只要在OCR表格中出现就必须输出，即使金额为空也要输出，例如“3 其他项目费”的amount可以是空字符串。
- 不要因为“3 其他项目费”下面存在3.1、3.2、3.3、3.4等子项，就跳过seq=3这条顶层行。
- 不要因为某个顶层行的金额为空、公式为空或备注为空，就跳过该顶层行；缺失字段填空字符串。
- 若只出现1、2、3、4、6等部分顶层序号，就只输出这些实际出现的行；不要为了凑齐1-7而生成空行。
- 跳过表头、页码行、工程名称行、无序号汇总行
- 不要从表外说明文字推断费用行
- 不要补全OCR里没有出现的金额

输出示例约束：
- 如果OCR表中同时出现“3 其他项目费”和“3.1 暂列金额 / 3.2 暂估价 / 3.3 计日工 / 3.4 施工总承包服务费”，只输出seq="3"这一条，不能输出3.1、3.2、3.3、3.4。
- 错误输出示例：[{{"seq":"3.1","fee_name":"暂列金额"}}, {{"seq":"3.2","fee_name":"暂估价"}}]
- 正确输出示例：[{{"seq":"3","fee_name":"其他项目费","formula":"(3.1+3.2+3.3+3.4)","amount":"","remark":""}}]

OCR表格行：
{table_text}""",

    "sub_item_project_table": """从以下分部分项工程清单与计价表OCR表格行中提取字段。

表格结构说明：
- 当前表格名称：《分部分项工程清单与计价表》
- 表格首行通常包含：单位（专业）工程名称：[具体工程名称文本]；但当前输入也可能是跨页后的续表页，续表页可能没有完整表名、工程名称或完整多级表头。
- 列结构从左到右通常为：
  1. 序号
  2. 项目编码
  3. 项目名称
  4. 项目特征
  5. 计量单位
  6. 工程量
  7. 标段（此列通常无内容，不提取）
  8. 金额（元）/综合单价
  9. 金额（元）/合价
  10. 金额（元）/其中/人工费
  11. 金额（元）/其中/机械费
  12. 金额（元）/暂估价
  13. 备注
- 数据行：每行对应一个分部分项工程项目。
- 跨页续表页的第一条数据行也必须正常提取，不要因为它靠近页眉、页码、表头或缺少完整表名就跳过。
- 表尾部分：“本页小计”和“合计”是汇总行，不是工程项目行，不要输出。
- 子项目汇总/标题行也不是工程项目行：这类行常位于表头后的首个非表头行，项目编码列可能是“37#”“51#”等编号；它们通常没有纯数字序号、项目名称、计量单位或工程量，只包含合价、人工费、机械费等汇总金额。必须跳过，不能把它当作项目编码或工程项目输出。
- 提取前必须先扫描完整个表格的所有 row_xxx，不能只处理表头后的第一条非空行。
- 扫描时先跳过分组行、楼栋行、汇总行、空行和表尾小计/合计行，然后继续向后扫描，不得在遇到第一个分组/汇总行后停止。
- 必须输出所有 col_001 为纯数字序号的行；只要 col_001 是纯数字，且该行包含项目名称、项目特征、单位、工程量或金额中的任一有效信息，就必须作为工程项目行输出。
- 如果 col_001 为 1、2、3... 的多条明细出现在分组/汇总行之后，必须全部输出，不能只输出前面的分组/汇总行。
- 最终输出前必须自检：如果输入中存在任意 col_001 为纯数字序号的行，而你的输出数组里没有任何 seq 为纯数字的对象，则说明提取错误，必须重新扫描并输出这些纯数字序号行。
- 禁止输出只有一条且 seq 为空的楼栋/分组/汇总行，例如“三北新村49幢”“超市变电所”“76幢”“1栋”；这类行必须跳过，不能作为唯一结果。
- 当输入是 TABLE_GRID_FOR_EXTRACTION 且包含 row_xxx/col_xxx 时，只把 col_001 为纯数字序号，或 col_002 像清单/定额项目编码的行当作工程项目行。项目编码通常形如“010903002001”“01B022”“9-81”“14-147”；“76幢”“1栋”“道路应急值班”“超市变电所”这类楼栋/分组/汇总名称不是项目编码，必须跳过。
- 如果 col_001 不是纯数字序号，且 col_002 不是项目编码，即使该行有楼栋名、分组名称或后面有金额，也不是工程项目行，必须跳过；跳过后要继续提取后续 col_001 为 1、2、3... 的真实明细行，不能因为第一条汇总行被跳过就停止。
- 不能仅因一行位于表格第一行而跳过：续表的首行若有纯数字序号，或有标准清单编码且有项目名称、单位、工程量等项目特征，仍是正常工程项目，必须提取。

输出JSON数组，每个元素：
{{"seq": "<纯数字序号>", "project_code": "<项目编码>", "project_name": "<项目名称>", "project_description": "<项目特征>", "unit": "<计量单位>", "quantity": "<工程量>", "unit_price": "<综合单价>", "total_price": "<合价>", "labor_cost": "<人工费>", "machinery_cost": "<机械费>", "provisional_estimate": "<暂估价>", "remark": "<备注>"}}

注意：
- project_code 来自“项目编码”列，项目编码可能是"010902001001"这类清单编码，也可能是"10-2-37"这类定额编号；不要因为包含短横线就忽略。
- project_name 来自“项目名称”列
- project_description 来自“项目特征”列
- unit 来自“计量单位”列
- quantity 来自“工程量”列
- unit_price 来自“综合单价”列
- total_price 来自“合价”列
- labor_cost 来自“人工费”列
- machinery_cost 来自“机械费”列
- provisional_estimate 来自“暂估价”列
- remark 只来自“备注”列
- 金额相关列的顺序必须按表头从左到右理解：综合单价 -> 合价 -> 人工费 -> 机械费 -> 暂估价 -> 备注。
- 当 TABLE_GRID_FOR_EXTRACTION 的多级表头因为 colspan/rowspan 出现列号错位时，以数据行中工程量右侧连续金额列为准：工程量后的第1个金额是unit_price，第2个金额是total_price，第3个金额是labor_cost，第4个金额是machinery_cost，第5个金额是provisional_estimate；不要因为表头文字出现在更靠后的空列而把数据行金额左/右移。
- 跨页续表如果表头不完整，但数据行包含工程量和多个金额，则工程量之后的金额通常按从左到右理解为：综合单价 -> 合价 -> 人工费 -> 机械费 -> 暂估价。
- 如果 total_price/合价 已经识别，unit_price/综合单价 优先取 total_price 左侧最近的金额；不要取 total_price 右侧的人工费或机械费。
- labor_cost只能来自“人工费”列；provisional_estimate只能来自“暂估价”列。禁止把人工费金额填入provisional_estimate。
- 如果“暂估价”列为空，provisional_estimate必须输出空字符串，即使人工费列有金额也不能填到provisional_estimate。
- 金额只保留数字和小数点
- seq只能是数据行第一列的纯数字，例如"1"、"2"；绝不能输出“序号”或其他表头文字。若第一列没有纯数字序号，seq填空字符串，不得用表头补值。
- 跳过表头行、本页小计行和合计行
- 只提取表格中有明确项目编码或明确序号的工程项目行
- 不要把页眉、表名、工程名称、单位工程名称提取成项目行

项目特征纠偏规则：
- “项目特征”可包含多条子特征，通常以 1.、2.、3. 或换行分隔。
- 同一个项目的多个编号特征必须全部放入project_description，例如“1.卷材品种、规格、厚度：3.0mmSBS沥青防水卷材”和“2.含屋面基层清理”都属于project_description。
- 判断新项目行：如果一行包含项目编码，或包含明确序号且同时有项目名称、计量单位、工程量或金额信息，则认为是新的工程项目行。
- 判断项目特征续行：如果一行没有项目编码、没有计量单位、没有工程量、没有金额，但在“项目名称”列或“项目特征”列出现文字，且上一条工程项目已经存在，则这行不是新项目，而是上一条工程项目的project_description续行。
- 续行归并：续行文本应追加到上一条工程项目的project_description；不要把续行当作新的project_name；不要把续行放入remark。
- 编号续行：以1.、2.、3.、1)、2)、3)或类似编号开头的文本，通常是项目特征的分条内容；如果该行不具备新项目行特征，应追加到上一条project_description。
- 如果OCR错位导致项目特征内容出现在remark中，但内容明显是材料、规格、厚度、遍数、基层处理、施工工艺、防水做法等，应放入project_description，不要放入remark。
- 如果remark内容以“1.”、“2.”、“3.”等编号开头，且描述基层清理、涂膜厚度、材料规格、拆除、修复、运输等项目做法，应迁回project_description，并将remark置空。
- 如果project_description已有内容，错位的项目特征内容应追加到project_description。
- 不要把价格、单位、工程量放入project_description。

金额字段规则：
- unit_price、total_price、labor_cost、machinery_cost、provisional_estimate只能来自对应金额列。
- 不要从项目名称、项目特征、备注中推断金额。

OCR表格行：
{table_text}""",

    "specialty_fee_table": """从以下专业工程费用表OCR表格行中提取字段。

表格结构说明：
- 当前表格名称通常为：《专业工程费用表》或《专业费用表》。
- 列结构从左到右通常为：
  1. 序号
  2. 工程名称
  3. 金额（元）
  4. 其中（元）/暂估价
  5. 其中（元）/安全文明施工基本费
  6. 其中（元）/规费
  7. 其中（元）/税金
  8. 备注
- “其中（元）”是跨列表头，不是数据字段；它下面的暂估价、安全文明施工基本费、规费、税金必须分别提取到独立字段。

输出JSON数组，每个元素：
{{"seq": "序号", "project_name": "工程名称", "amount": "金额", "provisional_estimate": "暂估价", "safety_civilization_fee": "安全文明施工基本费", "regulatory_fee": "规费", "tax": "税金", "remark": "备注"}}

注意：
- 只提取表格行，不要提取标题、页眉、合计说明
- project_name 来自“工程名称”列，不要把金额或其中列内容合并进project_name
- amount 来自“金额（元）”列
- provisional_estimate 来自“暂估价”列
- safety_civilization_fee 来自“安全文明施工基本费”列
- regulatory_fee 来自“规费”列
- tax 来自“税金”列
- remark 只来自“备注”列，不要把暂估价、安全文明施工基本费、规费、税金放入remark
- 金额字段只保留数字和小数点
- 如果某一列为空，对应字段填空字符串，不要挪到remark

OCR表格行：
{table_text}""",

    "labor_table": """从以下主要工日一览表OCR表格行中提取字段。

输出JSON数组，每个元素：
{{"seq": "序号", "name": "工日名称/类别（如一类人工）", "unit": "单位", "quantity": "数量", "unit_price": "单价", "total_price": "合价", "remark": "备注"}}

注意：
- 只提取有序号或明确工日名称的明细行
- 不要提取表头、合计、页眉
- 数量、单价、合价只保留原OCR数字

OCR表格行：
{table_text}""",

    "material_table": """从以下主要材料和工程设备一览表OCR表格行中提取字段。

输出JSON数组，每个元素：
{{"seq": "序号", "name_spec": "名称规格型号（合为一个字段）", "unit": "单位", "quantity": "数量", "unit_price": "单价", "total_price": "合价", "remark": "备注"}}

注意：
- 名称和规格可能在同一列或分列，合并到name_spec
- 只提取材料/设备明细行，不要提取表头、合计、页眉
- 不要把单位工程名称、工程名称提取为材料名称
- 数量、单价、合价只保留原OCR数字

OCR表格行：
{table_text}""",

    "machine_table": """从以下主要施工机械台班一览表OCR表格行中提取字段。

输出JSON数组，每个元素：
{{"seq": "序号", "name_spec": "名称规格型号", "unit": "单位", "quantity": "数量", "unit_price": "单价", "total_price": "合价", "remark": "备注"}}

注意：
- 只提取机械台班明细行，不要提取表头、合计、页眉
- 数量、单价、合价只保留原OCR数字

OCR表格行：
{table_text}""",

    "quantity_confirm_table": """从以下工程量确认单OCR表格行中提取字段。

表格结构说明：
- 当前表格名称：《工程量确认单》
- 列结构从左到右通常为：
  1. 序号
  2. 名称
  3. 维修内容
  4. 单位
  5. 预估工程量
  6. 备注
- 数据行：每行对应一个维修项目。
- OCR表格中每一条有序号的数据行都必须输出一条JSON；即使名称重复，只要序号不同或维修内容不同，也必须输出多条。
- 禁止把相邻数据行合并成一条。例如“屋面防水”、“天沟防水”、不同房号/楼栋必须分别输出。
- “维修内容”列可包含多条子维修工序，通常以逗号、顿号、分号或换行分隔。
- 表格末尾可能存在若干空行，例如序号10、11等但其他列为空白，这些空行不要输出。
- 表尾可能存在独立两行：
  1. 建设单位：[建设单位名称文本]
  2. 施工单位：[施工单位名称文本]
  表尾建设单位和施工单位不是数据行，不要输出。

输出JSON数组，每个元素：
{{"seq": "序号", "name": "名称", "repair_content": "维修内容", "unit": "单位", "formula": "计算式", "quantity": "工程量", "remark": "备注"}}

注意：
- 只提取有序号的明细行，跳过标题行、签字栏、审核说明
- 一个序号对应一个输出对象。不要把多个序号的维修内容拼接到同一个repair_content。
- 如果名称列重复但维修内容不同，也必须输出多条记录。
- 跳过空行、建设单位行、施工单位行
- name来自“名称”列
- repair_content来自“维修内容”列
- unit来自“单位”列
- quantity来自“预估工程量”列
- remark只来自“备注”列
- quantity必须来自工程量/数量列或同一行明确数字，不要用单价、金额代替
- 不要根据公式自行计算工程量
- 如果OCR错位导致维修内容的第二行被放到该行前面，仍应按语义合并到repair_content
- 如果OCR错位导致维修内容被放入remark，但内容明显是维修工序、施工做法、拆除/清理/修复/更换/安装等，应迁回repair_content，不要放入remark
- 不要把建设单位、施工单位、签字日期等表尾内容放入repair_content或remark

OCR表格行：
{table_text}""",
}


def parse_json_response(text: str) -> Any:
    text = text.strip()
    json_match = re.search(r"\[[\s\S]*\]", text)
    if json_match:
        text = json_match.group(0)
    else:
        obj_match = re.search(r"\{[\s\S]*\}", text)
        if obj_match:
            text = "[" + obj_match.group(0) + "]"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        cleaned = re.sub(r"```json\s*", "", text)
        cleaned = re.sub(r"```\s*", "", cleaned)
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
        cleaned = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", cleaned)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return None


CONSTRUCTION_PROCESS_CLEAN_PROMPT = """清理以下“施工工艺”OCR原文，并整理为JSON对象。

规则说明：
- 输入已经由规则层定位到“施工工艺”章节，可能跨页。
- 不要重新判断章节标题，不要从其他章节补充内容。
- 清理OCR噪声：去掉孤立的公司名残片、印章残片、签字栏残片、图片链接、HTML img标签、明显无关的单字噪声。
- 合并断行：同一句被OCR拆成多行时合并；但保留不同维修对象、小标题和编号步骤的层级。
- 支持内部小标题，例如“屋面防水：”“天沟防水：”“外墙防水：”“腰线防水”等；小标题后的编号可以重新从1开始。
- 不要删除工程量、单位、材料厚度、遍数、规格、房号、位置描述。
- 不要改写金额或工程量，不要根据常识补内容。
- 如果某些文字无法判断是否噪声，宁可保留在cleaned_content中。

输出JSON对象，格式：
{{
  "cleaned_content": "清理断行后的完整施工工艺文本",
  "structured_items": [
    {{
      "target": "维修对象/位置，如15栋1101室渗漏维修；没有则为空",
      "section": "内部小标题，如屋面防水；没有则为空",
      "steps": ["步骤1", "步骤2"]
    }}
  ]
}}

施工工艺OCR原文：
{text}"""


def _split_text_by_lines(text: str, limit: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current).strip())
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def clean_construction_process_text(text: str, debug_label: str = "construction_process") -> dict[str, Any]:
    if not text.strip():
        return {"cleaned_content": "", "structured_items": []}

    cleaned_parts: list[str] = []
    structured_items: list[dict[str, Any]] = []
    chunks = _split_text_by_lines(text, CONSTRUCTION_PROCESS_TEXT_LIMIT)
    for index, chunk in enumerate(chunks, start=1):
        prompt = CONSTRUCTION_PROCESS_CLEAN_PROMPT.format(text=chunk)
        raw = call_llm(
            prompt,
            max_tokens=4096,
            debug_label=f"{debug_label}_{index:02d}",
        )
        parsed = parse_json_response(raw)
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            parsed_obj = parsed[0]
        elif isinstance(parsed, dict):
            parsed_obj = parsed
        else:
            parsed_obj = {}

        cleaned = str(parsed_obj.get("cleaned_content", "") or "").strip()
        if cleaned:
            cleaned_parts.append(cleaned)
        else:
            cleaned_parts.append(chunk)

        items = parsed_obj.get("structured_items", [])
        if isinstance(items, list):
            structured_items.extend(item for item in items if isinstance(item, dict))

    return {
        "cleaned_content": "\n".join(cleaned_parts).strip(),
        "structured_items": structured_items,
    }


def _header_row_count_for_table(table_type: str) -> int:
    defaults = {
        "unit_project_fee_table": 4,
        "sub_item_project_table": 4,
        "quantity_confirm_table": 3,
    }
    return defaults.get(table_type, 3)


def _looks_like_data_row(row: list[Any], table_type: str) -> bool:
    cells = [str(cell or "").strip() for cell in row]
    if not any(cells):
        return False
    first = cells[0] if cells else ""
    joined = " ".join(cells)
    if table_type == "unit_project_fee_table":
        return bool(re.match(r"^[1-7](?:$|[^\d.])", first))
    if table_type == "sub_item_project_table":
        return bool(re.match(r"^\d+$", first) or re.search(r"\b\d{9,15}\b", joined))
    if table_type == "quantity_confirm_table":
        return bool(re.match(r"^\d+$", first))
    if table_type in {"labor_table", "material_table", "machine_table", "specialty_fee_table"}:
        return bool(re.match(r"^\d+$", first))
    return False


def _detect_header_row_count(grid: list[Any], table_type: str) -> int:
    fallback = min(_header_row_count_for_table(table_type), len(grid))
    for index, row in enumerate(grid):
        if not isinstance(row, list):
            row = [row]
        if _looks_like_data_row(row, table_type):
            return max(1, min(index, fallback))
    return fallback


def _chunk_table_rows(
    table: dict[str, Any],
    table_type: str,
    limit: int = EXTRACT_TEXT_LIMIT,
) -> list[dict[str, Any]]:
    grid = table.get("grid", [])
    if not isinstance(grid, list) or not grid:
        return [table]

    full_text = table_to_qwen_text(table, limit=limit, table_type=table_type)
    if "...[table_truncated_before_row_" not in full_text:
        return [table]

    header_count = _detect_header_row_count(grid, table_type)
    header_rows = grid[:header_count]
    data_rows = grid[header_count:]
    if not data_rows:
        return [table]

    chunks: list[dict[str, Any]] = []
    current_rows: list[Any] = []
    max_cell_chars = _max_cell_chars_for_table(table_type)
    max_cols = max((len(row) for row in grid if isinstance(row, list)), default=0)
    budget = max(limit - 1400, 2000)
    used = 0

    for row in data_rows:
        if not isinstance(row, list):
            row = [row]
        approx = len(_render_qwen_row(len(header_rows) + len(current_rows) + 1, row, max_cols, max_cell_chars=max_cell_chars)) + 1
        if current_rows and used + approx > budget:
            chunk = dict(table)
            chunk["grid"] = header_rows + current_rows
            chunk["chunk_info"] = {
                "chunk_index": len(chunks) + 1,
                "header_rows": header_count,
                "data_rows": len(current_rows),
                "source_row_start": header_count + sum(len(c.get("grid", [])) - header_count for c in chunks) + 1,
            }
            chunks.append(chunk)
            current_rows = []
            used = 0
        current_rows.append(row)
        used += approx

    if current_rows:
        chunk = dict(table)
        chunk["grid"] = header_rows + current_rows
        chunk["chunk_info"] = {
            "chunk_index": len(chunks) + 1,
            "header_rows": header_count,
            "data_rows": len(current_rows),
            "source_row_start": header_count + sum(len(c.get("grid", [])) - header_count for c in chunks) + 1,
        }
        chunks.append(chunk)

    return chunks or [table]


def extract_table_data(table: dict[str, Any], table_type: str) -> list[dict[str, Any]]:
    if table_type not in EXTRACT_PROMPTS:
        return []

    chunks = _chunk_table_rows(table, table_type, limit=EXTRACT_TEXT_LIMIT)
    all_rows: list[dict[str, Any]] = []
    for chunk_index, chunk in enumerate(chunks, start=1):
        table_text = table_to_qwen_text(chunk, limit=EXTRACT_TEXT_LIMIT, table_type=table_type)
        if not table_text.strip():
            continue

        chunk_info = chunk.get("chunk_info")
        if chunk_info:
            table_text = (
                f"TABLE_CHUNK_INFO chunk_index={chunk_info.get('chunk_index')} "
                f"data_rows={chunk_info.get('data_rows')} "
                f"source_row_start={chunk_info.get('source_row_start')}\n"
                f"{table_text}"
            )

        prompt = EXTRACT_PROMPTS[table_type].format(table_text=table_text)
        debug_label = table_type if len(chunks) == 1 else f"{table_type}_chunk_{chunk_index:02d}"
        raw = call_llm(prompt, debug_label=debug_label)

        result = parse_json_response(raw)
        if result and isinstance(result, list):
            all_rows.extend(r for r in result if isinstance(r, dict))
    return all_rows


FIELD_NORMALIZERS = {
    "unit_project_fee_table": {
        "seq": str,
        "fee_name": str,
        "formula": str,
        "amount": lambda x: re.sub(r"[^\d.]", "", str(x)),
        "remark": str,
    },
    "sub_item_project_table": {
        "seq": str,
        "project_code": str,
        "project_name": str,
        "project_description": str,
        "unit": str,
        "quantity": str,
        "unit_price": lambda x: re.sub(r"[^\d.]", "", str(x)),
        "total_price": lambda x: re.sub(r"[^\d.]", "", str(x)),
        "provisional_estimate": str,
        "labor_cost": str,
        "machinery_cost": str,
        "remark": str,
    },
    "labor_table": {
        "seq": str,
        "name": str,
        "unit": str,
        "quantity": str,
        "unit_price": lambda x: re.sub(r"[^\d.]", "", str(x)),
        "total_price": lambda x: re.sub(r"[^\d.]", "", str(x)),
        "remark": str,
    },
    "material_table": {
        "seq": str,
        "name_spec": str,
        "unit": str,
        "quantity": str,
        "unit_price": lambda x: re.sub(r"[^\d.]", "", str(x)),
        "total_price": lambda x: re.sub(r"[^\d.]", "", str(x)),
        "remark": str,
    },
    "machine_table": {
        "seq": str,
        "name_spec": str,
        "unit": str,
        "quantity": str,
        "unit_price": lambda x: re.sub(r"[^\d.]", "", str(x)),
        "total_price": lambda x: re.sub(r"[^\d.]", "", str(x)),
        "remark": str,
    },
    "quantity_confirm_table": {
        "seq": str,
        "name": str,
        "repair_content": str,
        "unit": str,
        "formula": str,
        "quantity": str,
        "remark": str,
    },
    "specialty_fee_table": {
        "seq": str,
        "project_name": str,
        "amount": lambda x: re.sub(r"[^\d.]", "", str(x)),
        "provisional_estimate": lambda x: re.sub(r"[^\d.]", "", str(x)),
        "safety_civilization_fee": lambda x: re.sub(r"[^\d.]", "", str(x)),
        "regulatory_fee": lambda x: re.sub(r"[^\d.]", "", str(x)),
        "tax": lambda x: re.sub(r"[^\d.]", "", str(x)),
        "remark": str,
    },
}

ALIASES = {
    "seq": ["序号"],
    "fee_name": ["费用名称", "名称"],
    "amount": ["金额", "合计"],
    "name_spec": ["名称规格", "名称、规格", "名称规格型号", "名称"],
    "project_name": ["项目名称", "名称"],
    "unit": ["计量单位", "单位"],
    "quantity": ["工程量", "预估工程量", "数量"],
    "unit_price": ["综合单价", "单价"],
    "total_price": ["合价"],
    "repair_content": ["维修内容", "修缮内容"],
    "provisional_estimate": ["暂估价", "暂估"],
    "labor_cost": ["人工费", "其中人工费"],
    "machinery_cost": ["机械费", "其中机械费"],
    "safety_civilization_fee": ["安全文明施工基本费", "安全文明施工费", "安全文明"],
    "regulatory_fee": ["规费"],
    "tax": ["税金", "税"],
}


def normalize_row(row: dict, table_type: str) -> dict:
    normalizers = FIELD_NORMALIZERS.get(table_type, {})
    result = {}
    for field_name, normalizer in normalizers.items():
        value = None
        if field_name in row:
            value = row[field_name]
        elif field_name in ALIASES:
            for alias in ALIASES[field_name]:
                for k, v in row.items():
                    if alias in str(k):
                        value = v
                        break
                if value is not None:
                    break
        if value is None:
            value = ""
        try:
            result[field_name] = normalizer(value) if value else ""
        except Exception:
            result[field_name] = str(value) if value else ""
    if table_type == "unit_project_fee_table":
        formula = result.get("formula", "")
        if "分部分项工程费" in result.get("fee_name", ""):
            formula = re.sub(r"\$?\s*\\sum\s*\$?", "Σ", formula)
            formula = formula.replace("∑", "Σ")
        formula = formula.replace(r"\times", "×")
        formula = formula.replace(r"\%", "%")
        formula = formula.replace("$", "")
        formula = re.sub(r"\s+", "", formula)
        result["formula"] = formula
    return result


def normalize_data(data: list[dict], table_type: str) -> list[dict]:
    return [normalize_row(row, table_type) for row in data if isinstance(row, dict)]
