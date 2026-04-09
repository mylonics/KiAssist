<script setup lang="ts">
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

// -----------------------------------------------------------------------
// Props
// -----------------------------------------------------------------------

type XYZ = {x:number,y:number,z:number};
type ModelTransform = { offset: XYZ, scale: XYZ, rotate: XYZ };

const props = defineProps<{
  /** Base64-encoded STEP file data for the primary/imported component */
  stepData?: string;
  /** Footprint S-expression string (optional – rendered as PCB board beneath the model) */
  footprintSexpr?: string;
  /** Base64-encoded STEP data for the comparison/selected footprint's 3D model */
  compareStepData?: string;
  /** Footprint S-expression for the comparison/selected footprint's board */
  compareFootprintSexpr?: string;
  /** Label for primary items */
  primaryLabel?: string;
  /** Label for comparison items */
  compareLabel?: string;
  /** Transform (offset/scale/rotate) to apply to the primary/imported 3D model (from footprint) */
  modelTransform?: ModelTransform;
  /** Transform (offset/scale/rotate) to apply to the comparison 3D model */
  compareModelTransform?: ModelTransform;
}>();

// -----------------------------------------------------------------------
// Reactive state
// -----------------------------------------------------------------------

const canvasContainer = ref<HTMLDivElement | null>(null);
const loading = ref(true);
const errorMsg = ref('');

/** Transform controls exposed to UI */
const xform = reactive({
  offsetX: 0,
  offsetY: 0,
  offsetZ: 0,
  rotX: 0,
  rotY: 0,
  rotZ: 0,
});

/** Visibility toggles for the 4 renderable items */
const visibility = reactive({
  primaryModel: true,
  primaryBoard: true,
  compareModel: true,
  compareBoard: true,
});

type VisKey = keyof typeof visibility;

function toggleVis(key: VisKey) {
  visibility[key] = !visibility[key];
}

const hasCompare = computed(() => !!props.compareStepData || !!props.compareFootprintSexpr);

let renderer: THREE.WebGLRenderer | null = null;
let scene: THREE.Scene | null = null;
let camera: THREE.PerspectiveCamera | null = null;
let controls: OrbitControls | null = null;
let animFrameId = 0;

/** Primary (imported) STEP model – transform controls apply to this */
let primaryModelGroup: THREE.Group | null = null;
/** Inner transform group for user-controlled transforms on primary model */
let primaryTransformGroup: THREE.Group | null = null;
/** Primary (imported) PCB board */
let primaryBoardGroup: THREE.Group | null = null;
/** Comparison (selected) STEP model (outer container with coordinate conversion) */
let compareModelGroup: THREE.Group | null = null;
/** Inner transform group for KiCad 3D transforms on comparison model */
let compareTransformGroup: THREE.Group | null = null;
/** Comparison (selected) PCB board */
let compareBoardGroup: THREE.Group | null = null;
/** Root group for camera fitting */
let rootGroup: THREE.Group | null = null;

// -----------------------------------------------------------------------
// Minimal S-expression tokeniser / parser  (same as KicadPreview)
// -----------------------------------------------------------------------

type SExpr = string | SExpr[];

function tokenise(src: string): string[] {
  const tokens: string[] = [];
  let i = 0;
  while (i < src.length) {
    const ch = src[i];
    if (ch === '(' || ch === ')') { tokens.push(ch); i++; continue; }
    if (/\s/.test(ch)) { i++; continue; }
    if (ch === '"') {
      let s = '';
      i++;
      while (i < src.length && src[i] !== '"') {
        if (src[i] === '\\' && i + 1 < src.length) { s += src[++i]; }
        else { s += src[i]; }
        i++;
      }
      i++;
      tokens.push(s);
      continue;
    }
    let atom = '';
    while (i < src.length && !/[\s()]/.test(src[i])) { atom += src[i]; i++; }
    tokens.push(atom);
  }
  return tokens;
}

function parse(tokens: string[]): SExpr[] {
  const result: SExpr[] = [];
  let idx = 0;
  function readList(): SExpr[] {
    const list: SExpr[] = [];
    while (idx < tokens.length) {
      const t = tokens[idx];
      if (t === ')') { idx++; return list; }
      if (t === '(') { idx++; list.push(readList()); continue; }
      list.push(t); idx++;
    }
    return list;
  }
  while (idx < tokens.length) {
    const t = tokens[idx];
    if (t === '(') { idx++; result.push(readList()); }
    else { result.push(t); idx++; }
  }
  return result;
}

function parseSexpr(src: string): SExpr[] { return parse(tokenise(src)); }
function find(tree: SExpr[], tag: string): SExpr[] | undefined {
  for (const n of tree) if (Array.isArray(n) && n[0] === tag) return n;
  return undefined;
}
function findAll(tree: SExpr[], tag: string): SExpr[][] {
  const out: SExpr[][] = [];
  for (const n of tree) if (Array.isArray(n) && n[0] === tag) out.push(n);
  return out;
}
function num(v: SExpr | undefined): number {
  if (v === undefined) return 0;
  return typeof v === 'string' ? parseFloat(v) || 0 : 0;
}
function str(v: SExpr | undefined): string {
  if (v === undefined) return '';
  return typeof v === 'string' ? v : '';
}

