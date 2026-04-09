<script setup lang="ts">
import { computed, ref, reactive, watch } from 'vue';

// -----------------------------------------------------------------------
// Props
// -----------------------------------------------------------------------

const props = defineProps<{
  sexpr: string;
  type: 'symbol' | 'footprint';
  /** Optional second footprint sexpr to render side-by-side in the same viewer */
  compareSexpr?: string;
  /** Label for the primary (left) footprint */
  primaryLabel?: string;
  /** Label for the comparison (right) footprint */
  compareLabel?: string;
}>();

// -----------------------------------------------------------------------
// Minimal S-expression tokeniser / parser
// -----------------------------------------------------------------------

function tokenise(src: string): string[] {
  const tokens: string[] = [];
  let i = 0;
  while (i < src.length) {
    const ch = src[i];
    if (ch === '(' || ch === ')') { tokens.push(ch); i++; continue; }
    if (/\s/.test(ch)) { i++; continue; }
    if (ch === '"') {
      let s = '';
      i++; // skip opening "
      while (i < src.length && src[i] !== '"') {
        if (src[i] === '\\' && i + 1 < src.length) { s += src[++i]; }
        else { s += src[i]; }
        i++;
      }
      i++; // skip closing "
      tokens.push(s);
      continue;
    }
    let atom = '';
    while (i < src.length && !/[\s()]/.test(src[i])) { atom += src[i]; i++; }
    tokens.push(atom);
  }
  return tokens;
}

type SExpr = string | SExpr[];

function parse(tokens: string[]): SExpr[] {
  const result: SExpr[] = [];
  let i = 0;
  function readList(): SExpr[] {
    const list: SExpr[] = [];
    while (i < tokens.length) {
      const t = tokens[i];
      if (t === ')') { i++; return list; }
      if (t === '(') { i++; list.push(readList()); continue; }
      list.push(t); i++;
    }
    return list;
  }
  while (i < tokens.length) {
    const t = tokens[i];
    if (t === '(') { i++; result.push(readList()); }
    else { result.push(t); i++; }
  }
  return result;
}

function parseSexpr(src: string): SExpr[] {
  return parse(tokenise(src));
}

// -----------------------------------------------------------------------
// Tree helpers
// -----------------------------------------------------------------------

function find(tree: SExpr[], tag: string): SExpr[] | undefined {
  for (const node of tree) {
    if (Array.isArray(node) && node[0] === tag) return node;
  }
  return undefined;
}

