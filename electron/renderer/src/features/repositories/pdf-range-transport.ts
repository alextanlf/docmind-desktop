import type { DocumentOriginalChunk } from "../../../../shared/contracts";
import { PDFDataRangeTransport } from "./pdf-runtime";

/**
 * 每片字节数。与 pdf.js 自己的 `DEFAULT_RANGE_CHUNK_SIZE`（64KB）对齐，
 * 首片同时充当 `initialData`，避免打开文档时多跑一次往返。
 */
export const ORIGINAL_CHUNK_BYTES = 64 * 1024;

export type ReadOriginalChunk = (begin: number, end: number) => Promise<DocumentOriginalChunk>;

export function decodeBase64Bytes(encoded: string): Uint8Array {
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

/** 首片请求区间；文档比一片还小时夹到文件末尾。 */
export function firstChunkRange(total: number): [number, number] {
  return [0, Math.max(0, Math.min(ORIGINAL_CHUNK_BYTES, total) - 1)];
}

export function createOriginalRangeTransport(options: {
  total: number;
  initialData: Uint8Array;
  readChunk: ReadOriginalChunk;
  onError: (error: unknown) => void;
}): PDFDataRangeTransport {
  const { total, initialData, readChunk, onError } = options;
  // progressiveDone 刻意留默认 false：initialData 只覆盖首片而非整个文件，
  // 置 true 会让 pdf.js 认为流已读完，后续页拿不到数据。
  const transport = new PDFDataRangeTransport(total, initialData);
  transport.requestDataRange = (begin, end) => {
    // 必须异步回调 onDataRange：pdf.js 的 getRangeReader 是先调用
    // requestDataRange、再把 reader 推进 _rangeReaders，
    // 同步回调会撞上 `_onReceiveData` 里 "no range reader found" 的断言。
    void (async () => {
      try {
        // HTTP Range 是闭区间，pdf.js 传入的 end 是开区间。
        const chunk = await readChunk(begin, end - 1);
        transport.onDataRange(begin, decodeBase64Bytes(chunk.data));
      } catch (error) {
        onError(error);
      }
    })();
  };
  return transport;
}