// -----------------------------------------------------------------------
// Build PCB board from footprint S-expression
// -----------------------------------------------------------------------

const PCB_GREEN  = 0x1a6b3c;
const PCB_COPPER = 0xc87533;
const PCB_SILK   = 0xf0f0e0;
const PCB_THICKNESS = 1.6;

function buildPcbBoard(sexpr: string): THREE.Group {
  const group = new THREE.Group();
  const tree = parseSexpr(sexpr);

  let fpRoot: SExpr[] | undefined;
  for (const n of tree) {
    if (Array.isArray(n) && (n[0] === 'footprint' || n[0] === 'module')) { fpRoot = n; break; }
  }
  if (!fpRoot) return group;

  const allX: number[] = [];
  const allY: number[] = [];

  // -------- Pads --------
  for (const pad of findAll(fpRoot, 'pad')) {
    const atN = find(pad, 'at');
    const sizeN = find(pad, 'size');
    if (!atN || !sizeN) continue;
    const px = num(atN[1]);
    const py = num(atN[2]);
    const pa = (num(atN[3]) || 0) * Math.PI / 180;
    const pw = num(sizeN[1]);
    const ph = num(sizeN[2]);
    const padType = str(pad[2]);
    const padShape = str(pad[3]);

    allX.push(px - pw / 2, px + pw / 2);
    allY.push(py - ph / 2, py + ph / 2);

    let padGeo: THREE.BufferGeometry;
    if (padShape === 'circle') {
      padGeo = new THREE.CylinderGeometry(pw / 2, pw / 2, 0.04, 32);
    } else if (padShape === 'oval') {
      // Oval / oblong pad = stadium shape (rounded rect with r = min dimension / 2)
      const rr = Math.min(pw, ph) / 2;
      const shape = new THREE.Shape();
      roundedRect(shape, -pw / 2, -ph / 2, pw, ph, rr);
      padGeo = new THREE.ExtrudeGeometry(shape, { depth: 0.04, bevelEnabled: false });
      padGeo.rotateX(-Math.PI / 2);
    } else if (padShape === 'roundrect') {
      const rrNode = find(pad, 'roundrect_rratio');
      const ratio = rrNode ? num(rrNode[1]) : 0.25;
      const rr = Math.min(pw, ph) * ratio;
      const shape = new THREE.Shape();
      roundedRect(shape, -pw / 2, -ph / 2, pw, ph, rr);
      padGeo = new THREE.ExtrudeGeometry(shape, { depth: 0.04, bevelEnabled: false });
      padGeo.rotateX(-Math.PI / 2);
    } else {
      padGeo = new THREE.BoxGeometry(pw, 0.04, ph);
    }

    const padMat = new THREE.MeshStandardMaterial({
      color: PCB_COPPER, metalness: 0.7, roughness: 0.3,
    });
    const padMesh = new THREE.Mesh(padGeo, padMat);
    padMesh.position.set(px, 0.02, py);
    padMesh.rotation.y = -pa;
    padMesh.receiveShadow = true;
    group.add(padMesh);

    // Drill hole
    if (padType === 'thru_hole' || padType === 'np_thru_hole') {
      const drillNode = find(pad, 'drill');
      if (drillNode) {
        const drillSize = num(drillNode[1]);
        if (drillSize > 0) {
          const holeGeo = new THREE.CylinderGeometry(drillSize / 2, drillSize / 2, PCB_THICKNESS + 0.1, 24);
          const holeMat = new THREE.MeshStandardMaterial({ color: 0x222222, roughness: 0.9 });
          const holeMesh = new THREE.Mesh(holeGeo, holeMat);
          holeMesh.position.set(px, -PCB_THICKNESS / 2, py);
          group.add(holeMesh);
        }
      }
    }
  }

  // -------- Silkscreen / Fab lines --------
  const silkMat = new THREE.MeshStandardMaterial({ color: PCB_SILK, roughness: 0.8 });

  for (const line of findAll(fpRoot, 'fp_line')) {
    const layerN = find(line, 'layer');
    const layer = layerN ? str(layerN[1]) : '';
    if (!layer.includes('SilkS') && !layer.includes('Fab')) continue;

    const startN = find(line, 'start');
    const endN = find(line, 'end');
    if (!startN || !endN) continue;

    const x1 = num(startN[1]), y1 = num(startN[2]);
    const x2 = num(endN[1]), y2 = num(endN[2]);
    allX.push(x1, x2);
    allY.push(y1, y2);

    const dx = x2 - x1, dy = y2 - y1;
    const len = Math.hypot(dx, dy);
    if (len < 0.01) continue;

    const lineW = 0.12;
    const mat = layer.includes('SilkS') ? silkMat : silkMat.clone();
    if (!layer.includes('SilkS')) {
      (mat as THREE.MeshStandardMaterial).color.set(0xc2c200);
    }
    const lineGeo = new THREE.BoxGeometry(len, 0.02, lineW);
    const lineMesh = new THREE.Mesh(lineGeo, mat);
    lineMesh.position.set((x1 + x2) / 2, 0.01, (y1 + y2) / 2);
    lineMesh.rotation.y = -Math.atan2(dy, dx);
    lineMesh.receiveShadow = true;
    group.add(lineMesh);
  }

  // -------- Silkscreen circles --------
  for (const circ of findAll(fpRoot, 'fp_circle')) {
    const layerN = find(circ, 'layer');
    const layer = layerN ? str(layerN[1]) : '';
    if (!layer.includes('SilkS')) continue;
    const centerN = find(circ, 'center');
    const endN = find(circ, 'end');
    if (!centerN || !endN) continue;
    const cx = num(centerN[1]), cy = num(centerN[2]);
    const ex = num(endN[1]), ey = num(endN[2]);
    const r = Math.hypot(ex - cx, ey - cy);
    allX.push(cx - r, cx + r);
    allY.push(cy - r, cy + r);

    const ringGeo = new THREE.TorusGeometry(r, 0.06, 6, 48);
    const ringMesh = new THREE.Mesh(ringGeo, silkMat);
    ringMesh.position.set(cx, 0.01, cy);
    ringMesh.rotation.x = -Math.PI / 2;
    ringMesh.receiveShadow = true;
    group.add(ringMesh);
  }

  // -------- Courtyard / rect extents --------
  for (const line of findAll(fpRoot, 'fp_line')) {
    const layerN = find(line, 'layer');
    const layer = layerN ? str(layerN[1]) : '';
    if (!layer.includes('CrtYd') && !layer.includes('Edge.Cuts')) continue;
    const startN = find(line, 'start');
    const endN = find(line, 'end');
    if (startN && endN) {
      allX.push(num(startN[1]), num(endN[1]));
      allY.push(num(startN[2]), num(endN[2]));
    }
  }
  for (const rect of findAll(fpRoot, 'fp_rect')) {
    const startN = find(rect, 'start');
    const endN = find(rect, 'end');
    if (startN && endN) {
      allX.push(num(startN[1]), num(endN[1]));
      allY.push(num(startN[2]), num(endN[2]));
    }
  }

  // -------- Board substrate --------
  if (allX.length > 0 && allY.length > 0) {
    const margin = 1.5;
    const bx1 = Math.min(...allX) - margin;
    const bx2 = Math.max(...allX) + margin;
    const by1 = Math.min(...allY) - margin;
    const by2 = Math.max(...allY) + margin;
    const bw = bx2 - bx1;
    const bh = by2 - by1;

    const boardGeo = new THREE.BoxGeometry(bw, PCB_THICKNESS, bh);
    const boardMat = new THREE.MeshStandardMaterial({
      color: PCB_GREEN, roughness: 0.6, metalness: 0.05,
    });
    const boardMesh = new THREE.Mesh(boardGeo, boardMat);
    boardMesh.position.set((bx1 + bx2) / 2, -PCB_THICKNESS / 2, (by1 + by2) / 2);
    boardMesh.receiveShadow = true;
    boardMesh.castShadow = true;
    group.add(boardMesh);

    // Shadow-catching ground plane
    const groundGeo = new THREE.PlaneGeometry(bw * 4, bh * 4);
    const groundMat = new THREE.ShadowMaterial({ opacity: 0.18 });
    const groundMesh = new THREE.Mesh(groundGeo, groundMat);
    groundMesh.rotation.x = -Math.PI / 2;
    groundMesh.position.y = -PCB_THICKNESS - 0.01;
    groundMesh.receiveShadow = true;
    group.add(groundMesh);
  }

  return group;
}

