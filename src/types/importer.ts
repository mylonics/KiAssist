/** Shared types for the component importer flow. */

export interface ImportedFields {
  mpn: string;
  manufacturer: string;
  digikey_pn: string;
  mouser_pn: string;
  lcsc_pn: string;
  value: string;
  reference: string;
  footprint: string;
  datasheet: string;
  description: string;
  package: string;
  extra: Record<string, string>;
}

/** Alternative CAD model source discovered on Octopart. */
export interface CadSource {
  partner: string;
  has_symbol: boolean;
  has_footprint: boolean;
  has_3d_model: boolean;
  preview_symbol: string;
  preview_footprint: string;
  preview_3d: string;
  download_url: string;
}

export interface ImportedComponent {
  name: string;
  import_method: string;
  source_info: string;
  symbol_path: string;
  footprint_path: string;
  model_paths: string[];
  symbol_sexpr: string;
  footprint_sexpr: string;
  /** Base64-encoded STEP file data for 3D preview */
  step_data: string;
  fields: ImportedFields;
}
