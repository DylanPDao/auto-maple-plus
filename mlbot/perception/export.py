"""
Exports models/detector.pt to ONNX for the runtime Detector and benchmarks it on real frames.

    python -m mlbot.perception.export [--half] [--bench-only] [--frames 200]

The input is a fixed 16:9 shape (736x1280) so game frames need almost no padding.
"""

from __future__ import annotations

import argparse
import random
import shutil
import statistics
from pathlib import Path

import cv2

from mlbot.config import ROOT
from mlbot.perception import labels
from mlbot.perception.detector import Detector

MODELS = ROOT.parent / 'models'
INPUT_SHAPE = (736, 1280)  # h, w: multiples of 32 that fit a 720p frame


def export(half: bool) -> None:
    from ultralytics import YOLO

    out = YOLO(str(MODELS / 'detector.pt')).export(
        format='onnx', imgsz=INPUT_SHAPE, half=half, device=0 if half else 'cpu',
        simplify=True, dynamic=False, opset=17)
    target = MODELS / 'detector.onnx'
    if Path(out).resolve() != target.resolve():
        shutil.copy2(out, target)
    add_uint8_input(target)
    print(f'[export] wrote {target}')


def add_uint8_input(path: Path) -> None:
    """
    Puts preprocessing inside the model: it takes the raw BGR uint8 frame (1, H, W, 3) and does
    BGR->RGB, /255, NHWC->NCHW and the fp16 cast on the GPU. Doing that in numpy cost ~25 ms.
    """

    import onnx
    from onnx import TensorProto, helper, numpy_helper
    import numpy as np

    model = onnx.load(str(path))
    graph = model.graph
    old = graph.input[0]
    elem = old.type.tensor_type.elem_type  # float or float16
    _, _, h, w = [d.dim_value for d in old.type.tensor_type.shape.dim]
    frame = helper.make_tensor_value_info('frame', TensorProto.UINT8, [1, h, w, 3])
    np_dtype = np.float16 if elem == TensorProto.FLOAT16 else np.float32
    graph.initializer.extend([
        numpy_helper.from_array(np.array([2, 1, 0], np.int64), 'pre_bgr2rgb'),
        numpy_helper.from_array(np.array(1 / 255, np_dtype), 'pre_scale'),
    ])
    nodes = [
        helper.make_node('Gather', ['frame', 'pre_bgr2rgb'], ['pre_rgb'], axis=3),
        helper.make_node('Cast', ['pre_rgb'], ['pre_float'], to=elem),
        helper.make_node('Mul', ['pre_float', 'pre_scale'], ['pre_scaled']),
        helper.make_node('Transpose', ['pre_scaled'], [old.name], perm=[0, 3, 1, 2]),
    ]
    for i, node in enumerate(nodes):
        graph.node.insert(i, node)
    graph.input.remove(old)
    graph.input.insert(0, frame)
    onnx.checker.check_model(model)
    onnx.save(model, str(path))


def bench(n: int) -> None:
    detector = Detector(MODELS / 'detector.onnx')
    frames = sorted(labels.RAW.glob('*/*.jpg'))
    images = [cv2.imread(str(p)) for p in random.Random(0).sample(frames, min(n, len(frames)))]
    for image in images[:10]:  # warm-up: CUDA/TensorRT build kernels on first runs
        detector.detect(image)
    pre, infer, post, total = [], [], [], []
    for image in images:
        detector.detect(image)
        t = detector.timing
        pre.append(t.pre_ms), infer.append(t.infer_ms), post.append(t.post_ms), total.append(t.total_ms)

    def p95(xs: list[float]) -> float:
        return sorted(xs)[int(len(xs) * 0.95) - 1]

    kind = detector.session.get_inputs()[0].type
    print(f'[bench] {detector.provider}, input {detector.input_w}x{detector.input_h} {kind}, '
          f'{len(images)} frames')
    for name, xs in (('preprocess', pre), ('inference', infer), ('postprocess', post), ('total', total)):
        print(f'  {name:12s} median {statistics.median(xs):6.2f} ms   p95 {p95(xs):6.2f} ms')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--half', action='store_true', help='fp16 weights (faster on RTX GPUs)')
    parser.add_argument('--bench-only', action='store_true')
    parser.add_argument('--frames', type=int, default=200)
    args = parser.parse_args()
    if not args.bench_only:
        export(args.half)
    bench(args.frames)


if __name__ == '__main__':
    main()