function roundedRect(shape: THREE.Shape, x: number, y: number, w: number, h: number, r: number) {
  shape.moveTo(x + r, y);
  shape.lineTo(x + w - r, y);
  shape.quadraticCurveTo(x + w, y, x + w, y + r);
  shape.lineTo(x + w, y + h - r);
  shape.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  shape.lineTo(x + r, y + h);
  shape.quadraticCurveTo(x, y + h, x, y + h - r);
  shape.lineTo(x, y + r);
  shape.quadraticCurveTo(x, y, x + r, y);
}

// -----------------------------------------------------------------------
// Camera fitting
// -----------------------------------------------------------------------

function fitCamera(obj: THREE.Object3D) {
  if (!camera || !controls) return;
  const box = new THREE.Box3().setFromObject(obj);
  const size = box.getSize(new THREE.Vector3());
  const centre = box.getCenter(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z);
  if (maxDim === 0) return;
  const fov = camera.fov * (Math.PI / 180);
  const dist = (maxDim / 2) / Math.tan(fov / 2) * 0.85;

  camera.position.set(centre.x + dist * 0.3, centre.y + dist * 0.32, centre.z + dist * 0.36);
  camera.near = dist / 1000;
  camera.far = dist * 100;
  camera.updateProjectionMatrix();
  controls.target.copy(centre);
  controls.update();
}

// -----------------------------------------------------------------------
// Scene setup
// -----------------------------------------------------------------------

function getContainerSize(): [number, number] {
  const el = canvasContainer.value;
  if (!el) return [400, 300];
  return [el.clientWidth || 400, el.clientHeight || 300];
}

