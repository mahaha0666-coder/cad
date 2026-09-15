"""Build traceable semantic text chunks from PostgreSQL engineering facts."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row


CHUNK_NAMESPACE = uuid.UUID("8c96ef1e-d6ea-49a5-84da-6f84c0faea66")


@dataclass(frozen=True)
class SemanticChunk:
    id: uuid.UUID
    drawing_revision_id: uuid.UUID
    drawing_key: str
    drawing_title: str
    source_type: str
    source_id: uuid.UUID
    source_external_id: str
    chunk_kind: str
    content: str
    content_hash: str
    metadata: dict[str, Any]

    @property
    def point_id(self) -> uuid.UUID:
        return self.id

    def payload(self) -> dict[str, Any]:
        return {
            "chunk_id": str(self.id),
            "drawing_revision_id": str(self.drawing_revision_id),
            "drawing_key": self.drawing_key,
            "drawing_title": self.drawing_title,
            "source_type": self.source_type,
            "source_id": str(self.source_id),
            "source_external_id": self.source_external_id,
            "chunk_kind": self.chunk_kind,
            "content": self.content,
            "content_hash": self.content_hash,
            **self.metadata,
        }


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _chunk_id(revision_id: Any, source_type: str, external_id: str) -> uuid.UUID:
    return uuid.uuid5(CHUNK_NAMESPACE, f"{revision_id}|{source_type}|{external_id}")


def _count_text(counts: dict[str, int]) -> str:
    return "、".join(f"{name} {count}个" for name, count in sorted(counts.items())) or "无已提取CAD实体"


def _build_drawing_content(row: dict[str, Any]) -> str:
    category = row.get("drawing_category") or "机械二维工程图"
    return (
        f"图纸《{row['title']}》是一张{category}。"
        f"CAD实体包括：{_count_text(row['entity_counts'])}。"
        f"共有{row['layer_count']}个图层、{row['block_count']}个图块、"
        f"{row['native_count']}条CAD原生标注、{row['ocr_count']}条OCR文字观察、"
        f"{row['vision_count']}条视觉候选观察和{row['feature_count']}条统一工程特征。"
    )


def _build_feature_content(row: dict[str, Any]) -> str:
    title = row["title"]
    feature_type = row["feature_type"]
    text = (row.get("canonical_text") or "").strip().rstrip("。；;") or None
    status = row["fusion_status"]
    parts = [f"图纸《{title}》中的工程特征类型为{feature_type}"]
    if feature_type == "vision_observation":
        parts.append(f"视觉候选观察为：{text or '未提供描述'}")
        parts.append("该信息来自视觉模型，属于候选语义，不能替代CAD精确几何和尺寸")
    else:
        if text:
            parts.append(f"规范文字为{text}")
        if row.get("dimension_kind"):
            parts.append(f"尺寸类型为{row['dimension_kind']}")
        if row.get("canonical_value") is not None:
            unit = row.get("unit") or "单位未知"
            parts.append(f"CAD规范数值为{row['canonical_value']}，{unit}")
        if row.get("vision_descriptions"):
            descriptions = [item.strip().rstrip("。；;") for item in row["vision_descriptions"] if item.strip()]
            parts.append("关联视觉观察为：" + "；".join(descriptions))
    parts.append(f"融合状态为{status}")
    return "。".join(parts) + "。"


def build_semantic_chunks(connection: psycopg.Connection[Any]) -> list[SemanticChunk]:
    chunks: list[SemanticChunk] = []
    with connection.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT r.id AS revision_id, d.drawing_key, d.title, d.drawing_category,
                   COALESCE((SELECT jsonb_object_agg(x.entity_type, x.count)
                             FROM (SELECT entity_type, count(*)::integer AS count
                                   FROM cad.entities e
                                   WHERE e.drawing_revision_id = r.id
                                   GROUP BY entity_type) x), '{}'::jsonb) AS entity_counts,
                   (SELECT count(*)::integer FROM cad.layers l WHERE l.drawing_revision_id = r.id) AS layer_count,
                   (SELECT count(*)::integer FROM cad.blocks b WHERE b.drawing_revision_id = r.id) AS block_count,
                   (SELECT count(*)::integer FROM cad.native_annotations n WHERE n.drawing_revision_id = r.id) AS native_count,
                   (SELECT count(*)::integer FROM ocr.items i JOIN ocr.runs o ON o.id=i.ocr_run_id AND o.is_current
                    WHERE o.drawing_revision_id = r.id) AS ocr_count,
                   (SELECT count(*)::integer FROM vision.observations o JOIN vision.runs v ON v.id=o.vision_run_id AND v.is_current
                    WHERE v.drawing_revision_id = r.id) AS vision_count,
                   (SELECT count(*)::integer FROM fusion.features f JOIN fusion.runs fr ON fr.id=f.fusion_run_id AND fr.is_current
                    WHERE f.drawing_revision_id = r.id) AS feature_count
            FROM core.drawing_revisions r
            JOIN core.drawings d ON d.id = r.drawing_id
            ORDER BY d.drawing_key, r.revision_sequence
            """
        )
        for row in cur.fetchall():
            content = _build_drawing_content(row)
            external_id = str(row["revision_id"])
            chunks.append(SemanticChunk(
                id=_chunk_id(row["revision_id"], "drawing_revision", external_id),
                drawing_revision_id=row["revision_id"],
                drawing_key=row["drawing_key"],
                drawing_title=row["title"],
                source_type="drawing_revision",
                source_id=row["revision_id"],
                source_external_id=external_id,
                chunk_kind="drawing_summary",
                content=content,
                content_hash=_hash(content),
                metadata={"drawing_category": row.get("drawing_category"), "entity_counts": row["entity_counts"]},
            ))

        cur.execute(
            """
            SELECT f.id AS feature_id, f.external_id, f.feature_type, f.canonical_text,
                   f.dimension_kind, f.canonical_value, f.unit, f.fusion_status,
                   f.fusion_score, f.metadata, f.drawing_revision_id AS revision_id,
                   d.drawing_key, d.title,
                   COALESCE(array_agg(DISTINCT vo.description) FILTER (WHERE vo.id IS NOT NULL), ARRAY[]::text[])
                       AS vision_descriptions
            FROM fusion.features f
            JOIN fusion.runs fr ON fr.id = f.fusion_run_id AND fr.is_current
            JOIN core.drawing_revisions r ON r.id = f.drawing_revision_id
            JOIN core.drawings d ON d.id = r.drawing_id
            LEFT JOIN fusion.feature_vision_observations fv ON fv.feature_id = f.id
            LEFT JOIN vision.observations vo ON vo.id = fv.observation_id
            GROUP BY f.id, d.drawing_key, d.title
            ORDER BY d.drawing_key, f.external_id
            """
        )
        for row in cur.fetchall():
            content = _build_feature_content(row)
            metadata = {
                "feature_id": str(row["feature_id"]),
                "feature_type": row["feature_type"],
                "fusion_status": row["fusion_status"],
                "fusion_score": row.get("fusion_score"),
                "dimension_kind": row.get("dimension_kind"),
                "canonical_value": row.get("canonical_value"),
                "unit": row.get("unit"),
            }
            chunks.append(SemanticChunk(
                id=_chunk_id(row["revision_id"], "unified_feature", row["external_id"]),
                drawing_revision_id=row["revision_id"],
                drawing_key=row["drawing_key"],
                drawing_title=row["title"],
                source_type="unified_feature",
                source_id=row["feature_id"],
                source_external_id=row["external_id"],
                chunk_kind="unified_feature",
                content=content,
                content_hash=_hash(content),
                metadata=metadata,
            ))
    return chunks
