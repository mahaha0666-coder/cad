"""Prompt for conservative visual observations of engineering drawings."""

VISION_PROMPT_VERSION = "cad-vision-prompt-v1"


def build_vision_prompt(drawing_key: str) -> str:
    return f"""你是 CAD 工程图视觉分析器。当前图纸 key 为：{drawing_key}

请分析这张 PNG 工程图，并且只输出一个合法 JSON 对象，不要输出 Markdown、解释文字或代码围栏。

目标不是替代 LibreDWG 或 OCR，而是提取图像中它们难以表达的视觉和工程布局信息。请遵守：
1. 不要臆造无法从图像观察到的精确坐标、尺寸、单位、材料或制造结论。
2. 如果文字或数字不清晰，不要猜测，写入 uncertainty。
3. 任何孔、槽、圆角、零件类型都只能作为候选观察，不能写成已确认事实。
4. 可以提取视图、剖面线、中心线、对称关系、标题栏、技术要求和引线关系。
5. 每个 observation 尽量给出像素区域 region_px，格式为 [x_min, y_min, x_max, y_max]；无法定位时使用 null。
6. 不要把 status 写成 confirmed；所有模型观察都由系统后续与 CAD/OCR 证据融合。

JSON 格式必须为：
{{
  "drawing_key": "{drawing_key}",
  "drawing_type": "...或 null",
  "summary": "...或 null",
  "observations": [
    {{
      "observation_type": "section_view | orthographic_view | centerline | symmetry | hatch | title_block | technical_requirement | leader_relation | feature_candidate | other",
      "description": "客观描述",
      "region_px": [0, 0, 0, 0],
      "candidate_feature": null,
      "model_confidence": 0.0,
      "uncertainty": null
    }}
  ]
}}

confidence 必须是 0 到 1 之间的数；没有观察结果时 observations 返回空数组。"""
