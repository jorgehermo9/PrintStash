"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls, PerspectiveCamera } from "@react-three/drei";
import * as THREE from "three";
import { AlertTriangle, Layers, Loader2 } from "lucide-react";
import { authHeaders, getUrl } from "@/lib/api/request";
import { previewPixelRatio, usePreviewPreferences } from "@/lib/preview-preferences";
import { parseGcode, type ToolpathData } from "@/lib/gcode";

// ---- Three.js Scene ----

function GcodeScene({
  data,
  currentLayer,
  showTravel,
  showBed,
  printerBedMm,
}: {
  data: ToolpathData;
  currentLayer: number;
  showTravel: boolean;
  showBed: boolean;
  printerBedMm: { x: number; y: number } | null;
}) {
  const { camera } = useThree();
  const orbitRef = useRef<any>(null);

  const extrudeGeo = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(data.extrudePositions, 3));
    geo.setAttribute("color", new THREE.BufferAttribute(data.extrudeColors, 3));
    return geo;
  }, [data]);

  const travelGeo = useMemo(() => {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.BufferAttribute(data.travelPositions, 3));
    return geo;
  }, [data]);

  const extrudeMat = useMemo(() => new THREE.LineBasicMaterial({ vertexColors: true }), []);
  const travelMat = useMemo(
    () => new THREE.LineBasicMaterial({ color: "#94a3b8", transparent: true, opacity: 0.2 }),
    [],
  );

  // Stable LineSegments objects — must be memoized so <primitive> identity is stable across renders
  const extrudeLines = useMemo(
    () => new THREE.LineSegments(extrudeGeo, extrudeMat),
    [extrudeGeo, extrudeMat],
  );
  const travelLines = useMemo(
    () => new THREE.LineSegments(travelGeo, travelMat),
    [travelGeo, travelMat],
  );

  // Bed geometry (actual mm dimensions — gcode coords are real mm)
  const bedGeo = useMemo(() => {
    if (!printerBedMm) return null;
    return new THREE.PlaneGeometry(printerBedMm.x, printerBedMm.y);
  }, [printerBedMm]);
  const bedEdgesGeo = useMemo(() => (bedGeo ? new THREE.EdgesGeometry(bedGeo) : null), [bedGeo]);

  // Update drawRange directly on the stable geometry — no ref gymnastics needed
  useEffect(() => {
    const count = data.cumulativeVertices[currentLayer + 1] ?? data.extrudePositions.length / 3;
    extrudeGeo.setDrawRange(0, count);
  }, [currentLayer, data, extrudeGeo]);

  // Reset camera and OrbitControls when data changes
  useEffect(() => {
    const d = data.bounds.maxDim;
    camera.position.set(d * 0.8, d * 0.9, d * 1.2);
    if (orbitRef.current) {
      orbitRef.current.target.set(0, 0, 0);
      orbitRef.current.update();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const gridHalfSize =
    showBed && printerBedMm
      ? Math.max(printerBedMm.x, printerBedMm.y) * 0.6
      : (Math.max(data.bounds.sizeX, data.bounds.sizeY) / 2) * 1.1 || 10;
  const floorY = -(data.bounds.sizeZ / 2);

  return (
    <>
      <PerspectiveCamera
        makeDefault
        fov={45}
        near={0.1}
        far={10000}
        position={[data.bounds.maxDim * 0.8, data.bounds.maxDim * 0.9, data.bounds.maxDim * 1.2]}
      />
      <ambientLight intensity={0.8} />
      <primitive object={extrudeLines} />
      {showTravel && data.travelPositions.length > 0 && <primitive object={travelLines} />}

      {/* Bed platform (only in bed-fit mode) */}
      {showBed && bedGeo && bedEdgesGeo && printerBedMm && (
        <group position={[0, floorY - 0.5, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <mesh geometry={bedGeo}>
            <meshStandardMaterial
              color="#1e3a5f"
              transparent
              opacity={0.15}
              side={THREE.DoubleSide}
            />
          </mesh>
          <lineSegments geometry={bedEdgesGeo}>
            <lineBasicMaterial color="#3b82f6" transparent opacity={0.8} />
          </lineSegments>
          <gridHelper
            args={[Math.max(printerBedMm.x, printerBedMm.y), 10, "#1e40af", "#1e3a5f"]}
            rotation={[Math.PI / 2, 0, 0]}
          />
        </group>
      )}

      {/* Default floor grid (when not in bed-fit mode) */}
      {!showBed && (
        <gridHelper
          args={[gridHalfSize * 2, 20, "#475569", "#334155"]}
          position={[0, floorY - 0.5, 0]}
        />
      )}

      <OrbitControls ref={orbitRef} enablePan enableZoom enableRotate />
    </>
  );
}

// ---- Error Boundary ----

interface EBState {
  hasError: boolean;
}
class GcodeErrorBoundary extends React.Component<{ children: React.ReactNode }, EBState> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }
  static getDerivedStateFromError(): EBState {
    return { hasError: true };
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted-foreground">
          <AlertTriangle className="h-8 w-8" />
          <span className="font-mono text-xs">G-code render failed</span>
        </div>
      );
    }
    return this.props.children;
  }
}