function findAll(tree: SExpr[], tag: string): SExpr[][] {
  const out: SExpr[][] = [];
  for (const node of tree) {
    if (Array.isArray(node) && node[0] === tag) out.push(node);
  }
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

function hasAtom(tree: SExpr[], atom: string): boolean {
  return tree.some(n => n === atom);
}

function isHidden(node: SExpr[]): boolean {
  // Check for (hide yes) inside effects or directly
  const eff = find(node, 'effects');
  if (eff) {
    const h = find(eff, 'hide');
    if (h && str(h[1]) === 'yes') return true;
  }
  const h = find(node, 'hide');
  if (h && str(h[1]) === 'yes') return true;
  return false;
}

// -----------------------------------------------------------------------
// Rendered element types
// -----------------------------------------------------------------------

interface RLine   { kind: 'line';   x1: number; y1: number; x2: number; y2: number; stroke: number; color: string; dash?: boolean; layer?: number; }
interface RRect   { kind: 'rect';   x: number; y: number; w: number; h: number; stroke: number; fill: string; color: string; layer?: number; }
interface RCircle { kind: 'circle'; cx: number; cy: number; r: number; stroke: number; fill: string; color: string; layer?: number; }
interface RArc    { kind: 'arc';    d: string; stroke: number; fill: string; color: string; dash?: boolean; layer?: number; mx?: number; my?: number; }
interface RPoly   { kind: 'poly';   points: string; stroke: number; fill: string; color: string; layer?: number; }
interface RText   { kind: 'text';   x: number; y: number; text: string; size: number; color: string; angle: number; anchor: string; weight?: string; layer?: number; }
interface RPad    { kind: 'pad';    x: number; y: number; w: number; h: number; shape: string; angle: number; label: string; color: string; rx: number; padType: string; drill: number; layer?: number; }

type RElement = RLine | RRect | RCircle | RArc | RPoly | RText | RPad;

// -----------------------------------------------------------------------
// KiCad default (dark theme) colour palette
// -----------------------------------------------------------------------

// Symbol editor colours — KiCad 8 "KiCad Default" built-in theme
// Source: builtin_color_themes.h  s_defaultTheme
const CLR = {
  // Symbol body graphics  (LAYER_DEVICE)
  symBody:    '#840000',  // Dark red — body outline
  symBodyFill:'#FFFFC2',  // Light yellow — body background fill (LAYER_DEVICE_BACKGROUND)
  symFillOut: '#840000',  // Outline fill = same as body outline
  // Pins  (LAYER_PIN)
  pin:        '#840000',  // Dark red — pin lines
  pinName:    '#006464',  // Teal — pin names  (LAYER_PINNAM)
  pinNumber:  '#A90000',  // Dark red — pin numbers (LAYER_PINNUM)
  // Decorators
  invBubble:  '#840000',  // Inverted-output bubble (same as body)
  // Properties
  propRef:    '#006464',  // Reference designator (LAYER_REFERENCEPART)
  propVal:    '#006464',  // Value field (LAYER_VALUEPART)
  // Backgrounds
  bgSymbol:   '#F5F4EF',  // KiCad schematic background (LAYER_SCHEMATIC_BACKGROUND)
  bgFootprint:'#1a1a2e',  // PCB editor dark canvas
};

// Footprint layer colours (matching KiCad 8 dark theme)
const LAYER_COLORS: Record<string, string> = {
  'F.Cu':     '#f03636',
  'B.Cu':     '#3636f0',
  'F.SilkS':  '#f0f000',
  'B.SilkS':  '#e00080',
  'F.Fab':    '#c2c200',
  'B.Fab':    '#3636c2',
  'F.CrtYd':  '#a0a0a0',
  'B.CrtYd':  '#a0a0a0',
  'F.Paste':  '#c04040',
  'B.Paste':  '#4040c0',
  'F.Mask':   '#c040c0',
  'B.Mask':   '#40c040',
  'Edge.Cuts':'#c0c000',
  'Dwgs.User':'#c0c0c0',
  'Cmts.User':'#6060c0',
};

// Layer render order (lower = drawn first = further back)
const LAYER_ORDER: Record<string, number> = {
  'B.CrtYd': 0, 'F.CrtYd': 1,
  'B.Fab': 2, 'F.Fab': 3,
  'B.SilkS': 4, 'F.SilkS': 5,
  'B.Mask': 6, 'F.Mask': 7,
  'B.Paste': 8, 'F.Paste': 9,
  'B.Cu': 10, 'F.Cu': 11,
  'Edge.Cuts': 12,
  'Dwgs.User': 13, 'Cmts.User': 14,
};

function layerColor(layer: string): string {
  return LAYER_COLORS[layer] || '#808080';
}

function layerOrder(layer: string): number {
  return LAYER_ORDER[layer] ?? 5;
}

function isDashedLayer(layer: string): boolean {
  return layer.includes('CrtYd');
}

// -----------------------------------------------------------------------
// Parse stroke width — KiCad treats 0 as "use default"
// -----------------------------------------------------------------------

function parseStroke(node: SExpr[]): number {
  const s = find(node, 'stroke');
  if (!s) return 0;
  const w = find(s, 'width');
  return w ? num(w[1]) : 0;
}

function parseStrokeType(node: SExpr[]): string {
  const s = find(node, 'stroke');
  if (!s) return 'default';
  const t = find(s, 'type');
  return t ? str(t[1]) : 'default';
}

// Resolve stroke width: KiCad uses 0 to mean "default width"
function resolveSymStroke(raw: number): number {
  return raw > 0 ? raw : 0.254;  // KiCad default symbol line width = 10 mils
}

function resolveFpStroke(raw: number): number {
  return raw > 0 ? raw : 0.12;
}

// -----------------------------------------------------------------------
// Symbol fill
// -----------------------------------------------------------------------

function parseFill(node: SExpr[]): string {
  const f = find(node, 'fill');
  if (!f) return 'none';
  const t = find(f, 'type');
  if (!t) return 'none';
  const val = str(t[1]);
  if (val === 'background') return CLR.symBodyFill;
  if (val === 'outline')    return CLR.symFillOut;
  return 'none';
}

// -----------------------------------------------------------------------
// Parse symbol S-expression
// -----------------------------------------------------------------------

function parseSymbol(src: string): { elements: RElement[]; bbox: { x1: number; y1: number; x2: number; y2: number } } {
  const elements: RElement[] = [];
  const tree = parseSexpr(src);

  // Find the top-level symbol — may be wrapped in kicad_symbol_lib
  let symRoot: SExpr[] | undefined;
  for (const node of tree) {
    if (!Array.isArray(node)) continue;
    if (node[0] === 'kicad_symbol_lib') {
      symRoot = findAll(node, 'symbol')[0];
      break;
    }
    if (node[0] === 'symbol') { symRoot = node; break; }
  }
  if (!symRoot) return { elements, bbox: { x1: -5, y1: -5, x2: 5, y2: 5 } };

  // Read symbol-level flags
  const pinNamesNode = find(symRoot, 'pin_names');
  const pinNameOffset = pinNamesNode ? (find(pinNamesNode, 'offset') ? num(find(pinNamesNode, 'offset')![1]) : num(pinNamesNode[1])) : 0.508;
  const hidePinNames = pinNamesNode ? isHidden(pinNamesNode) || hasAtom(pinNamesNode, 'hide') : false;
  const pinNumbersNode = find(symRoot, 'pin_numbers');
  const hidePinNumbers = pinNumbersNode ? isHidden(pinNumbersNode) || hasAtom(pinNumbersNode, 'hide') : false;

  // KiCad symbols use Y-up; SVG uses Y-down. Negate all Y coordinates.
  const Y = -1;  // multiplier applied to every Y value read from the S-expression

  // Properties — render Reference & Value
  for (const prop of findAll(symRoot, 'property')) {
    const propName = str(prop[1]);
    const propVal = str(prop[2]);
    if (!propName || !propVal || propVal === '~' || propVal === '') continue;
    if (propName !== 'Reference' && propName !== 'Value') continue;

    const atN = find(prop, 'at');
    if (!atN) continue;
    const effN = find(prop, 'effects');
    if (effN && isHidden(effN)) continue;

    const tx = num(atN[1]), ty = Y * num(atN[2]), ta = num(atN[3]) || 0;
    let fontSize = 1.27;
    let fontWeight = 'normal';
    if (effN) {
      const fontN = find(effN, 'font');
      if (fontN) {
        const sizeN = find(fontN, 'size');
        if (sizeN) fontSize = num(sizeN[1]);
        if (hasAtom(fontN, 'bold') || (find(fontN, 'bold') && str(find(fontN, 'bold')![1]) === 'yes')) {
          fontWeight = 'bold';
        }
      }
    }

    elements.push({
      kind: 'text',
      x: tx, y: ty,
      text: propVal,
      size: fontSize,
      color: propName === 'Reference' ? CLR.propRef : CLR.propVal,
      angle: ta,
      anchor: 'middle-middle',
      weight: fontWeight,
      layer: 100,  // draw properties on top
    });
  }

  // Collect sub-symbols (units)
  const units = findAll(symRoot, 'symbol');

  for (const unit of units) {
    // Filter out alternate body styles (DeMorgan): render style 0 (shared)
    // and style 1 (normal); skip style 2+ (DeMorgan alternate).
    // Sub-symbol names follow: parentName_unitNumber_styleNumber
    const unitName = str(unit[1]);
    const styleMatch = unitName.match(/_(\d+)_(\d+)$/);
    if (styleMatch && parseInt(styleMatch[2], 10) > 1) continue;
    // Polylines
    for (const poly of findAll(unit, 'polyline')) {
      const ptsNode = find(poly, 'pts');
      if (!ptsNode) continue;
      const pts = findAll(ptsNode, 'xy');
      const sw = resolveSymStroke(parseStroke(poly));
      const fill = parseFill(poly);
      if (pts.length === 2) {
        elements.push({
          kind: 'line',
          x1: num(pts[0][1]), y1: Y * num(pts[0][2]),
          x2: num(pts[1][1]), y2: Y * num(pts[1][2]),
          stroke: sw, color: CLR.symBody, layer: 10,
        });
      } else if (pts.length > 2) {
        elements.push({
          kind: 'poly',
          points: pts.map(p => `${num(p[1])},${Y * num(p[2])}`).join(' '),
          stroke: sw, fill, color: CLR.symBody, layer: 10,
        });
      }
    }

    // Rectangles
    for (const rect of findAll(unit, 'rectangle')) {
      const start = find(rect, 'start');
      const end = find(rect, 'end');
      if (!start || !end) continue;
      const x1 = num(start[1]), y1 = Y * num(start[2]);
      const x2 = num(end[1]), y2 = Y * num(end[2]);
      const sw = resolveSymStroke(parseStroke(rect));
      const fill = parseFill(rect);
      elements.push({
        kind: 'rect',
        x: Math.min(x1, x2), y: Math.min(y1, y2),
        w: Math.abs(x2 - x1), h: Math.abs(y2 - y1),
        stroke: sw, fill, color: CLR.symBody, layer: 10,
      });
    }

    // Circles
    for (const circ of findAll(unit, 'circle')) {
      const center = find(circ, 'center');
      const radius = find(circ, 'radius');
      if (!center || !radius) continue;
      const sw = resolveSymStroke(parseStroke(circ));
      const fill = parseFill(circ);
      elements.push({
        kind: 'circle',
        cx: num(center[1]), cy: Y * num(center[2]),
        r: num(radius[1]),
        stroke: sw, fill, color: CLR.symBody, layer: 10,
      });
    }

    // Arcs
    for (const arc of findAll(unit, 'arc')) {
      const startN = find(arc, 'start');
      const midN = find(arc, 'mid');
      const endN = find(arc, 'end');
      if (!startN || !midN || !endN) continue;
      const sw = resolveSymStroke(parseStroke(arc));
      const fill = parseFill(arc);
      const amx = num(midN[1]), amy = Y * num(midN[2]);
      const d = arcPath(
        num(startN[1]), Y * num(startN[2]),
        amx,            amy,
        num(endN[1]),   Y * num(endN[2]),
      );
      elements.push({ kind: 'arc', d, stroke: sw, fill, color: CLR.symBody, layer: 10, mx: amx, my: amy });
    }

    // Pins
    for (const pin of findAll(unit, 'pin')) {
      // (pin electrical_type graphic_style (at x y angle) (length l) (name ...) (number ...))
      const atN = find(pin, 'at');
      const lenN = find(pin, 'length');
      if (!atN) continue;
      const px = num(atN[1]), py = Y * num(atN[2]);
      const angle = num(atN[3]) || 0;
      const len = lenN ? num(lenN[1]) : 2.54;

      // Graphic style (2nd positional atom after "pin")
      const graphicStyle = str(pin[2]); // e.g. "line", "inverted", "clock", etc.

      // Pin direction vector (angle 0 = extends right from connection point towards body)
      const rad = (angle * Math.PI) / 180;
      const cosA = Math.cos(rad), sinA = Math.sin(rad);
      const dx = cosA * len;
      const dy = -sinA * len; // KiCad Y-up for angle, SVG Y-down

      const bodyX = px + dx;  // where pin meets symbol body
      const bodyY = py + dy;

      // Inverted style: bubble radius
      const INV_R = 0.4;
      let lineEndX = bodyX, lineEndY = bodyY;

      if (graphicStyle === 'inverted' || graphicStyle === 'inverted_clock') {
        // Shorten pin line by bubble diameter, draw bubble at body end
        const bubbleDx = cosA * INV_R;
        const bubbleDy = -sinA * INV_R;
        lineEndX = bodyX - bubbleDx;
        lineEndY = bodyY + bubbleDy;
        const bubbleCx = bodyX - bubbleDx * 0.5;
        const bubbleCy = bodyY + bubbleDy * 0.5;
        // Inversion bubble (not filled, just outline)
        elements.push({
          kind: 'circle',
          cx: bubbleCx, cy: bubbleCy, r: INV_R * 0.5,
          stroke: 0.15, fill: 'none', color: CLR.symBody, layer: 22,
        });
      }

      // Pin line
      elements.push({
        kind: 'line',
        x1: px, y1: py, x2: lineEndX, y2: lineEndY,
        stroke: 0.15, color: CLR.pin, layer: 20,
      });

      // Clock style: small triangle at body end
      if (graphicStyle === 'clock' || graphicStyle === 'inverted_clock') {
        const CLK_SZ = 0.6;
        // Triangle perpendicular to pin direction at bodyX, bodyY
        const perpX = sinA * CLK_SZ;  // perpendicular direction
        const perpY = cosA * CLK_SZ;
        const tipDx = cosA * CLK_SZ;
        const tipDy = -sinA * CLK_SZ;
        const triPoints = [
          `${bodyX - perpX},${bodyY - perpY}`,
          `${bodyX + tipDx},${bodyY + tipDy}`,
          `${bodyX + perpX},${bodyY + perpY}`,
        ].join(' ');
        elements.push({
          kind: 'poly', points: triPoints,
          stroke: 0.15, fill: 'none', color: CLR.symBody, layer: 22,
        });
      }

      // Pin number text — placed along pin line, offset above/below
      if (!hidePinNumbers) {
        const numN = find(pin, 'number');
        const pinNum = numN ? str(numN[1]) : '';
        if (pinNum && pinNum !== '~') {
          // Position: midpoint of pin line, offset perpendicular
          const midX = (px + bodyX) / 2;
          const midY = (py + bodyY) / 2;
          const OFFSET = 0.4;
          // Offset perpendicular to pin direction
          const offX = sinA * OFFSET;
          const offY = cosA * OFFSET;
          let numFontSize = 1.0;
          if (numN) {
            const eff = find(numN, 'effects');
            if (eff) {
              const fn = find(eff, 'font');
              if (fn) {
                const sz = find(fn, 'size');
                if (sz) numFontSize = num(sz[1]);
              }
            }
          }
          elements.push({
            kind: 'text',
            x: midX + offX, y: midY + offY,
            text: pinNum,
            size: numFontSize,
            color: CLR.pinNumber,
            angle: 0,
            anchor: 'middle-middle',
            layer: 30,
          });
        }
      }

      // Pin name text — placed at body end with offset inside body
      if (!hidePinNames) {
        const nameN = find(pin, 'name');
        const pinName = nameN ? str(nameN[1]) : '';
        if (pinName && pinName !== '~') {
          // Place name at bodyEnd, offset inside the symbol body
          const nameShift = pinNameOffset > 0 ? pinNameOffset : 0.508;
          const nameX = bodyX + cosA * nameShift;
          const nameY = bodyY + (-sinA) * nameShift;
          let nameFontSize = 1.27;
          if (nameN) {
            const eff = find(nameN, 'effects');
            if (eff) {
              const fn = find(eff, 'font');
              if (fn) {
                const sz = find(fn, 'size');
                if (sz) nameFontSize = num(sz[1]);
              }
            }
          }

          const a = ((angle % 360) + 360) % 360;
          let anchor: string;
          if (pinNameOffset === 0) {
            // When offset is 0, names are centered on the pin end (above/below)
            anchor = 'middle-middle';
          } else if (a === 0)   { anchor = 'start-middle'; }
          else if (a === 180)   { anchor = 'end-middle'; }
          else if (a === 90)    { anchor = 'middle-bottom'; }
          else                  { anchor = 'middle-top'; }

          elements.push({
            kind: 'text',
            x: nameX, y: nameY,
            text: pinName,
            size: nameFontSize,
            color: CLR.pinName,
            angle: 0,
            anchor,
            layer: 30,
          });
        }
      }

      // Small circle at connection point (pin end dot)
      elements.push({
        kind: 'circle',
        cx: px, cy: py, r: 0.15,
        stroke: 0, fill: CLR.pin, color: CLR.pin, layer: 21,
      });
    }
  }

  // Sort by layer
  elements.sort((a, b) => (a.layer ?? 0) - (b.layer ?? 0));
  const bbox = computeBbox(elements);
  return { elements, bbox };
}

// -----------------------------------------------------------------------
// Parse footprint S-expression
// -----------------------------------------------------------------------

function parseFootprint(src: string): { elements: RElement[]; bbox: { x1: number; y1: number; x2: number; y2: number } } {
  const elements: RElement[] = [];
  const tree = parseSexpr(src);
  let fpRoot: SExpr[] | undefined;
  for (const node of tree) {
    if (Array.isArray(node) && (node[0] === 'footprint' || node[0] === 'module')) {
      fpRoot = node;
      break;
    }
  }
  if (!fpRoot) return { elements, bbox: { x1: -5, y1: -5, x2: 5, y2: 5 } };

  // Helper to get layer from a graphic element
  function getLayer(node: SExpr[]): string {
    const layerN = find(node, 'layer');
    return layerN ? str(layerN[1]) : 'F.Fab';
  }

  // fp_line
  for (const line of findAll(fpRoot, 'fp_line')) {
    const startN = find(line, 'start');
    const endN = find(line, 'end');
    if (!startN || !endN) continue;
    const layer = getLayer(line);
    const sw = resolveFpStroke(parseStroke(line));
    const dashed = isDashedLayer(layer) || parseStrokeType(line) === 'dash';
    elements.push({
      kind: 'line',
      x1: num(startN[1]), y1: num(startN[2]),
      x2: num(endN[1]), y2: num(endN[2]),
      stroke: sw, color: layerColor(layer), dash: dashed,
      layer: layerOrder(layer),
    });
  }

  // fp_rect
  for (const rect of findAll(fpRoot, 'fp_rect')) {
    const startN = find(rect, 'start');
    const endN = find(rect, 'end');
    if (!startN || !endN) continue;
    const layer = getLayer(rect);
    const sw = resolveFpStroke(parseStroke(rect));
    const x1 = num(startN[1]), y1 = num(startN[2]);
    const x2 = num(endN[1]), y2 = num(endN[2]);
    const fillN = find(rect, 'fill');
    let fill = 'none';
    if (fillN) {
      const t = find(fillN, 'type');
      if (t && str(t[1]) === 'solid') fill = layerColor(layer);
    }
    elements.push({
      kind: 'rect',
      x: Math.min(x1, x2), y: Math.min(y1, y2),
      w: Math.abs(x2 - x1), h: Math.abs(y2 - y1),
      stroke: sw, fill, color: layerColor(layer),
      layer: layerOrder(layer),
    });
  }

  // fp_circle
  for (const circ of findAll(fpRoot, 'fp_circle')) {
    const centerN = find(circ, 'center');
    const endN = find(circ, 'end');
    if (!centerN || !endN) continue;
    const layer = getLayer(circ);
    const cx = num(centerN[1]), cy = num(centerN[2]);
    const ex = num(endN[1]), ey = num(endN[2]);
    const r = Math.hypot(ex - cx, ey - cy);
    const sw = resolveFpStroke(parseStroke(circ));
    const fillN = find(circ, 'fill');
    let fill = 'none';
    if (fillN) {
      const t = find(fillN, 'type');
      if (t && str(t[1]) === 'solid') fill = layerColor(layer);
    }
    elements.push({
      kind: 'circle',
      cx, cy, r,
      stroke: sw, fill, color: layerColor(layer),
      layer: layerOrder(layer),
    });
  }

  // fp_arc
  for (const arc of findAll(fpRoot, 'fp_arc')) {
    const startN = find(arc, 'start');
    const midN = find(arc, 'mid');
    const endN = find(arc, 'end');
    const angleN = find(arc, 'angle');
    if (!startN || !endN) continue;
    const layer = getLayer(arc);
    const sw = resolveFpStroke(parseStroke(arc));
    const dashed = isDashedLayer(layer);

    let d: string;
    let fpArcMx: number | undefined;
    let fpArcMy: number | undefined;
    if (midN) {
      // New KiCad format: start / mid / end (3 points on the arc)
      fpArcMx = num(midN[1]);
      fpArcMy = num(midN[2]);
      d = arcPath(
        num(startN[1]), num(startN[2]),
        fpArcMx, fpArcMy,
        num(endN[1]), num(endN[2]),
      );
    } else if (angleN) {
      // Old KiCad format: start = centre, end = arc-start-point, angle = sweep°
      d = arcPathFromAngle(
        num(startN[1]), num(startN[2]),   // centre
        num(endN[1]), num(endN[2]),       // arc start point
        num(angleN[1]),                    // sweep angle in degrees
      );
    } else {
      // Fallback: straight line
      d = `M${num(startN[1])},${num(startN[2])} L${num(endN[1])},${num(endN[2])}`;
    }
    elements.push({ kind: 'arc', d, stroke: sw, fill: 'none', color: layerColor(layer), dash: dashed, layer: layerOrder(layer), mx: fpArcMx, my: fpArcMy });
  }

  // fp_poly
  for (const poly of findAll(fpRoot, 'fp_poly')) {
    const ptsNode = find(poly, 'pts');
    if (!ptsNode) continue;
    const layer = getLayer(poly);
    const pts = findAll(ptsNode, 'xy');
    const sw = resolveFpStroke(parseStroke(poly));
    const fillN = find(poly, 'fill');
    let fill = 'none';
    if (fillN) {
      const t = find(fillN, 'type');
      if (t && str(t[1]) === 'solid') fill = layerColor(layer);
    }
    if (pts.length > 1) {
      elements.push({
        kind: 'poly',
        points: pts.map(p => `${num(p[1])},${num(p[2])}`).join(' '),
        stroke: sw, fill, color: layerColor(layer),
        layer: layerOrder(layer),
      });
    }
  }

  // pads
  for (const pad of findAll(fpRoot, 'pad')) {
    const atN = find(pad, 'at');
    const sizeN = find(pad, 'size');
    if (!atN || !sizeN) continue;
    const px = num(atN[1]), py = num(atN[2]), pa = num(atN[3]) || 0;
    const pw = num(sizeN[1]), ph = num(sizeN[2]);
    const padNum = str(pad[1]);

    // Pad type: smd, thru_hole, np_thru_hole, connect
    const padType = str(pad[2]);

    // Shape: roundrect, circle, oval, rect, custom, trapezoid
    const padShape = str(pad[3]);

    // Determine layer colour
    const layersN = find(pad, 'layers');
    let padColor = LAYER_COLORS['F.Cu'];
    if (layersN) {
      for (let li = 1; li < layersN.length; li++) {
        const l = str(layersN[li]);
        if (l.includes('Cu')) { padColor = layerColor(l); break; }
      }
    }
    // Through-hole pads using *.Cu get F.Cu colour
    if (layersN) {
      for (let li = 1; li < layersN.length; li++) {
        if (str(layersN[li]) === '*.Cu') { padColor = LAYER_COLORS['F.Cu']; break; }
      }
    }

    let rx = 0;
    if (padShape === 'roundrect') {
      const rrNode = find(pad, 'roundrect_rratio');
      const ratio = rrNode ? num(rrNode[1]) : 0.25;
      rx = Math.min(pw, ph) * ratio;
    }

    // Drill size
    let drill = 0;
    const drillNode = find(pad, 'drill');
    if (drillNode) {
      drill = num(drillNode[1]);
    }

    elements.push({
      kind: 'pad',
      x: px, y: py,
      w: pw, h: ph,
      shape: padShape,
      angle: pa,
      label: padNum,
      color: padColor,
      rx,
      padType,
      drill,
      layer: 50,  // pads always on top of graphics
    });
  }

  // fp_text — reference / value labels
  for (const txt of findAll(fpRoot, 'fp_text')) {
    const txtVal = str(txt[2]);
    const atN = find(txt, 'at');
    if (!atN || !txtVal) continue;
    const layer = getLayer(txt);

    // Check for hidden text
    const effectsN = find(txt, 'effects');
    if (effectsN && isHidden(effectsN)) continue;

    const tx = num(atN[1]), ty = num(atN[2]);
    const ta = num(atN[3]) || 0;
    let fontSize = 1.0;
    let fontWeight = 'normal';
    if (effectsN) {
      const fontN = find(effectsN, 'font');
      if (fontN) {
        const sizeN = find(fontN, 'size');
        if (sizeN) fontSize = num(sizeN[1]);
        if (hasAtom(fontN, 'bold')) fontWeight = 'bold';
      }
    }

    elements.push({
      kind: 'text',
      x: tx, y: ty,
      text: txtVal,
      size: fontSize,
      color: layerColor(layer),
      angle: ta,
      anchor: 'middle-middle',
      weight: fontWeight,
      layer: layerOrder(layer),
    });
  }

  // Sort elements by layer for proper rendering order
  elements.sort((a, b) => (a.layer ?? 0) - (b.layer ?? 0));

  const bbox = computeBbox(elements);
  return { elements, bbox };
}

// -----------------------------------------------------------------------
// Arc through 3 points → SVG path
// -----------------------------------------------------------------------

/**
 * Build an SVG arc path from the **old** KiCad fp_arc format:
 *   (fp_arc (start cx cy) (end sx sy) (angle deg) ...)
 * where (start) is the circle centre, (end) is the arc start point,
 * and angle is the sweep in degrees (negative = clockwise).
 */
function arcPathFromAngle(
  cx: number, cy: number,
  sx: number, sy: number,
  angleDeg: number,
): string {
  const r = Math.hypot(sx - cx, sy - cy);
  if (r < 1e-10) return `M${sx},${sy}`;

  const startAngle = Math.atan2(sy - cy, sx - cx);
  // KiCad Y-down: positive angle = CW on screen, negative = CCW.
  // atan2 in Y-down also increases CW, so no negation needed.
  const sweepRad = (angleDeg * Math.PI) / 180;
  const endAngle = startAngle + sweepRad;

  const ex = cx + r * Math.cos(endAngle);
  const ey = cy + r * Math.sin(endAngle);

  const largeArc = Math.abs(angleDeg) > 180 ? 1 : 0;
  // In SVG Y-down: positive sweep = CW = sweepFlag 1
  const sweepFlag = sweepRad >= 0 ? 1 : 0;

  return `M${sx},${sy} A${r},${r} 0 ${largeArc},${sweepFlag} ${ex},${ey}`;
}

function arcPath(sx: number, sy: number, mx: number, my: number, ex: number, ey: number): string {
  // Compute circumcircle through the 3 points
  const ax = sx, ay = sy;
  const bx = mx, by = my;
  const cx = ex, cy = ey;

  const d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by));
  if (Math.abs(d) < 1e-10) {
    return `M${sx},${sy} L${ex},${ey}`;
  }

  const ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d;
  const uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d;
  const r = Math.hypot(ax - ux, ay - uy);

  // Use the midpoint to determine which arc (sweep direction) to draw.
  // Compute angles from center to each point.
  const aStart = Math.atan2(sy - uy, sx - ux);
  const aMid   = Math.atan2(my - uy, mx - ux);
  const aEnd   = Math.atan2(ey - uy, ex - ux);

  // Angular distance from start going in the positive-angle (CCW in math) direction
  function angDistCCW(from: number, to: number): number {
    let a = to - from;
    while (a < 0) a += 2 * Math.PI;
    while (a >= 2 * Math.PI) a -= 2 * Math.PI;
    return a;
  }

  const startToMid = angDistCCW(aStart, aMid);
  const startToEnd = angDistCCW(aStart, aEnd);

  // If mid is between start and end going in the positive-angle direction,
  // the arc follows that direction.
  // In SVG (Y-down): positive atan2 direction = CW visually = sweepFlag 1
  let sweepFlag: number;
  let arcSpan: number;

  if (startToMid <= startToEnd) {
    // Arc goes in the positive-angle direction (CW in SVG)
    sweepFlag = 1;
    arcSpan = startToEnd;
  } else {
    // Arc goes in the negative-angle direction (CCW in SVG)
    sweepFlag = 0;
    arcSpan = 2 * Math.PI - startToEnd;
  }

  const largeArcFlag = arcSpan > Math.PI ? 1 : 0;

  return `M${sx},${sy} A${r},${r} 0 ${largeArcFlag},${sweepFlag} ${ex},${ey}`;
}

