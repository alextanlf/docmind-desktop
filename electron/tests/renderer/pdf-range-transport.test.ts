import { describe, expect, it, vi } from "vitest";
import {
  ORIGINAL_CHUNK_BYTES,
  createOriginalRangeTransport,
  decodeBase64Bytes,
  firstChunkRange,
} from "../../renderer/src/features/repositories/pdf-range-transport";

describe("decodeBase64Bytes", () => {
  it("round-trips arbitrary bytes, not just ASCII", () => {
    // 用全部 256 个取值：只测 ASCII 会漏掉 charCodeAt 的符号问题。
    const bytes = new Uint8Array(256);
    for (let index = 0; index < 256; index += 1) bytes[index] = index;
    expect(decodeBase64Bytes(btoa(String.fromCharCode(...bytes)))).toEqual(bytes);
  });

  it("yields an empty array for empty input", () => {
    expect(decodeBase64Bytes("")).toEqual(new Uint8Array());
  });
});

describe("firstChunkRange", () => {
  it("asks for the first chunk when the document is larger", () => {
    expect(firstChunkRange(ORIGINAL_CHUNK_BYTES * 4)).toEqual([0, ORIGINAL_CHUNK_BYTES - 1]);
  });

  it("clamps to the file end when the document is smaller than one chunk", () => {
    expect(firstChunkRange(10)).toEqual([0, 9]);
    expect(firstChunkRange(1)).toEqual([0, 0]);
  });

  it("never produces a negative end for an empty file", () => {
    expect(firstChunkRange(0)).toEqual([0, 0]);
  });
});

describe("createOriginalRangeTransport", () => {
  function setup(
    readChunk: (begin: number, end: number) => Promise<{ data: string; total: number }>,
  ) {
    const received: Array<[number, number[]]> = [];
    const onError = vi.fn();
    const transport = createOriginalRangeTransport({
      total: 4096,
      initialData: new Uint8Array(),
      readChunk,
      onError,
    });
    transport.addRangeListener((begin: number, chunk: Uint8Array) => {
      received.push([begin, Array.from(chunk)]);
    });
    return { transport, received, onError };
  }

  it("translates the half-open pdf.js range into an inclusive HTTP range", async () => {
    const readChunk = vi.fn().mockResolvedValue({ data: "AQID", total: 4096 });
    const { transport, received } = setup(readChunk);

    transport.requestDataRange(10, 13);

    await vi.waitFor(() => expect(received).toEqual([[10, [1, 2, 3]]]));
    // pdf.js 传的是开区间 end，HTTP Range 要闭区间。
    expect(readChunk).toHaveBeenCalledWith(10, 12);
  });

  it("delivers data asynchronously so pdf.js has already registered its reader", async () => {
    // getRangeReader 的顺序是：new reader → requestDataRange → push 进 _rangeReaders。
    // 同步回调会撞上 _onReceiveData 里 "no range reader found" 的断言。
    const readChunk = vi.fn().mockResolvedValue({ data: "AQID", total: 4096 });
    const { transport, received } = setup(readChunk);

    transport.requestDataRange(0, 3);

    expect(received).toEqual([]);
    await vi.waitFor(() => expect(received).toHaveLength(1));
  });

  it("does not advertise the stream as finished", () => {
    // initialData 只覆盖首片；progressiveDone 若为 true，pdf.js 会以为整份文件已读完。
    const { transport } = setup(vi.fn().mockResolvedValue({ data: "", total: 4096 }));
    expect(transport.progressiveDone).toBe(false);
  });

  it("routes failures to onError instead of leaving the reader pending forever", async () => {
    const failure = new Error("范围请求失败");
    const readChunk = vi.fn().mockRejectedValue(failure);
    const { transport, received, onError } = setup(readChunk);

    transport.requestDataRange(0, 3);

    await vi.waitFor(() => expect(onError).toHaveBeenCalledWith(failure));
    expect(received).toEqual([]);
  });
});
