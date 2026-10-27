export const MODEL = 'Xenova/bge-small-zh-v1.5';
export const REVISION = '75c43b069aac4d136ba6bc1122f995fedcfd2781';
export const DIMENSION = 512;
export const QUERY_PREFIX = '为这个句子生成表示以用于检索相关文章：';
export const PROFILES = {
  auto: { label: '自动 · WebGPU 优先', dtype: 'fp16', device: 'webgpu' },
  fp32: { label: 'FP32 · WASM 基线', dtype: 'fp32', device: 'wasm' },
  wasm: { label: 'INT8 · WASM', dtype: 'q8', device: 'wasm' },
  gpu: { label: 'FP16 · WebGPU 实验', dtype: 'fp16', device: 'webgpu' },
};
export const MODEL_FILES = {
  fp32: 'onnx/model.onnx', fp16: 'onnx/model_fp16.onnx', q8: 'onnx/model_quantized.onnx',
};
export const MODEL_SIGNATURE = `${MODEL}@${REVISION}:cls:l2:query-prefix-v1:chunk220-40`;