// -----------------------------------------------------------------------
// Bounding box
// -----------------------------------------------------------------------

function computeBbox(elements: RElement[]): { x1: number; y1: number; x2: number; y2: number } {
  let x1 = Infinity, y1 = Infinity, x2 = -Infinity, y2 = -Infinity;

  function expand(x: number, y: number, margin = 0) {
    if (x - margin < x1) x1 = x - margin;
    if (y - margin < y1) y1 = y - margin;
    if (x + margin > x2) x2 = x + margin;
    if (y + margin > y2) y2 = y + margin;
  }

  for (const el of elements) {
    switch (el.kind) {
      case 'line':
        expand(el.x1, el.y1);
        expand(el.x2, el.y2);
        break;
      case 'rect':
        expand(el.x, el.y);
        expand(el.x + el.w, el.y + el.h);
        break;
      case 'circle':
        expand(el.cx - el.r, el.cy - el.r);
        expand(el.cx + el.r, el.cy + el.r);
        break;
      case 'arc': {
        const match = el.d.match(/^M([-\d.]+),([-\d.]+).*?([-\d.]+),([-\d.]+)$/);
        if (match) {
          expand(parseFloat(match[1]), parseFloat(match[2]));
          expand(parseFloat(match[3]), parseFloat(match[4]));
        }
        // Include arc midpoint for accurate bounding box
        if (el.mx !== undefined && el.my !== undefined) {
          expand(el.mx, el.my);
        }
        break;
      }
      case 'poly':
        for (const pair of el.points.split(' ')) {
          const [px, py] = pair.split(',').map(Number);
          if (!isNaN(px) && !isNaN(py)) expand(px, py);
        }
        break;
      case 'text':
        expand(el.x, el.y, el.size * 1.5);
        break;
      case 'pad':
        expand(el.x - el.w / 2, el.y - el.h / 2);
        expand(el.x + el.w / 2, el.y + el.h / 2);
        break;
    }
  }

  if (!isFinite(x1)) return { x1: -5, y1: -5, x2: 5, y2: 5 };
  return { x1, y1, x2, y2 };
}

