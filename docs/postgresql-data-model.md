# CAD-KB PostgreSQL 数据模型 v1

## 目标

将当前 6 张配对 DWG/PNG 图纸的解析结果导入 PostgreSQL，提供可追溯的精确工程数据：

- 图纸和图纸版本；
- 源文件和处理运行；
- Layer、Block、CAD Entity；
- DIMENSION/MTEXT 归一化标注；
- OCR Item 和相邻框合并关系；
- CAD/OCR 坐标对齐和匹配；
- Unified Feature、证据和冲突。

## 当前范围

本版本只依赖 PostgreSQL 核心类型：

- `uuid`：应用侧生成的内部主键；
- `jsonb`：Handle、原始筛选属性、配置和统计；
- `double precision`：CAD 坐标、角度、半径和测量值；
- `double precision[]`：3x3 CAD 到图像的仿射矩阵；
- 普通 B-tree/GIN 索引。

本版本暂不创建 PostGIS、pgvector、视觉模型和语义 Chunk 表。

## 数据来源与数量基线

当前六张图纸导入前的数量基线：

```text
structured CAD entities: 850
OCR items:                93
Unified Features:        155
  confirmed:               74
  conflict:                 6
  cad_only:                62
  ocr_only:                13
```

raw LibreDWG 中未被当前 structured 提取器支持的实体类型，通过
`core.processing_runs.statistics.skipped_entity_counts` 保存，不从数据库中静默丢弃。

## 表的职责

```text
core.drawings                 逻辑图纸
core.drawing_revisions        具体图纸版本
core.source_assets            DWG/PNG/JSON文件索引
core.processing_runs          解析、OCR、对齐、融合运行记录

cad.layers                    CAD图层
cad.blocks                    CAD图块
cad.entities                  CAD实体公共记录
cad.lines/circles/arcs/...    CAD类型详情
cad.block_memberships         Block与Entity成员关系
cad.native_annotations        DIMENSION/MTEXT归一化结果
cad.native_annotation_entities 标注与原生实体关系

ocr.runs                      OCR运行
ocr.items                     OCR文字观察
ocr.item_sources              OCR相邻框合并来源
ocr.alignment_runs            坐标注册运行
ocr.alignment_anchors         注册锚点
ocr.alignment_matches         OCR/CAD匹配结果

fusion.runs                   融合运行
fusion.features               Unified Feature
fusion.feature_*              Unified Feature证据关联
fusion.conflicts              冲突和人工复核记录
```

## 关键设计约定

1. `H351`、`OCR_0001`、`UF_0001` 是来源系统外部 ID，只在图纸版本或处理运行内唯一。
2. 内部表关联使用 UUID，避免不同图纸之间的 Handle 冲突。
3. `cad.entities.raw_properties` 保存筛选后的 structured entity，不保存完整 LibreDWG raw dump。
4. `native_measurement`、`parsed_value` 和 `display_text_raw` 分开保存，避免覆盖尺寸原始含义。
5. 单位未知时保留 `NULL`，不能默认写入毫米。
6. 浮点值不在导入时四舍五入，精确查询通过容差完成。
7. OCR 是图像观察，只有与 CAD 或其他证据一致时，融合状态才标记为 `confirmed`。
8. 同一阶段的新处理结果创建新 Run，旧 Run 保留以便回溯。

## 导入顺序

```text
Drawing
→ DrawingRevision
→ SourceAsset
→ ProcessingRun
→ Layer/Block
→ CADEntity及类型详情
→ NativeAnnotation
→ OCRRun/OCRItem
→ AlignmentRun/Match
→ FusionRun/UnifiedFeature
→ Evidence/Conflict
```

每张图纸版本在一个事务中导入；同一文件、同一 Schema 和同一处理配置通过 fingerprint 保证幂等。

## 后续扩展

- PostGIS：将 CAD 和像素坐标转换为空间索引；
- `vision` Schema：保存视觉模型观察；
- `kb` Schema：保存 Semantic Chunk；
- pgvector/Qdrant/Milvus：由实际语义检索实验决定；
- Revision Change：保存同一零件不同版本的特征变化。
