/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the scan API. Unset means the app runs on placeholder data. */
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
