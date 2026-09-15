# PostgreSQL CAD-KB v1

本目录保存精确工程数据和语义 Chunk 的来源关系；Embedding 向量存放在独立 Qdrant Server。

## Migration 顺序

```text
001_create_schemas.sql
002_core_tables.sql
003_cad_tables.sql
004_ocr_alignment_tables.sql
005_fusion_tables.sql
006_indexes_and_constraints.sql
007_vision_tables.sql
008_semantic_chunks.sql
009_retrieval_audit.sql
```

执行方式示例：

```bash
for file in db/migrations/*.sql; do
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$file"
done
```

## 当前数据边界

- `core`：图纸、版本、源文件、处理运行；
- `cad`：图层、图块、CAD Entity、类型详情、原生文字标注；
- `ocr`：OCR 运行、OCR Item、框合并、坐标对齐和匹配；
- `fusion`：Unified Feature、证据关联和冲突；
- `vision`：视觉模型运行、候选观察和视觉证据；
- `semantic`：语义索引运行、Chunk 文本、来源关系和 Qdrant Point ID；
- `retrieval`：混合检索运行、候选结果和 PostgreSQL 精确约束检查审计。

向量不在 PostgreSQL 重复保存；`semantic.chunks` 负责把 Qdrant 检索结果追溯到 DrawingRevision 或 UnifiedFeature。视觉观察默认仍是 candidate。

## 设计约定

- 内部主键使用应用侧生成的 UUID；
- `H351`、`OCR_0001`、`UF_0001` 是作用域内外部 ID，不是全局主键；
- DWG 单位未知时保存为 NULL；
- 原始浮点数不在导入阶段四舍五入；
- `cad.entities.raw_properties` 保存筛选后的 structured entity，不保存完整 LibreDWG raw dump；
- OCR 是观察证据，只有融合状态确认后才作为 Unified Feature 的确认来源；
- 多次解析、OCR、对齐和融合结果通过 run 记录并存，不覆盖旧结果。