// -----------------------------------------------------------------------
// Computed rendering data
// -----------------------------------------------------------------------

const renderData = computed(() => {
  if (!props.sexpr?.trim()) return null;
  try {
    const result = props.type === 'symbol'
      ? parseSymbol(props.sexpr)
      : parseFootprint(props.sexpr);
    const padFrac = 0.12;
    const w = result.bbox.x2 - result.bbox.x1;
    const h = result.bbox.y2 - result.bbox.y1;
    const pad = Math.max(w * padFrac, h * padFrac, 0.5);
    return {
      elements: result.elements,
      bbox: result.bbox,
      viewBox: `${result.bbox.x1 - pad} ${result.bbox.y1 - pad} ${w + pad * 2} ${h + pad * 2}`,
    };
  } catch {
    return null;
  }
});

/** Parsed comparison data (for footprint or symbol type with compareSexpr) */
const compareData = computed(() => {
  if (!props.compareSexpr?.trim()) return null;
  try {
    return props.type === 'symbol'
      ? parseSymbol(props.compareSexpr)
      : parseFootprint(props.compareSexpr);
  } catch {
    return null;
  }
});

/** Whether we are in comparison mode */
const isCompareMode = computed(() => !!renderData.value && !!compareData.value);

/**
 * Merged view: primary on the left, comparison on the right, with a gap.
 * Comparison elements are rendered in a <g transform="translate(...)"> group.
 */
