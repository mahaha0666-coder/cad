# CAD Knowledge Base：第一阶段 DWG 解析 Demo

本 Demo 已完成 CAD/OCR 数据入库、PostgreSQL 精确查询和 DeepSeek 文本模型问答，并正在接入独立的 DeepSeek Vision 图纸观察流程。
当前已增加 PostgreSQL 精确查询层：CAD、OCR 和 Unified Feature 数据可通过 `app/tools/cad_query_tools.py` 中的白名单工具查询。

## 路径

- 测试数据：`/home/lysq/CAD/dataset/AutoCAD_2D_Drawings`
- LibreDWG 源码：`/home/lysq/CAD/libredwg/libredwg`
- LibreDWG 构建目录：`/home/lysq/CAD/libredwg-build`
- 测试 DWG：`2D PROFILE DRAWING WITH RADIAL AND ANGULAR DIMENSION.dwg`

数据集共 42 个文件，其中 6 个 DWG/DXF（本次列出的 6 个全部为 DWG），总大小约 5.5M。

## 当前流程

```text
DWG → LibreDWG dwgread -O JSON → test_raw.json → extract_cad.py → test_structured.json
```

系统中原本没有 `dwgread`。标准 `autogen.sh` 因缺少 `automake`/`autoreconf` 无法执行；随后使用 LibreDWG 自带 CMake 入口在独立构建目录中成功构建了 `dwgread`。JSON 支持所需的 `jsmn` 子模块也已按仓库声明初始化。没有安装到系统目录，也没有修改 LibreDWG 核心源码和数据集原始文件。

## 输出结果

- 原始 JSON：`demo/raw/test_raw.json`，约 790K
- structured JSON：`demo/structured/test_structured.json`，约 77K
- 图层：5
- Block Header：27
- 成功提取实体：147

成功提取的实体类型：

```text
LINE: 77
CIRCLE: 7
ARC: 23
MTEXT: 20
DIMENSION: 20
```

LibreDWG 在本图中实际输出的尺寸子类型为 `DIMENSION_RADIUS`、`DIMENSION_DIAMETER`、`DIMENSION_LINEAR` 和 `DIMENSION_ANG2LN`，structured JSON 统一为 `DIMENSION`，并保留 `dimension_type`、`text`、`measurement`、文字位置和 Block 引用。

本图没有 `TEXT`、`POLYLINE/LWPOLYLINE` 或 `INSERT` 实体，因此没有伪造这些记录。当前跳过的实体为：`POINT 52`、`SOLID 28`、`TRACE 1`、`VIEWPORT 4`。`BLOCK 27` 和 `ENDBLK 27` 是块边界记录，已通过 `BLOCK_HEADER` 转换为 `blocks` 及其 `entity_ids`，未重复放入 `entities`。

## 运行提取

```bash
python3 scripts/extract_cad.py \
  demo/raw/test_raw.json \
  demo/structured/test_structured.json \
  --source-file "/home/lysq/CAD/dataset/AutoCAD_2D_Drawings/2D PROFILE DRAWING WITH RADIAL AND ANGULAR DIMENSION.dwg"
```

structured JSON 保留稳定的 `id`、原始 `handle`、`source_index`、图层引用和 Block 引用，只复制后续结构化查询需要的几何、文字和尺寸字段，并通过 `statistics` 记录原始、成功提取和跳过的实体计数。

## 双模型架构

文本模型和视觉模型职责分离：

~~~text
deepseek-v4-flash
  对话、Function Calling、PostgreSQL 精确查询

deepseek-v4-flash-vision-exp
  PNG 图纸理解、视觉候选观察提取
~~~

Vision 结果保存为 cad-vision-v1 JSON，并通过 scripts/db/import_vision.py 导入
vision.runs、vision.observations。视觉观察默认是 candidate，不能覆盖 CAD 原生精确
坐标和尺寸；后续只有在 CAD、OCR、Vision 证据一致时才升级为 confirmed。

对一张本地 PNG 执行视觉分析：

~~~bash
./.venv/bin/python scripts/run_vision.py   "dataset/AutoCAD_2D_Drawings/2D PROFILE DRAWING WITH RADIAL AND ANGULAR DIMENSION.png"   --drawing-key 2d-profile-drawing-with-radial-and-angular-dimension   --output demo/vision/normalized/test_vision.json   --raw-output demo/vision/raw/test_vision_raw.json
~~~

导入 PostgreSQL：

~~~bash
./.venv/bin/python scripts/db/import_vision.py   demo/vision/normalized/test_vision.json   --raw-json demo/vision/raw/test_vision_raw.json
~~~

上述命令会把 PNG 发送给外部 DeepSeek Vision API；没有明确授权时不要执行视觉
分析命令。Vision 表迁移为 db/migrations/007_vision_tables.sql。

## 后续建议

1. 用另一张包含 `INSERT`、`POLYLINE/LWPOLYLINE` 和 `TEXT` 的真实图纸补充回归样例。
2. 增加实体级测试，验证句柄、图层和 Block 关系不丢失。
3. 再扩展 Polyline 顶点展开、INSERT 块实例关系和更完整的 Dimension 语义。
4. 继续扩充图纸类型，并用标注集评估语义召回率和 CAD 精确验证准确率。

