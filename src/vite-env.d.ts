/// <reference types="vite/client" />

declare module "*.vue" {
  import type { DefineComponent } from "vue";
  const component: DefineComponent<{}, {}, any>;
  export default component;
}

declare module "occt-import-js" {
  interface OcctImportResult {
    success: boolean;
    root: {
      name: string;
      meshes: number[];
      children: any[];
    };
    meshes: Array<{
      name: string;
      color?: [number, number, number];
      brep_faces: Array<{ first: number; last: number; color: [number, number, number] | null }>;
      attributes: {
        position: { array: number[] };
        normal?: { array: number[] };
      };
      index: { array: number[] };
    }>;
  }

  interface OcctInstance {
    ReadStepFile(content: Uint8Array, params: any): OcctImportResult;
    ReadBrepFile(content: Uint8Array, params: any): OcctImportResult;
    ReadIgesFile(content: Uint8Array, params: any): OcctImportResult;
  }

  function occtimportjs(options?: { locateFile?: (name: string) => string }): Promise<OcctInstance>;
  export default occtimportjs;
}