const mergedViewData = computed(() => {
  if (!isCompareMode.value || !renderData.value || !compareData.value) return null;

  const pBbox = renderData.value.bbox;
  const cBbox = compareData.value.bbox;

  // Gap between primary and comparison (in footprint units = mm)
  const gap = 3;

  // Offset to shift the comparison: move it so its left edge is
  // at the right edge of the primary + gap.
  const offsetX = pBbox.x2 - cBbox.x1 + gap;

  // Combined bounding box
  const x1 = pBbox.x1;
  const y1 = Math.min(pBbox.y1, cBbox.y1);
  const x2 = cBbox.x2 + offsetX;
  const y2 = Math.max(pBbox.y2, cBbox.y2);

  const w = x2 - x1;
  const h = y2 - y1;
  const padFrac = 0.10;
  const pad = Math.max(w * padFrac, h * padFrac, 0.5);

  // Divider line x
  const dividerX = pBbox.x2 + gap / 2;

  // Label positions (above the footprints)
  const labelY = y1 - pad * 0.3;
  const primaryLabelX = (pBbox.x1 + pBbox.x2) / 2;
  const compareLabelX = (cBbox.x1 + offsetX + cBbox.x2 + offsetX) / 2;
  const labelSize = Math.max(w * 0.025, 0.8);

  return {
    compareElements: compareData.value.elements,
    viewBox: `${x1 - pad} ${y1 - pad * 1.8} ${w + pad * 2} ${h + pad * 2.8}`,
    offsetX,
    dividerX,
    dividerY1: y1 - pad * 0.6,
    dividerY2: y2 + pad * 0.3,
    labelY,
    primaryLabelX,
    compareLabelX,
    labelSize,
  };
});