function initScene() {
  if (!canvasContainer.value) return;
  const [w, h] = getContainerSize();

  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(w, h, false);
  renderer.domElement.style.width = w + 'px';
  renderer.domElement.style.height = h + 'px';
  renderer.domElement.style.display = 'block';
  renderer.setClearColor(0x3d3d50);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.8;
  canvasContainer.value.appendChild(renderer.domElement);

  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x3d3d50);

  camera = new THREE.PerspectiveCamera(45, w / h, 0.01, 50000);
  camera.position.set(30, 25, 30);

  // ---- Lighting ----
  // Ambient base
  scene.add(new THREE.AmbientLight(0xffffff, 0.35));

  // Hemisphere (sky / ground fill)
  scene.add(new THREE.HemisphereLight(0xd8e8ff, 0x606060, 1.0));

  // Key light with shadows
  const key = new THREE.DirectionalLight(0xffffff, 1.8);
  key.position.set(40, 80, 60);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  key.shadow.camera.near = 0.1;
  key.shadow.camera.far = 500;
  key.shadow.camera.left = -50;
  key.shadow.camera.right = 50;
  key.shadow.camera.top = 50;
  key.shadow.camera.bottom = -50;
  key.shadow.bias = -0.002;
  scene.add(key);

  // Fill light
  const fill = new THREE.DirectionalLight(0xc0d0ee, 0.8);
  fill.position.set(-30, 40, -20);
  scene.add(fill);

  // Rim / back light
  const rim = new THREE.DirectionalLight(0xffffff, 0.5);
  rim.position.set(0, 10, -60);
  scene.add(rim);

  // Bottom fill to reduce dark underside
  const bottomFill = new THREE.DirectionalLight(0x8899bb, 0.3);
  bottomFill.position.set(0, -40, 0);
  scene.add(bottomFill);

  // Orbit controls
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.1;
  controls.minDistance = 1;
  controls.maxDistance = 500;

  rootGroup = new THREE.Group();
  scene.add(rootGroup);

  function animate() {
    animFrameId = requestAnimationFrame(animate);
    controls?.update();
    if (renderer && scene && camera) renderer.render(scene, camera);
  }
  animate();
}

// -----------------------------------------------------------------------
// Dispose helpers
// -----------------------------------------------------------------------

function disposeGroup(g: THREE.Group) {
  g.traverse((obj) => {
    if (obj instanceof THREE.Mesh) {
      obj.geometry.dispose();
      const mat = obj.material;
      if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
      else (mat as THREE.Material).dispose();
    }
  });
}

function clearScene() {
  if (!rootGroup) return;
  if (primaryModelGroup) { disposeGroup(primaryModelGroup); rootGroup.remove(primaryModelGroup); primaryModelGroup = null; primaryTransformGroup = null; }
  if (primaryBoardGroup) { disposeGroup(primaryBoardGroup); rootGroup.remove(primaryBoardGroup); primaryBoardGroup = null; }
  if (compareModelGroup) { disposeGroup(compareModelGroup); rootGroup.remove(compareModelGroup); compareModelGroup = null; compareTransformGroup = null; }
  if (compareBoardGroup) { disposeGroup(compareBoardGroup); rootGroup.remove(compareBoardGroup); compareBoardGroup = null; }
}

// -----------------------------------------------------------------------
// Apply transform
// -----------------------------------------------------------------------

function applyTransform() {
  if (!primaryTransformGroup) return;
  primaryTransformGroup.position.set(xform.offsetX, xform.offsetY, xform.offsetZ);
  primaryTransformGroup.rotation.set(
    xform.rotX * Math.PI / 180,
    xform.rotY * Math.PI / 180,
    xform.rotZ * Math.PI / 180,
  );
}

/** Apply the footprint-defined offset / scale / rotate to the comparison model.
 *
 * KiCad 3D models are authored with Z-up.  The PCB board built by
 * `buildPcbBoard` lives on the Three.js XZ plane with Y-up.  To align:
 *
 *   outerGroup  (rotation.x = -π/2  →  Z-up ➜ Y-up conversion)
 *     └─ transformGroup  (KiCad offset / scale / rotate in KiCad 3D space)
 *         └─ STEP meshes
 */
function buildCompareModelContainer(stepMeshes: THREE.Group): THREE.Group {
  const deg = Math.PI / 180;

  // Inner group: footprint-defined transform (operates in KiCad 3D space, Z-up)
  compareTransformGroup = new THREE.Group();
  const t = props.compareModelTransform;
  if (t) {
    compareTransformGroup.scale.set(t.scale.x, t.scale.y, t.scale.z);
    compareTransformGroup.rotation.set(t.rotate.x * deg, t.rotate.y * deg, t.rotate.z * deg);
    compareTransformGroup.position.set(t.offset.x, t.offset.y, t.offset.z);
  }
  compareTransformGroup.add(stepMeshes);

  // Outer group: coordinate conversion KiCad 3D (Z-up) → Three.js (Y-up)
  const container = new THREE.Group();
  container.rotation.x = -Math.PI / 2;
  container.add(compareTransformGroup);
  return container;
}

