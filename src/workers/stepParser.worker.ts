/**
 * Web Worker for parsing STEP files using occt-import-js.
 *
 * Accepts messages with { b64: string } and returns the parsed result
 * (mesh data as transferable arrays) so the main thread stays responsive.
 */

let occtInstance: any = null;

async function getOcct() {
  if (!occtInstance) {
    const occtModule = await import('occt-import-js');
    const occtFactory = occtModule.default || occtModule;
    occtInstance = await occtFactory({
      locateFile: (name: string) => {
        if (name.endsWith('.wasm')) return '/occt-import-js.wasm';
        return name;
      },
    });
  }
  return occtInstance;
}

/** Decode base-64 string → Uint8Array */
function b64ToUint8(b64: string): Uint8Array {
  const bin = atob(b64);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return buf;
}

/**
 * Serialise the OCCT result into plain transferable objects so we can
 * send them back to the main thread without cloning overhead.
 */
function serialiseResult(result: any) {
  if (!result || !result.success || !result.meshes) return null;
  const meshes = [];
  const transfers: ArrayBuffer[] = [];
  for (const mesh of result.meshes) {
    const entry: any = { color: mesh.color ?? null };

    if (mesh.attributes?.position?.array) {
      const arr = new Float32Array(mesh.attributes.position.array);
      entry.position = arr;
      transfers.push(arr.buffer);
    }
    if (mesh.attributes?.normal?.array) {
      const arr = new Float32Array(mesh.attributes.normal.array);
      entry.normal = arr;
      transfers.push(arr.buffer);
    }
    if (mesh.index?.array) {
      const arr = new Uint32Array(mesh.index.array);
      entry.index = arr;
      transfers.push(arr.buffer);
    }
    meshes.push(entry);
  }
  return { meshes, transfers };
}

self.onmessage = async (e: MessageEvent) => {
  const { id, b64 } = e.data;
  try {
    const occt = await getOcct();
    const fileBuffer = b64ToUint8(b64);
    const result = occt.ReadStepFile(fileBuffer, { linearUnit: 'millimeter' });
    const serialised = serialiseResult(result);
    if (serialised) {
      (self as any).postMessage({ id, meshes: serialised.meshes }, serialised.transfers);
    } else {
      (self as any).postMessage({ id, meshes: null });
    }
  } catch (err: any) {
    (self as any).postMessage({ id, error: err?.message ?? String(err) });
  }
};