// -----------------------------------------------------------------------
// SVG text helpers
// -----------------------------------------------------------------------

function textAnchor(anchor: string): string {
  if (anchor.startsWith('start')) return 'start';
  if (anchor.startsWith('end')) return 'end';
  return 'middle';
}

function dominantBaseline(anchor: string): string {
  if (anchor.endsWith('top')) return 'text-before-edge';
  if (anchor.endsWith('bottom')) return 'text-after-edge';
  return 'central';
}

// -----------------------------------------------------------------------
// Dash array for courtyard / dashed strokes
// -----------------------------------------------------------------------

function dashArray(strokeWidth: number): string {
  const dash = strokeWidth * 4;
  const gap = strokeWidth * 3;
  return `${dash},${gap}`;
}

// -----------------------------------------------------------------------
// Zoom & pan state
// -----------------------------------------------------------------------

const svgRef = ref<SVGSVGElement | null>(null);
const zoomLevel = ref(1);           // 1 = fit-all (minimum zoom)
const panOffset = reactive({ x: 0, y: 0 }); // offset in SVG units
const MIN_ZOOM = 1;
const MAX_ZOOM = 20;
const ZOOM_FACTOR = 1.15;

// Reset zoom when the data changes
watch([() => props.sexpr, () => props.compareSexpr], () => {
  zoomLevel.value = 1;
  panOffset.x = 0;
  panOffset.y = 0;
});

/** Convert a mouse event position to SVG-coordinate space */
function clientToSvg(e: MouseEvent | WheelEvent): { x: number; y: number } | null {
  const svg = svgRef.value;
  if (!svg) return null;
  const rect = svg.getBoundingClientRect();
  const vb = zoomedViewBox.value;
  if (!vb) return null;
  // Fraction within the SVG element
  const fx = (e.clientX - rect.left) / rect.width;
  const fy = (e.clientY - rect.top) / rect.height;
  return {
    x: vb.x + fx * vb.w,
    y: vb.y + fy * vb.h,
  };
}

function onWheel(e: WheelEvent) {
  e.preventDefault();
  const svg = svgRef.value;
  if (!svg || !renderData.value) return;

  const cursor = clientToSvg(e);
  if (!cursor) return;

  const oldZoom = zoomLevel.value;
  let newZoom: number;
  if (e.deltaY < 0) {
    newZoom = Math.min(oldZoom * ZOOM_FACTOR, MAX_ZOOM);
  } else {
    newZoom = Math.max(oldZoom / ZOOM_FACTOR, MIN_ZOOM);
  }

  // Adjust pan so the point under the cursor stays fixed
  const scale = oldZoom / newZoom; // how the viewport size changes
  panOffset.x = cursor.x - scale * (cursor.x - panOffset.x);
  panOffset.y = cursor.y - scale * (cursor.y - panOffset.y);

  // Clamp zoom-out: prevent panning beyond the default view
  if (newZoom <= MIN_ZOOM) {
    panOffset.x = 0;
    panOffset.y = 0;
  }

  zoomLevel.value = newZoom;
}

// Middle-mouse drag for panning
const isPanning = ref(false);
const panStart = reactive({ x: 0, y: 0, ox: 0, oy: 0 });