/** Update the inner transform group when compareModelTransform prop changes */
function updateCompareTransform() {
  if (!compareTransformGroup) return;
  const t = props.compareModelTransform;
  const deg = Math.PI / 180;
  if (t) {
    compareTransformGroup.scale.set(t.scale.x, t.scale.y, t.scale.z);
    compareTransformGroup.rotation.set(t.rotate.x * deg, t.rotate.y * deg, t.rotate.z * deg);
    compareTransformGroup.position.set(t.offset.x, t.offset.y, t.offset.z);
  } else {
    compareTransformGroup.scale.set(1, 1, 1);
    compareTransformGroup.rotation.set(0, 0, 0);
    compareTransformGroup.position.set(0, 0, 0);
  }
}

watch(xform, () => { applyTransform(); });

type XformKey = 'offsetX' | 'offsetY' | 'offsetZ' | 'rotX' | 'rotY' | 'rotZ';


function nudge(key: string, delta: number) {
  const k = key as XformKey;
  let val = xform[k] + delta;
  if (k.startsWith('rot')) {
    val = ((val % 360) + 360) % 360; // wrap 0-359
  }
  xform[k] = Math.round(val * 1000) / 1000;
}

function setVal(key: string, raw: string) {
  const k = key as XformKey;
  const v = parseFloat(raw);
  if (!isNaN(v)) xform[k] = v;
}

// -----------------------------------------------------------------------
// STEP parser – runs in a Web Worker so the UI stays responsive
// -----------------------------------------------------------------------

let stepWorker: Worker | null = null;
let stepMsgId = 0;

function getStepWorker(): Worker {
  if (!stepWorker) {
    stepWorker = new Worker(
      new URL('../workers/stepParser.worker.ts', import.meta.url),
      { type: 'module' },
    );
  }
  return stepWorker;
}

async function parseStepB64(b64: string): Promise<THREE.Group | null> {
  const worker = getStepWorker();
  const id = ++stepMsgId;

  return new Promise<THREE.Group | null>((resolve, reject) => {
    const handler = (e: MessageEvent) => {
      if (e.data.id !== id) return;
      worker.removeEventListener('message', handler);
      if (e.data.error) { reject(new Error(e.data.error)); return; }
      if (!e.data.meshes) { resolve(null); return; }
      resolve(buildMeshesFromWorker(e.data.meshes));
    };
    worker.addEventListener('message', handler);
    worker.postMessage({ id, b64 });
  });
}

/**
 * Build Three.js meshes from the serialised worker result.
 * The worker sends typed arrays directly (position, normal, index, color).
 */
function buildMeshesFromWorker(meshes: any[]): THREE.Group {
  const group = new THREE.Group();
  for (const mesh of meshes) {
    const geo = new THREE.BufferGeometry();

    if (mesh.position) {
      geo.setAttribute('position', new THREE.Float32BufferAttribute(mesh.position, 3));
    }
    if (mesh.normal) {
      geo.setAttribute('normal', new THREE.Float32BufferAttribute(mesh.normal, 3));
    }
    if (mesh.index) {
      geo.setIndex(new THREE.BufferAttribute(mesh.index, 1));
    }
    if (!geo.attributes.normal) geo.computeVertexNormals();

    let color = 0x8c8c8c;
    let metalness = 0.1;
    let roughness = 0.6;
    if (mesh.color && mesh.color.length >= 3) {
      color = new THREE.Color(mesh.color[0] / 255, mesh.color[1] / 255, mesh.color[2] / 255).getHex();
      const avg = (mesh.color[0] + mesh.color[1] + mesh.color[2]) / 3;
      if (avg > 120 && avg < 200
        && Math.abs(mesh.color[0] - mesh.color[1]) < 30
        && Math.abs(mesh.color[1] - mesh.color[2]) < 30) {
        metalness = 0.6;
        roughness = 0.3;
      }
    }

    const mat = new THREE.MeshStandardMaterial({
      color,
      metalness,
      roughness,
      side: THREE.DoubleSide,
      envMapIntensity: 0.8,
    });

    const m = new THREE.Mesh(geo, mat);
    m.castShadow = true;
    m.receiveShadow = true;
    group.add(m);
  }
  return group;
}

// -----------------------------------------------------------------------
// Load all models + boards
// -----------------------------------------------------------------------