## Qdrant 语义检索

当前采用双存储：PostgreSQL 保存 CAD/OCR/Vision/Unified Feature 精确事实和
Semantic Chunk 来源关系；独立 Qdrant Server 保存 Chunk 的 512 维向量。Qdrant
v1.19.0 绑定本机 127.0.0.1，HTTP 使用 6333，gRPC 使用 6334，持久化数据目录为
.data/qdrant-server/storage。

管理独立 Qdrant Server：

~~~bash
./.venv/bin/python scripts/qdrant_server.py start
./.venv/bin/python scripts/qdrant_server.py status
./.venv/bin/python scripts/qdrant_server.py stop
~~~

虚拟机重启后需要再次执行 start；当前环境没有可用的用户级 systemd。

安装依赖并建立索引：

~~~bash
./.venv/bin/pip install -r requirements-vector.txt
./.venv/bin/python scripts/index_semantic.py --migrate
~~~

命令行语义搜索：

~~~bash
./.venv/bin/python scripts/search_semantic.py "带T型槽和中央通孔的对称型材"
~~~

FastAPI：

~~~text
GET /api/semantic/search?q=带中央通孔的对称型材&limit=5
~~~

## Qdrant + PostgreSQL 混合检索

当前闭环为：

~~~text
自然语言问题
  ↓
查询规划：提取显式尺寸/实体约束
  ↓
Qdrant top-k 语义召回
  ↓
按 DrawingRevision 聚合候选
  ↓
PostgreSQL 批量验证当前版本、实体、尺寸、Unified Feature 和证据
  ↓
按 0.60*语义分数 + 0.30*精确验证分数 + 0.10*证据分数排序
~~~

FastAPI 混合检索：

~~~text
POST /api/search/hybrid
~~~

示例：

~~~bash
curl -X POST http://127.0.0.1:8000/api/search/hybrid \
  -H 'Content-Type: application/json' \
  -d '{"query":"R100 半径尺寸","top_k":30,"final_limit":5}'
~~~

也可以使用 CLI：

~~~bash
./.venv/bin/python scripts/search_hybrid.py "R100 半径尺寸" --final-limit 5
~~~

结果中的 `verified` 表示显式精确约束已由 PostgreSQL 通过；
`partially_verified` 表示来源可追溯但自然语言语义尚未完全被精确条件证明；
`semantic_candidate` 是语义候选；`conflict` 表示证据冲突；`rejected` 表示未通过精确约束。

semantic_search 仍保留为低层召回接口；DeepSeek Function Calling 的语义问题优先调用 `hybrid_search`。
尺寸、数量、坐标和实体类型必须以 PostgreSQL 验证结果为准。

## 混合检索审计

每次执行 `hybrid_search` 都会写入 PostgreSQL 的 `retrieval` schema；正常完成会记录候选和约束检查，Qdrant/查询规划失败时会记录 `failed` 的 `search_runs`：

- `retrieval.search_runs`：查询、Query Plan、Qdrant 参数、耗时、状态统计和警告；
- `retrieval.candidates`：每个 DrawingRevision 候选的语义/精确/证据分数、状态和证据快照；
- `retrieval.constraint_checks`：尺寸、实体类型或来源存在性检查的期望值、实际值和通过结果。

接口返回 `search_run_id`，可通过以下接口查看完整审计记录：

~~~text
GET /api/search/runs/{search_run_id}
~~~


## 图纸上传与自动处理流水线

新增接口：

```text
POST /api/drawings/upload
GET  /api/drawings/upload/{job_id}
```

上传接口使用 multipart/form-data，DWG 必填，PNG 可选：

```bash
curl -X POST http://127.0.0.1:8000/api/drawings/upload   -F 'dwg=@/path/to/part.dwg'   -F 'png=@/path/to/part.png'   -F 'drawing_key=part-001'   -F 'title=Part 001'
```

接口返回 202 和 job_id，客户端轮询 status_url 获取
queued → processing → completed/failed、当前阶段、进度和各阶段统计。

后台流水线为：

```text
保存源文件
  → 创建 Drawing/DrawingRevision/SourceAsset
  → LibreDWG 原始 JSON
  → structured JSON + CAD 原生文字
  → RapidOCR + 相邻框合并
  → CAD/OCR 坐标对齐
  → Unified Features（可选 Vision 证据）
  → PostgreSQL 导入
  → 当前 Revision 的 Semantic Chunk
  → 增量写入 Qdrant
```

源文件和派生工件保存在 data/uploads/<job_id>/，数据库只保存资产元数据、
结构化事实、证据关系和处理状态。Vision 阶段在 UPLOAD_RUN_VISION=true
且存在 DEEPSEEK_API_KEY 时执行；否则自动跳过，不影响 DWG/OCR/PG 流程。

数据库迁移：

```bash
psql -d cad_kb -v ON_ERROR_STOP=1 -f db/migrations/010_ingestion_jobs.sql
```

本次真实本地冒烟任务 upload-pipeline-smoke 已验证全链路成功：
147 个 CAD 实体、21 个 OCR 项、21 个 Unified Feature、22 个 Semantic Chunk，
并以增量方式写入 Qdrant。