function onPointerDown(e: PointerEvent) {
  // Middle button (1) or left button with Ctrl
  if (e.button === 1 || (e.button === 0 && e.ctrlKey)) {
    e.preventDefault();
    isPanning.value = true;
    panStart.x = e.clientX;
    panStart.y = e.clientY;
    panStart.ox = panOffset.x;
    panStart.oy = panOffset.y;
    (e.currentTarget as Element)?.setPointerCapture(e.pointerId);
  }
}

function onPointerMove(e: PointerEvent) {
  if (!isPanning.value || !svgRef.value) return;
  const svg = svgRef.value;
  const rect = svg.getBoundingClientRect();
  const vb = zoomedViewBox.value;
  if (!vb) return;
  // Convert pixel delta to SVG units
  const dxPx = e.clientX - panStart.x;
  const dyPx = e.clientY - panStart.y;
  const dxSvg = -(dxPx / rect.width) * vb.w;
  const dySvg = -(dyPx / rect.height) * vb.h;
  panOffset.x = panStart.ox + dxSvg;
  panOffset.y = panStart.oy + dySvg;
}

function onPointerUp(e: PointerEvent) {
  if (isPanning.value) {
    isPanning.value = false;
    (e.currentTarget as Element)?.releasePointerCapture(e.pointerId);
  }
}

function onDblClick() {
  // Double-click resets zoom to fit
  zoomLevel.value = 1;
  panOffset.x = 0;
  panOffset.y = 0;
}

/** The actual viewBox used by the SVG, accounting for zoom + pan */
const zoomedViewBox = computed(() => {
  // Choose base viewBox: merged (compare mode) or single
  const baseViewBoxStr = mergedViewData.value
    ? mergedViewData.value.viewBox
    : renderData.value?.viewBox;
  if (!baseViewBoxStr) return null;
  // Parse the base viewBox
  const parts = baseViewBoxStr.split(' ').map(Number);
  const baseX = parts[0], baseY = parts[1], baseW = parts[2], baseH = parts[3];
  // Zoomed dimensions
  const w = baseW / zoomLevel.value;
  const h = baseH / zoomLevel.value;
  // Center of the base view, shifted by pan
  const cx = baseX + baseW / 2 + panOffset.x;
  const cy = baseY + baseH / 2 + panOffset.y;
  return {
    x: cx - w / 2,
    y: cy - h / 2,
    w,
    h,
    str: `${cx - w / 2} ${cy - h / 2} ${w} ${h}`,
  };
});
</script>