async function loadAll() {
  loading.value = true;
  errorMsg.value = '';

  try {
    if (!scene) initScene();
    clearScene();

    // Primary STEP model
    if (props.stepData) {
      const stepMeshes = await parseStepB64(props.stepData);
      if (stepMeshes) {
        // Inner group: user-controlled transforms (KiCad 3D space, Z-up)
        primaryTransformGroup = new THREE.Group();
        primaryTransformGroup.add(stepMeshes);
        // Outer group: coordinate conversion KiCad 3D (Z-up) → Three.js (Y-up)
        primaryModelGroup = new THREE.Group();
        primaryModelGroup.rotation.x = -Math.PI / 2;
        primaryModelGroup.add(primaryTransformGroup);
        primaryModelGroup.visible = visibility.primaryModel;
        applyTransform();
        rootGroup!.add(primaryModelGroup);
      }
    }

    // Primary board
    if (props.footprintSexpr) {
      primaryBoardGroup = buildPcbBoard(props.footprintSexpr);
      primaryBoardGroup.visible = visibility.primaryBoard;
      rootGroup!.add(primaryBoardGroup);
    }

    // Comparison STEP model
    if (props.compareStepData) {
      const stepMeshes = await parseStepB64(props.compareStepData);
      if (stepMeshes) {
        compareModelGroup = buildCompareModelContainer(stepMeshes);
        compareModelGroup.visible = visibility.compareModel;
        rootGroup!.add(compareModelGroup);
      }
    }

    // Comparison board
    if (props.compareFootprintSexpr) {
      compareBoardGroup = buildPcbBoard(props.compareFootprintSexpr);
      compareBoardGroup.visible = visibility.compareBoard;
      rootGroup!.add(compareBoardGroup);
    }

    if (rootGroup) fitCamera(rootGroup);
  } catch (err: any) {
    console.error('Model load error:', err);
    errorMsg.value = err?.message || 'Failed to load 3D model';
  } finally {
    loading.value = false;
  }
}

// -----------------------------------------------------------------------
// Resize
// -----------------------------------------------------------------------

function onResize() {
  if (!canvasContainer.value || !renderer || !camera) return;
  const [w, h] = getContainerSize();
  renderer.setSize(w, h, false);
  renderer.domElement.style.width = w + 'px';
  renderer.domElement.style.height = h + 'px';
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}

let resizeObserver: ResizeObserver | null = null;

// -----------------------------------------------------------------------
// Reset view
// -----------------------------------------------------------------------

function resetView() {
  initXformFromProp();
  if (rootGroup) fitCamera(rootGroup);
}

/** Initialise xform reactive state from the modelTransform prop (or zero). */
function initXformFromProp() {
  const t = props.modelTransform;
  xform.offsetX = t?.offset.x ?? 0;
  xform.offsetY = t?.offset.y ?? 0;
  xform.offsetZ = t?.offset.z ?? 0;
  xform.rotX = t?.rotate.x ?? 0;
  xform.rotY = t?.rotate.y ?? 0;
  xform.rotZ = t?.rotate.z ?? 0;
}

/** Return the current transform values (for saving back to footprint). */
function getTransform(): ModelTransform {
  return {
    offset: { x: xform.offsetX, y: xform.offsetY, z: xform.offsetZ },
    scale: { x: 1, y: 1, z: 1 },
    rotate: { x: xform.rotX, y: xform.rotY, z: xform.rotZ },
  };
}

defineExpose({ getTransform });

// -----------------------------------------------------------------------
// Lifecycle
// -----------------------------------------------------------------------

onMounted(async () => {
  initXformFromProp();
  await nextTick();
  initScene();
  loadAll();

  if (canvasContainer.value) {
    resizeObserver = new ResizeObserver(onResize);
    resizeObserver.observe(canvasContainer.value);
  }
});

onBeforeUnmount(() => {
  cancelAnimationFrame(animFrameId);
  resizeObserver?.disconnect();
  controls?.dispose();
  renderer?.dispose();
  stepWorker?.terminate();
  stepWorker = null;
});

// --- Primary prop watchers ---
watch(() => props.modelTransform, () => {
  initXformFromProp();
}, { deep: true });

watch(() => props.stepData, async (val) => {
  if (!rootGroup) return;
  if (primaryModelGroup) { disposeGroup(primaryModelGroup); rootGroup.remove(primaryModelGroup); primaryModelGroup = null; primaryTransformGroup = null; }
  if (val) {
    try {
      loading.value = true;
      const stepMeshes = await parseStepB64(val);
      if (stepMeshes) {
        primaryTransformGroup = new THREE.Group();
        primaryTransformGroup.add(stepMeshes);
        primaryModelGroup = new THREE.Group();
        primaryModelGroup.rotation.x = -Math.PI / 2;
        primaryModelGroup.add(primaryTransformGroup);
        primaryModelGroup.visible = visibility.primaryModel;
        applyTransform();
        rootGroup.add(primaryModelGroup);
        fitCamera(rootGroup);
      }
    } catch { /* ignore */ } finally { loading.value = false; }
  }
});

watch(() => props.footprintSexpr, (val) => {
  if (!rootGroup) return;
  if (primaryBoardGroup) { disposeGroup(primaryBoardGroup); rootGroup.remove(primaryBoardGroup); primaryBoardGroup = null; }
  if (val) {
    primaryBoardGroup = buildPcbBoard(val);
    primaryBoardGroup.visible = visibility.primaryBoard;
    rootGroup.add(primaryBoardGroup);
  }
});

// --- Comparison prop watchers ---
watch(() => props.compareStepData, async (val) => {
  if (!rootGroup) return;
  if (compareModelGroup) { disposeGroup(compareModelGroup); rootGroup.remove(compareModelGroup); compareModelGroup = null; compareTransformGroup = null; }
  if (val) {
    try {
      loading.value = true;
      const stepMeshes = await parseStepB64(val);
      if (stepMeshes) {
        compareModelGroup = buildCompareModelContainer(stepMeshes);
        compareModelGroup.visible = visibility.compareModel;
        rootGroup.add(compareModelGroup);
        fitCamera(rootGroup);
      }
    } catch (err) { console.error('[ModelPreview] compare STEP parse error:', err); } finally { loading.value = false; }
  }
});

