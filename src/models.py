from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class UnitProjectFeeRow:
    seq: str = ""
    fee_name: str = ""
    formula: str = ""
    amount: str = ""
    remark: str = ""
    page_no: int = 0
    row_index: int = 0
    raw_text: str = ""
    sub_project_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SubItemProjectRow:
    seq: str = ""
    project_code: str = ""
    project_name: str = ""
    project_description: str = ""
    unit: str = ""
    quantity: str = ""
    unit_price: str = ""
    total_price: str = ""
    provisional_estimate: str = ""
    labor_cost: str = ""
    machinery_cost: str = ""
    remark: str = ""
    page_no: int = 0
    row_index: int = 0
    raw_text: str = ""
    sub_project_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SpecialtyFeeRow:
    seq: str = ""
    project_name: str = ""
    amount: str = ""
    provisional_estimate: str = ""
    safety_civilization_fee: str = ""
    regulatory_fee: str = ""
    tax: str = ""
    remark: str = ""
    page_no: int = 0
    row_index: int = 0
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LaborRow:
    seq: str = ""
    name: str = ""
    unit: str = ""
    quantity: str = ""
    unit_price: str = ""
    total_price: str = ""
    remark: str = ""
    page_no: int = 0
    row_index: int = 0
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MaterialRow:
    seq: str = ""
    name_spec: str = ""
    unit: str = ""
    quantity: str = ""
    unit_price: str = ""
    total_price: str = ""
    remark: str = ""
    page_no: int = 0
    row_index: int = 0
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MachineRow:
    seq: str = ""
    name_spec: str = ""
    unit: str = ""
    quantity: str = ""
    unit_price: str = ""
    total_price: str = ""
    remark: str = ""
    page_no: int = 0
    row_index: int = 0
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QuantityConfirmRow:
    seq: str = ""
    name: str = ""
    repair_content: str = ""
    unit: str = ""
    formula: str = ""
    quantity: str = ""
    remark: str = ""
    page_no: int = 0
    row_index: int = 0
    raw_text: str = ""
    sub_project_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConstructionProcess:
    process_type: str = ""
    content: str = ""
    cleaned_content: str = ""
    structured_items: list[dict[str, Any]] = field(default_factory=list)
    page_no: int = 0
    section_index: int = 0
    image_refs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SubProject:
    sub_project_id: str = ""
    sub_project_name: str = ""
    parent_project: str = ""
    unit_project_fee_rows: list[dict] = field(default_factory=list)
    sub_item_project_rows: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuditProject:
    doc_id: str = ""
    file_name: str = ""
    project_name: str = ""
    document_info: dict[str, Any] = field(default_factory=dict)
    total_pages: int = 0
    created_at: str = ""
    sub_projects: list[dict] = field(default_factory=list)
    specialty_fee_rows: list[dict] = field(default_factory=list)
    quantity_confirm_rows: list[dict] = field(default_factory=list)
    labor_rows: list[dict] = field(default_factory=list)
    material_rows: list[dict] = field(default_factory=list)
    machine_rows: list[dict] = field(default_factory=list)
    construction_processes: list[dict] = field(default_factory=list)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.doc_id:
            self.doc_id = hashlib.md5(f"{self.file_name}_{self.created_at}".encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, output_path: str | Path) -> None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