// ---- Public Component ----

/** The outcome of one completed toolpath fetch, tagged with the url it was for. */
interface LoadedToolpath {
  url: string;
  data: ToolpathData | null;
  error: string | null;
}

export interface GcodeViewerProps {
  url: string;
  printerBedMm?: { x: number; y: number } | null;
  screenshotName?: string;
}

export function GcodeViewer({ url, printerBedMm = null }: GcodeViewerProps) {
  const previewPreferences = usePreviewPreferences();
  // One state for the fetch that has actually completed, tagged with its url,
  // so switching files derives "loading" during render instead of clearing the
  // previous file's toolpath from an effect.
  const [loaded, setLoaded] = useState<LoadedToolpath | null>(null);
  const [currentLayer, setCurrentLayer] = useState(0);
  const [showTravel, setShowTravel] = useState(false);
  const [showBed, setShowBed] = useState(true);

  const current = loaded?.url === url ? loaded : null;
  const loading = current === null;
  const data = current?.data ?? null;
  const error = current?.error ?? null;

  useEffect(() => {
    // A response for a file the viewer has already left must not be shown as
    // this url's toolpath.
    let live = true;

    fetch(getUrl(url), { headers: authHeaders() })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.text();
      })
      .then((text) => {
        // PrusaSlicer binary G-code (.bgcode) starts with the "GCDE" magic and
        // carries no plain-text toolpath — its moves are heatshrink-compressed.
        // Its metadata + thumbnail are indexed on the server, but there's
        // nothing here to rasterise, so show a notice instead of an empty plot.
        if (text.startsWith("GCDE")) {
          throw new Error(
            "Binary G-code (.bgcode) can't be previewed in the browser — download the file to open it in a slicer.",
          );
        }
        const parsed = parseGcode(text);
        if (!live) return;
        setLoaded({ url, data: parsed, error: null });
        setCurrentLayer(parsed.totalLayers - 1);
      })
      .catch((cause: unknown) => {
        if (!live) return;
        setLoaded({
          url,
          data: null,
          error: cause instanceof Error ? cause.message : "Failed to load G-code",
        });
      });

    return () => {
      live = false;
    };
  }, [url]);

  if (loading) {
    return (
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted-foreground">
        <Loader2 className="h-8 w-8 animate-spin" />
        <span className="font-mono text-xs">Parsing G-code…</span>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted-foreground">
        <AlertTriangle className="h-8 w-8" />
        <span className="font-mono text-xs">{error ?? "No toolpath data"}</span>
      </div>
    );
  }

  if (data.totalLayers === 0) {
    return (
      <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted-foreground">
        <Layers className="h-8 w-8 opacity-40" />
        <span className="font-mono text-xs">No toolpath found in file</span>
      </div>
    );
  }

  return (
    <div className="relative h-full w-full">
      <GcodeErrorBoundary>
        <Canvas
          className="h-full w-full"
          dpr={previewPixelRatio(previewPreferences.previewQuality)}
          gl={{ preserveDrawingBuffer: true }}
        >
          <GcodeScene
            data={data}
            currentLayer={currentLayer}
            showTravel={showTravel}
            showBed={showBed}
            printerBedMm={printerBedMm ?? null}
          />
        </Canvas>
      </GcodeErrorBoundary>

      {/* Layer controls overlay */}
      <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-10 w-[min(90%,440px)]">
        <div className="bg-surface-container-lowest/90 backdrop-blur border border-outline-variant rounded px-3 py-2 flex flex-col gap-1.5">
          <div className="flex items-center justify-between gap-2">
            <span className="font-mono text-3xs uppercase tracking-wider text-muted-foreground">
              Layer {currentLayer + 1} / {data.totalLayers}
              {data.layerRanges[currentLayer] && (
                <> · Z {data.layerRanges[currentLayer].z.toFixed(2)} mm</>
              )}
            </span>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setShowTravel((v) => !v)}
                className={`font-mono text-3xs uppercase tracking-wider px-1.5 py-0.5 rounded border transition-colors ${
                  showTravel
                    ? "border-primary text-primary bg-secondary-container"
                    : "border-outline-variant text-muted-foreground hover:text-foreground"
                }`}
              >
                Travel
              </button>
              {printerBedMm && (
                <button
                  onClick={() => setShowBed((v) => !v)}
                  className={`font-mono text-3xs uppercase tracking-wider px-1.5 py-0.5 rounded border transition-colors ${
                    showBed
                      ? "border-blue-500 dark:border-blue-400 text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-950/30"
                      : "border-outline-variant text-muted-foreground hover:text-foreground"
                  }`}
                >
                  Bed {printerBedMm.x}×{printerBedMm.y}
                </button>
              )}
            </div>
          </div>
          <input
            type="range"
            min={0}
            max={data.totalLayers - 1}
            value={currentLayer}
            onChange={(e) => setCurrentLayer(Number(e.target.value))}
            className="w-full h-1.5 accent-primary cursor-pointer"
          />
        </div>
      </div>
    </div>
  );
}