watch(() => props.compareFootprintSexpr, (val) => {
  if (!rootGroup) return;
  if (compareBoardGroup) { disposeGroup(compareBoardGroup); rootGroup.remove(compareBoardGroup); compareBoardGroup = null; }
  if (val) {
    compareBoardGroup = buildPcbBoard(val);
    compareBoardGroup.visible = visibility.compareBoard;
    rootGroup.add(compareBoardGroup);
  }
});

watch(() => props.compareModelTransform, () => {
  updateCompareTransform();
}, { deep: true });

// --- Visibility watcher ---
watch(visibility, () => {
  if (primaryModelGroup) primaryModelGroup.visible = visibility.primaryModel;
  if (primaryBoardGroup) primaryBoardGroup.visible = visibility.primaryBoard;
  if (compareModelGroup) compareModelGroup.visible = visibility.compareModel;
  if (compareBoardGroup) compareBoardGroup.visible = visibility.compareBoard;
});
</script>

<template>
  <div class="model-preview">
    <!-- Loading -->
    <div v-if="loading" class="model-overlay model-loading">
      <span class="material-icons spin">autorenew</span>
      Loading 3D model…
    </div>
    <!-- Error -->
    <div v-if="errorMsg" class="model-overlay model-error">
      <span class="material-icons">error_outline</span>
      {{ errorMsg }}
    </div>

    <!-- Canvas -->
    <div ref="canvasContainer" class="canvas-container"></div>

    <!-- Visibility toggles -->
    <div v-if="!loading && !errorMsg" class="visibility-panel">
      <div class="vis-group">
        <span class="vis-group-label">{{ primaryLabel || 'Imported' }}</span>
        <button v-if="stepData" class="vis-btn" :class="{ active: visibility.primaryModel }" @click="toggleVis('primaryModel')" title="Toggle imported 3D model">
          <span class="material-icons vis-icon">view_in_ar</span>
        </button>
        <button v-if="footprintSexpr" class="vis-btn" :class="{ active: visibility.primaryBoard }" @click="toggleVis('primaryBoard')" title="Toggle imported PCB board">
          <span class="material-icons vis-icon">developer_board</span>
        </button>
      </div>
      <template v-if="hasCompare">
        <div class="vis-divider"></div>
        <div class="vis-group">
          <span class="vis-group-label">{{ compareLabel || 'Selected' }}</span>
          <button v-if="compareStepData" class="vis-btn" :class="{ active: visibility.compareModel }" @click="toggleVis('compareModel')" title="Toggle comparison 3D model">
            <span class="material-icons vis-icon">view_in_ar</span>
          </button>
          <button v-if="compareFootprintSexpr" class="vis-btn" :class="{ active: visibility.compareBoard }" @click="toggleVis('compareBoard')" title="Toggle comparison PCB board">
            <span class="material-icons vis-icon">developer_board</span>
          </button>
        </div>
      </template>
    </div>

    <!-- Transform controls -->
    <div v-if="!loading && !errorMsg" class="xform-panel">
      <div class="xform-group">
        <div class="xform-section-label">Pos mm</div>
        <div class="xform-row">
          <label>X</label>
          <button class="xform-btn xform-btn-sm" @click="nudge('offsetX', -0.1)" title="-.1">-.1</button>
          <button class="xform-btn xform-btn-sm" @click="nudge('offsetX', 0.1)" title="+.1">.1</button>
          <input type="number" class="xform-input" :value="xform.offsetX.toFixed(2)" step="0.1"
            @change="setVal('offsetX', ($event.target as HTMLInputElement).value)" />
        </div>
        <div class="xform-row">
          <label>Y</label>
          <button class="xform-btn xform-btn-sm" @click="nudge('offsetY', -0.1)" title="-.1">-.1</button>
          <button class="xform-btn xform-btn-sm" @click="nudge('offsetY', 0.1)" title="+.1">.1</button>
          <input type="number" class="xform-input" :value="xform.offsetY.toFixed(2)" step="0.1"
            @change="setVal('offsetY', ($event.target as HTMLInputElement).value)" />
        </div>
        <div class="xform-row">
          <label>Z</label>
          <button class="xform-btn xform-btn-sm" @click="nudge('offsetZ', -0.1)" title="-.1">-.1</button>
          <button class="xform-btn xform-btn-sm" @click="nudge('offsetZ', 0.1)" title="+.1">.1</button>
          <input type="number" class="xform-input" :value="xform.offsetZ.toFixed(2)" step="0.1"
            @change="setVal('offsetZ', ($event.target as HTMLInputElement).value)" />
        </div>
      </div>
      <div class="xform-divider-v"></div>
      <div class="xform-group">
        <div class="xform-section-label">Rot °</div>
        <div class="xform-row">
          <label>X</label>
          <button class="xform-btn" @click="nudge('rotX', -90)" title="-90°">-90</button>
          <button class="xform-btn" @click="nudge('rotX', 90)" title="+90°">+90</button>
          <input type="number" class="xform-input" :value="xform.rotX.toFixed(0)" step="1"
            @change="setVal('rotX', ($event.target as HTMLInputElement).value)" />
        </div>
        <div class="xform-row">
          <label>Y</label>
          <button class="xform-btn" @click="nudge('rotY', -90)" title="-90°">-90</button>
          <button class="xform-btn" @click="nudge('rotY', 90)" title="+90°">+90</button>
          <input type="number" class="xform-input" :value="xform.rotY.toFixed(0)" step="1"
            @change="setVal('rotY', ($event.target as HTMLInputElement).value)" />
        </div>
        <div class="xform-row">
          <label>Z</label>
          <button class="xform-btn" @click="nudge('rotZ', -90)" title="-90°">-90</button>
          <button class="xform-btn" @click="nudge('rotZ', 90)" title="+90°">+90</button>
          <input type="number" class="xform-input" :value="xform.rotZ.toFixed(0)" step="1"
            @change="setVal('rotZ', ($event.target as HTMLInputElement).value)" />
        </div>
      </div>
      <button class="xform-reset" @click="resetView" title="Reset">
        <span class="material-icons" style="font-size: 13px">restart_alt</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.model-preview {
  position: relative;
  width: 100%;
  height: 400px;
  border-radius: 8px;
  overflow: hidden;
  background: #3d3d50;
}

