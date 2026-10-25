/** Comparison uses one physical scale and a camera bus; compensation cannot silently rescale a model. */
import { describe, expect, it } from "vitest";
import { BoxGeometry, PerspectiveCamera, Vector3 } from "three";
import {
  comparisonBounds,
  comparisonTransform,
  fitComparisonFrames,
  createComparisonCamera,
  sharedScale,
  type CameraPose,
  type ComparisonFrame,
} from "@/lib/comparison-camera";

describe("sharedScale", () => {
  it("preserves physical size ratios", () => {
    const scale = sharedScale(40);
    expect(20 * scale).toBe(5);
    expect(40 * scale).toBe(10);
  });
  it.each([0, -1, NaN, Infinity])("contains invalid extent %s", (size) => {
    expect(sharedScale(size)).toBe(1);
  });
});
describe("comparisonTransform", () => {
  const alignment = [
    [-2, 0, 0, 80],
    [0, 2, 0, 90],
    [0, 0, 2, 100],
    [0, 0, 0, 1],
  ];
  it.each([
    { label: "neither", compensateScale: false, compensateMirror: false, x: 1, y: 1 },
    { label: "scale", compensateScale: true, compensateMirror: false, x: 2, y: 2 },
    { label: "reflection", compensateScale: false, compensateMirror: true, x: -1, y: 1 },
    { label: "both", compensateScale: true, compensateMirror: true, x: -2, y: 2 },
  ])("applies $label compensation", ({ compensateScale, compensateMirror, x, y }) => {
    const transformed = new Vector3(1, 1, 1).applyMatrix4(
      comparisonTransform({ referenceSizeMm: 10, alignment, compensateScale, compensateMirror }),
    );
    expect(transformed.toArray()).toEqual([x, y, y]);
  });
  it.each([
    undefined,
    [[1]],
    [
      [NaN, 0, 0, 0],
      [0, 1, 0, 0],
      [0, 0, 1, 0],
      [0, 0, 0, 1],
    ],
    [
      [0, 0, 0, 0],
      [0, 0, 0, 0],
      [0, 0, 0, 0],
      [0, 0, 0, 1],
    ],
  ])("ignores unusable alignment %j", (input) => {
    expect(
      new Vector3(1, 2, 3)
        .applyMatrix4(comparisonTransform({ referenceSizeMm: 10, alignment: input }))
        .toArray(),
    ).toEqual([1, 2, 3]);
  });
  it("retains the verified rotation without scale compensation", () => {
    const matrix = comparisonTransform({
      referenceSizeMm: 10,
      alignment: [
        [0, -2, 0, 0],
        [2, 0, 0, 0],
        [0, 0, 2, 0],
        [0, 0, 0, 1],
      ],
    });
    expect(new Vector3(1, 0, 0).applyMatrix4(matrix).distanceTo(new Vector3(0, 1, 0))).toBeLessThan(
      1e-12,
    );
  });
});
describe("createComparisonCamera", () => {
  it("delivers the originating camera pose to each subscriber", () => {
    const camera = createComparisonCamera();
    const origin = Symbol("origin");
    const received: CameraPose[] = [];
    camera.subscribe((sender, pose) => {
      if (sender !== origin) received.push(pose);
    });
    camera.subscribe((sender, pose) => {
      if (sender === origin) received.push(pose);
    });
    camera.publish(origin, { position: [3, 4, 5], target: [0, 0, 0] });
    expect(received).toEqual([{ position: [3, 4, 5], target: [0, 0, 0] }]);
  });
  it("stops delivering after unmount", () => {
    const camera = createComparisonCamera();
    const received: CameraPose[] = [];
    const unsubscribe = camera.subscribe((_sender, pose) => received.push(pose));
    unsubscribe();
    camera.publish(Symbol("origin"), { position: [1, 1, 1], target: [0, 0, 0] });
    expect(received).toEqual([]);
  });
});

describe("comparison geometry framing", () => {
  function projectedCorners(frame: ComparisonFrame, pose: CameraPose) {
    const camera = new PerspectiveCamera(frame.verticalFov, frame.aspect, 0.1, 1000);
    camera.position.fromArray(pose.position);
    camera.lookAt(new Vector3(...pose.target));
    camera.updateMatrixWorld();
    const half = new Vector3(...frame.size).multiplyScalar(0.5);
    return [-half.x, half.x].flatMap((x) =>
      [-half.y, half.y].flatMap((y) =>
        [-half.z, half.z].map((z) => new Vector3(x, y, z).project(camera)),
      ),
    );
  }

  it.each([0.7, 1.4, 2.4])("fills the usable frame for a shallow tray at aspect %s", (aspect) => {
    const frame: ComparisonFrame = { size: [10, 3.29, 7.04], aspect, verticalFov: 50 };
    const corners = projectedCorners(frame, fitComparisonFrames([frame]));
    const extent = Math.max(
      ...corners.flatMap((corner) => [Math.abs(corner.x), Math.abs(corner.y)]),
    );
    expect(extent).toBeCloseTo(0.8, 10);
    expect(corners.every((corner) => corner.z > -1 && corner.z < 1)).toBe(true);
  });

  it("uses one camera distance that contains both shapes in unequal viewports", () => {
    const frames: ComparisonFrame[] = [
      { size: [10, 2, 3], aspect: 2, verticalFov: 50 },
      { size: [3, 8, 5], aspect: 0.7, verticalFov: 50 },
    ];
    const pose = fitComparisonFrames(frames);
    for (const frame of frames)
      for (const corner of projectedCorners(frame, pose)) {
        expect(Math.abs(corner.x)).toBeLessThanOrEqual(0.800000001);
        expect(Math.abs(corner.y)).toBeLessThanOrEqual(0.800000001);
      }
    expect(new Vector3(...pose.position).length()).toBe(
      Math.max(
        ...frames.map((frame) => new Vector3(...fitComparisonFrames([frame]).position).length()),
      ),
    );
  });

  it("keeps physical dimensions and applies only selected scale compensation", () => {
    const geometry = new BoxGeometry(20, 10, 4);
    const positions = Array.from(geometry.getAttribute("position").array);
    const options = {
      referenceSizeMm: 40,
      alignment: [
        [2, 0, 0, 0],
        [0, 2, 0, 0],
        [0, 0, 2, 0],
        [0, 0, 0, 1],
      ],
    };
    comparisonBounds(geometry, options)
      .toArray()
      .forEach((value, index) => expect(value).toBeCloseTo([5, 1, 2.5][index], 10));
    comparisonBounds(geometry, { ...options, compensateScale: true })
      .toArray()
      .forEach((value, index) => expect(value).toBeCloseTo([10, 2, 5][index], 10));
    expect(Array.from(geometry.getAttribute("position").array)).toEqual(positions);
    geometry.dispose();
  });

  it("fits every registered viewer together and removes retired bounds", () => {
    const camera = createComparisonCamera();
    const first = Symbol("first");
    const second = Symbol("second");
    const frame: ComparisonFrame = { size: [10, 3, 7], aspect: 1.4, verticalFov: 50 };
    const large: ComparisonFrame = { ...frame, size: [20, 6, 14] };
    const received: CameraPose[] = [];
    camera.subscribe((_sender, pose) => received.push(pose));
    camera.setFrame(first, frame);
    camera.setFrame(second, large);
    camera.fit();
    expect(received.at(-1)).toEqual(fitComparisonFrames([frame, large]));
    camera.removeFrame(second);
    camera.fit();
    expect(received.at(-1)).toEqual(fitComparisonFrames([frame]));
    camera.removeFrame(first);
    camera.fit();
    expect(received).toHaveLength(2);
  });
});
