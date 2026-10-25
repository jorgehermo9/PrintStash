import * as THREE from "three";
import { heroCameraDirection } from "@/lib/thumbnail-camera";

export interface CameraPose {
  position: [number, number, number];
  target: [number, number, number];
}
export interface ComparisonFrame {
  size: [number, number, number];
  aspect: number;
  verticalFov: number;
}

/** Fit every centered mesh with one physical camera distance and a 10% border. */
export function fitComparisonFrames(frames: Iterable<ComparisonFrame>): CameraPose {
  const direction = heroCameraDirection();
  const forward = direction.clone().negate();
  const right = new THREE.Vector3().crossVectors(forward, new THREE.Vector3(0, 1, 0)).normalize();
  const up = new THREE.Vector3().crossVectors(right, forward).normalize();
  const contentScale = 0.8;
  let distance = 0.1;
  for (const frame of frames) {
    const tangent = Math.tan(THREE.MathUtils.degToRad(frame.verticalFov) / 2) * contentScale;
    const horizontal = tangent * Math.max(frame.aspect, 1e-6);
    const half = new THREE.Vector3(...frame.size).multiplyScalar(0.5);
    for (const x of [-half.x, half.x]) {
      for (const y of [-half.y, half.y]) {
        for (const z of [-half.z, half.z]) {
          const corner = new THREE.Vector3(x, y, z);
          const depth = corner.dot(forward);
          distance = Math.max(
            distance,
            Math.abs(corner.dot(right)) / horizontal - depth,
            Math.abs(corner.dot(up)) / tangent - depth,
            0.1 - depth,
          );
        }
      }
    }
  }
  return { position: direction.multiplyScalar(distance).toArray(), target: [0, 0, 0] };
}

/** Bounds use the same alignment and normalization as the displayed mesh. */
export function comparisonBounds(
  geometry: THREE.BufferGeometry,
  options: MeshComparison,
): THREE.Vector3 {
  if (!geometry.boundingBox) geometry.computeBoundingBox();
  const transform = new THREE.Matrix4()
    .makeRotationX(-Math.PI / 2)
    .multiply(comparisonTransform(options));
  return geometry
    .boundingBox!.clone()
    .applyMatrix4(transform)
    .getSize(new THREE.Vector3())
    .multiplyScalar(sharedScale(options.referenceSizeMm));
}

export interface ComparisonCamera {
  publish: (sender: symbol, pose: CameraPose) => void;
  setFrame: (sender: symbol, frame: ComparisonFrame) => void;
  removeFrame: (sender: symbol) => void;
  fit: () => void;
  subscribe: (listener: (sender: symbol, pose: CameraPose) => void) => () => void;
}
export function createComparisonCamera(): ComparisonCamera {
  const listeners = new Set<(sender: symbol, pose: CameraPose) => void>();
  const frames = new Map<symbol, ComparisonFrame>();
  const framing = Symbol("shared-comparison-fit");
  return {
    setFrame(sender, frame) {
      frames.set(sender, frame);
    },
    removeFrame(sender) {
      frames.delete(sender);
    },
    fit() {
      if (!frames.size) return;
      const pose = fitComparisonFrames(frames.values());
      for (const listener of listeners) listener(framing, pose);
    },
    publish(sender, pose) {
      for (const listener of listeners) listener(sender, pose);
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}
export interface MeshComparison {
  referenceSizeMm: number;
  alignment?: number[][];
  compensateScale?: boolean;
  compensateMirror?: boolean;
}
/** Keep the verified rotation; apply scale/reflection only when selected. */
export function comparisonTransform(options: MeshComparison): THREE.Matrix4 {
  if (!options.alignment) return new THREE.Matrix4();
  const rows = options.alignment;
  if (
    rows.length !== 4 ||
    rows.some((row) => row.length !== 4 || row.some((value) => !Number.isFinite(value)))
  )
    return new THREE.Matrix4();
  const matrix = new THREE.Matrix4().fromArray(rows.flat()).transpose();
  const quaternion = new THREE.Quaternion();
  const scale = new THREE.Vector3();
  matrix.decompose(new THREE.Vector3(), quaternion, scale);
  if (Math.min(Math.abs(scale.x), Math.abs(scale.y), Math.abs(scale.z)) < 1e-12)
    return new THREE.Matrix4();
  if (!options.compensateScale)
    scale.set(Math.sign(scale.x), Math.sign(scale.y), Math.sign(scale.z));
  if (!options.compensateMirror) scale.set(Math.abs(scale.x), Math.abs(scale.y), Math.abs(scale.z));
  return new THREE.Matrix4().compose(new THREE.Vector3(), quaternion, scale);
}
export function sharedScale(referenceSizeMm: number): number {
  return Number.isFinite(referenceSizeMm) && referenceSizeMm > 0 ? 10 / referenceSizeMm : 1;
}