.canvas-container {
  position: absolute;
  inset: 0;
  overflow: hidden;
}

.model-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 13px;
  z-index: 2;
  pointer-events: none;
}

.model-loading {
  color: #aaa;
  background: rgba(61, 61, 80, 0.9);
}

.model-error {
  color: #e66;
  background: rgba(61, 61, 80, 0.9);
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

/* ---- Transform panel ---- */
.xform-panel {
  position: absolute;
  bottom: 6px;
  left: 6px;
  background: rgba(20, 20, 30, 0.85);
  backdrop-filter: blur(6px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px;
  padding: 4px 6px;
  display: flex;
  align-items: flex-start;
  gap: 4px;
  z-index: 3;
  user-select: none;
}

.xform-group {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.xform-section-label {
  font-size: 8px;
  font-weight: 600;
  color: #8af;
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.xform-row {
  display: flex;
  align-items: center;
  gap: 2px;
  font-size: 10px;
  color: #ccc;
}

.xform-row label {
  width: 12px;
  font-weight: 700;
  font-size: 9px;
  text-align: right;
  color: #8af;
  flex-shrink: 0;
}

.xform-btn {
  height: 18px;
  padding: 0 4px;
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 2px;
  background: rgba(255, 255, 255, 0.06);
  color: #bbb;
  font-size: 9px;
  font-family: monospace;
  line-height: 16px;
  text-align: center;
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
  flex-shrink: 0;
  white-space: nowrap;
}

.xform-btn-sm {
  padding: 0 2px;
  font-size: 8px;
}

.xform-btn:hover {
  background: rgba(106, 159, 255, 0.25);
  color: #fff;
}

.xform-btn:active {
  background: rgba(106, 159, 255, 0.4);
}

.xform-input {
  width: 42px;
  height: 18px;
  padding: 0 2px;
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 2px;
  background: rgba(0, 0, 0, 0.3);
  color: #ddd;
  font-family: monospace;
  font-size: 9px;
  text-align: center;
  outline: none;
  -moz-appearance: textfield;
}

.xform-input::-webkit-inner-spin-button,
.xform-input::-webkit-outer-spin-button {
  -webkit-appearance: none;
  margin: 0;
}

.xform-input:focus {
  border-color: #6a9fff;
}

.xform-divider-v {
  width: 1px;
  align-self: stretch;
  background: rgba(255, 255, 255, 0.08);
  margin: 0 2px;
}

.xform-reset {
  align-self: center;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  padding: 0;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.05);
  color: #aaa;
  cursor: pointer;
  transition: background 0.15s;
}

.xform-reset:hover {
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
}

/* ---- Visibility panel ---- */
.visibility-panel {
  position: absolute;
  top: 6px;
  right: 6px;
  background: rgba(20, 20, 30, 0.85);
  backdrop-filter: blur(6px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px;
  padding: 4px 8px;
  display: flex;
  align-items: center;
  gap: 6px;
  z-index: 3;
  user-select: none;
}

.vis-group {
  display: flex;
  align-items: center;
  gap: 3px;
}

.vis-group-label {
  font-size: 8px;
  font-weight: 600;
  color: #8af;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  margin-right: 2px;
  max-width: 60px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.vis-divider {
  width: 1px;
  height: 20px;
  background: rgba(255, 255, 255, 0.1);
}

.vis-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.04);
  color: #555;
  cursor: pointer;
  transition: all 0.15s;
  padding: 0;
}

.vis-btn.active {
  color: #8af;
  background: rgba(106, 159, 255, 0.15);
  border-color: rgba(106, 159, 255, 0.3);
}

.vis-btn:hover {
  background: rgba(255, 255, 255, 0.12);
  color: #ccc;
}

.vis-btn.active:hover {
  background: rgba(106, 159, 255, 0.25);
  color: #adf;
}

.vis-icon {
  font-size: 15px;
}
</style>