<template>
  <div :class="['kicad-preview', type === 'symbol' ? 'bg-symbol' : 'bg-footprint']">
    <div v-if="!renderData" class="preview-empty">
      <span class="material-icons">visibility_off</span>
      No preview available
    </div>
    <svg
      v-else
      ref="svgRef"
      :viewBox="zoomedViewBox?.str ?? (mergedViewData?.viewBox ?? renderData.viewBox)"
      class="preview-svg"
      :class="{ 'is-panning': isPanning }"
      xmlns="http://www.w3.org/2000/svg"
      @wheel="onWheel"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @dblclick="onDblClick"
    >
      <!-- Grid origin crosshair (primary) -->
      <line x1="-0.6" y1="0" x2="0.6" y2="0" :stroke="type === 'symbol' ? '#CCCCCC' : '#404060'" stroke-width="0.06" />
      <line x1="0" y1="-0.6" x2="0" y2="0.6" :stroke="type === 'symbol' ? '#CCCCCC' : '#404060'" stroke-width="0.06" />

      <!-- Compare-mode: origin crosshair for the comparison footprint -->
      <template v-if="mergedViewData">
        <line :x1="mergedViewData.offsetX - 0.6" y1="0" :x2="mergedViewData.offsetX + 0.6" y2="0" stroke="#404060" stroke-width="0.06" />
        <line :x1="mergedViewData.offsetX" y1="-0.6" :x2="mergedViewData.offsetX" y2="0.6" stroke="#404060" stroke-width="0.06" />
      </template>

      <!-- ===== Primary elements ===== -->
      <template v-for="(el, idx) in renderData.elements" :key="'p-' + idx">
        <line v-if="el.kind === 'line'" :x1="el.x1" :y1="el.y1" :x2="el.x2" :y2="el.y2" :stroke="el.color" :stroke-width="el.stroke" stroke-linecap="round" :stroke-dasharray="el.dash ? dashArray(el.stroke) : undefined" />
        <rect v-else-if="el.kind === 'rect'" :x="el.x" :y="el.y" :width="el.w" :height="el.h" :stroke="el.color" :stroke-width="el.stroke" :fill="el.fill" stroke-linejoin="miter" />
        <circle v-else-if="el.kind === 'circle'" :cx="el.cx" :cy="el.cy" :r="Math.max(el.r, 0.01)" :stroke="el.stroke > 0 ? el.color : 'none'" :stroke-width="el.stroke" :fill="el.fill" />
        <path v-else-if="el.kind === 'arc'" :d="el.d" :stroke="el.color" :stroke-width="el.stroke" :fill="el.fill" stroke-linecap="round" :stroke-dasharray="el.dash ? dashArray(el.stroke) : undefined" />
        <polyline v-else-if="el.kind === 'poly'" :points="el.points" :stroke="el.color" :stroke-width="el.stroke" :fill="el.fill" stroke-linejoin="miter" stroke-linecap="round" />
        <g v-else-if="el.kind === 'pad'" :transform="el.angle ? `rotate(${el.angle},${el.x},${el.y})` : undefined">
          <template v-if="el.padType === 'smd' || el.padType === 'connect'">
            <rect v-if="el.shape !== 'circle'" :x="el.x - el.w / 2" :y="el.y - el.h / 2" :width="el.w" :height="el.h" :rx="el.rx || (el.shape === 'oval' ? Math.min(el.w, el.h) / 2 : 0)" :fill="el.color" fill-opacity="0.7" :stroke="el.color" :stroke-width="0.03" />
            <ellipse v-else :cx="el.x" :cy="el.y" :rx="el.w / 2" :ry="el.h / 2" :fill="el.color" fill-opacity="0.7" :stroke="el.color" :stroke-width="0.03" />
          </template>
          <template v-else>
            <rect v-if="el.shape === 'rect'" :x="el.x - el.w / 2" :y="el.y - el.h / 2" :width="el.w" :height="el.h" :fill="el.color" fill-opacity="0.7" :stroke="el.color" :stroke-width="0.03" />
            <ellipse v-else-if="el.shape === 'oval'" :cx="el.x" :cy="el.y" :rx="el.w / 2" :ry="el.h / 2" :fill="el.color" fill-opacity="0.7" :stroke="el.color" :stroke-width="0.03" />
            <circle v-else :cx="el.x" :cy="el.y" :r="Math.max(el.w, el.h) / 2" :fill="el.color" fill-opacity="0.7" :stroke="el.color" :stroke-width="0.03" />
            <circle v-if="el.drill > 0" :cx="el.x" :cy="el.y" :r="el.drill / 2" :fill="CLR.bgFootprint" :stroke="el.color" :stroke-width="0.03" />
          </template>
          <text :x="el.x" :y="el.y" text-anchor="middle" dominant-baseline="central" :font-size="Math.min(el.w, el.h) * 0.45" fill="white" font-weight="bold" font-family="sans-serif" style="pointer-events: none">{{ el.label }}</text>
        </g>
        <text v-else-if="el.kind === 'text'" :x="el.x" :y="el.y" :fill="el.color" :font-size="el.size" :font-weight="el.weight || 'normal'" :text-anchor="textAnchor(el.anchor)" :dominant-baseline="dominantBaseline(el.anchor)" :transform="el.angle ? `rotate(${-el.angle},${el.x},${el.y})` : undefined" font-family="sans-serif" style="pointer-events: none">{{ el.text }}</text>
      </template>

      <!-- ===== Comparison elements (translated) ===== -->
      <g v-if="mergedViewData" :transform="`translate(${mergedViewData.offsetX}, 0)`">
        <template v-for="(el, idx) in mergedViewData.compareElements" :key="'c-' + idx">
          <line v-if="el.kind === 'line'" :x1="el.x1" :y1="el.y1" :x2="el.x2" :y2="el.y2" :stroke="el.color" :stroke-width="el.stroke" stroke-linecap="round" :stroke-dasharray="el.dash ? dashArray(el.stroke) : undefined" />
          <rect v-else-if="el.kind === 'rect'" :x="el.x" :y="el.y" :width="el.w" :height="el.h" :stroke="el.color" :stroke-width="el.stroke" :fill="el.fill" stroke-linejoin="miter" />
          <circle v-else-if="el.kind === 'circle'" :cx="el.cx" :cy="el.cy" :r="Math.max(el.r, 0.01)" :stroke="el.stroke > 0 ? el.color : 'none'" :stroke-width="el.stroke" :fill="el.fill" />
          <path v-else-if="el.kind === 'arc'" :d="el.d" :stroke="el.color" :stroke-width="el.stroke" :fill="el.fill" stroke-linecap="round" :stroke-dasharray="el.dash ? dashArray(el.stroke) : undefined" />
          <polyline v-else-if="el.kind === 'poly'" :points="el.points" :stroke="el.color" :stroke-width="el.stroke" :fill="el.fill" stroke-linejoin="miter" stroke-linecap="round" />
          <g v-else-if="el.kind === 'pad'" :transform="el.angle ? `rotate(${el.angle},${el.x},${el.y})` : undefined">
            <template v-if="el.padType === 'smd' || el.padType === 'connect'">
              <rect v-if="el.shape !== 'circle'" :x="el.x - el.w / 2" :y="el.y - el.h / 2" :width="el.w" :height="el.h" :rx="el.rx || (el.shape === 'oval' ? Math.min(el.w, el.h) / 2 : 0)" :fill="el.color" fill-opacity="0.7" :stroke="el.color" :stroke-width="0.03" />
              <ellipse v-else :cx="el.x" :cy="el.y" :rx="el.w / 2" :ry="el.h / 2" :fill="el.color" fill-opacity="0.7" :stroke="el.color" :stroke-width="0.03" />
            </template>
            <template v-else>
              <rect v-if="el.shape === 'rect'" :x="el.x - el.w / 2" :y="el.y - el.h / 2" :width="el.w" :height="el.h" :fill="el.color" fill-opacity="0.7" :stroke="el.color" :stroke-width="0.03" />
              <ellipse v-else-if="el.shape === 'oval'" :cx="el.x" :cy="el.y" :rx="el.w / 2" :ry="el.h / 2" :fill="el.color" fill-opacity="0.7" :stroke="el.color" :stroke-width="0.03" />
              <circle v-else :cx="el.x" :cy="el.y" :r="Math.max(el.w, el.h) / 2" :fill="el.color" fill-opacity="0.7" :stroke="el.color" :stroke-width="0.03" />
              <circle v-if="el.drill > 0" :cx="el.x" :cy="el.y" :r="el.drill / 2" :fill="CLR.bgFootprint" :stroke="el.color" :stroke-width="0.03" />
            </template>
            <text :x="el.x" :y="el.y" text-anchor="middle" dominant-baseline="central" :font-size="Math.min(el.w, el.h) * 0.45" fill="white" font-weight="bold" font-family="sans-serif" style="pointer-events: none">{{ el.label }}</text>
          </g>
          <text v-else-if="el.kind === 'text'" :x="el.x" :y="el.y" :fill="el.color" :font-size="el.size" :font-weight="el.weight || 'normal'" :text-anchor="textAnchor(el.anchor)" :dominant-baseline="dominantBaseline(el.anchor)" :transform="el.angle ? `rotate(${-el.angle},${el.x},${el.y})` : undefined" font-family="sans-serif" style="pointer-events: none">{{ el.text }}</text>
        </template>
      </g>

      <!-- ===== Compare-mode overlay: divider line + labels ===== -->
      <template v-if="mergedViewData">
        <line :x1="mergedViewData.dividerX" :y1="mergedViewData.dividerY1" :x2="mergedViewData.dividerX" :y2="mergedViewData.dividerY2" stroke="#606080" :stroke-width="mergedViewData.labelSize * 0.06" stroke-dasharray="0.4,0.3" />
        <text :x="mergedViewData.primaryLabelX" :y="mergedViewData.labelY" text-anchor="middle" dominant-baseline="auto" :font-size="mergedViewData.labelSize" fill="#80a0ff" font-weight="bold" font-family="sans-serif" style="pointer-events: none">{{ primaryLabel || 'Imported' }}</text>
        <text :x="mergedViewData.compareLabelX" :y="mergedViewData.labelY" text-anchor="middle" dominant-baseline="auto" :font-size="mergedViewData.labelSize" fill="#80ffa0" font-weight="bold" font-family="sans-serif" style="pointer-events: none">{{ compareLabel || 'Selected' }}</text>
      </template>
    </svg>
  </div>
</template>

<style scoped>
.kicad-preview {
  width: 100%;
  aspect-ratio: 4 / 3;
  border-radius: var(--radius-sm, 4px);
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}

.kicad-preview.bg-symbol {
  background-color: #F5F4EF;
}

.kicad-preview.bg-footprint {
  background-color: #1a1a2e;
}

.preview-svg {
  width: 100%;
  height: 100%;
  cursor: grab;
  touch-action: none;
}

.preview-svg.is-panning {
  cursor: grabbing;
}

.preview-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.4rem;
  color: #606080;
  font-size: 0.78rem;
}

.preview-empty .material-icons {
  font-size: 1.5rem;
  opacity: 0.5;
}
</style>
