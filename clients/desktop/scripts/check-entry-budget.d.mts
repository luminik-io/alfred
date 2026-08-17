export const ENTRY_RAW_LIMIT: number;
export const ENTRY_GZIP_LIMIT: number;

export function assertEntryBudget(size: {
  rawBytes: number;
  gzipBytes: number;
}): void;

export function checkBuiltEntry(distDir: string): {
  entryPath: string;
  rawBytes: number;
  gzipBytes: number;
};
