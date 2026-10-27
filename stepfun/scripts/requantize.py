"""Optional offline weight conversion; does not run document/query inference.

Python 3.11:
  python -m pip install -r scripts/quantization-requirements.txt
  python scripts/requantize.py

The judged app uses the published, SHA-256 pinned artifacts. Re-exported graphs can
differ across converter versions and are deliberately saved outside the model path.
"""

import json
from pathlib import Path

import onnx
from onnxconverter_common import float16
from onnxruntime.quantization import QuantType, quantize_dynamic

root = Path(__file__).resolve().parent.parent
source = root / "public/models/Xenova/bge-small-zh-v1.5"
output = root / "tmp/requantized"
output.mkdir(parents=True, exist_ok=True)
config = json.loads((source / "quantize_config.json").read_text(encoding="utf-8"))
quantize_dynamic(
    str(source / "onnx/model.onnx"),
    str(output / "model_quantized.onnx"),
    per_channel=config["per_channel"],
    reduce_range=config["reduce_range"],
    weight_type=QuantType.QInt8,
    op_types_to_quantize=config["per_model_config"]["model"]["op_types"],
)
model = onnx.load(str(source / "onnx/model.onnx"))
converted = float16.convert_float_to_float16(model, keep_io_types=True)
onnx.save(converted, str(output / "model_fp16.onnx"))
print(f"Saved optional conversions in {output}; app weights remain pinned.")
