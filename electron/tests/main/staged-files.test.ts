import { describe, expect, it, beforeEach, afterEach } from "vitest";
import { mkdir, mkdtemp, writeFile, symlink, rm, readdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { StagedFileService, type ImportFormat } from "../../main/staged-files";
import { FILE_DOMAIN, pathToken } from "../../main/path-tokens";

/** 暂存目录里现在有什么。链接型导入之后它必须是空的。 */
async function stagingEntries(dataDir: string): Promise<string[]> {
  return (await readdir(join(dataDir, "imports", "staging")).catch(() => [])).sort();
}

/**
 * Format declarations standing in for what the backend serves.
 *
 * Written out here rather than imported from the main process on purpose: the
 * service must be driven entirely by whatever it is handed, so the test states
 * the input instead of sharing a constant with the implementation.
 */
const PDF: ImportFormat = {
  name: "pdf",
  label: "PDF",
  extensions: [".pdf"],
  mediaType: "application/pdf",
  maxBytes: 100 * 1024 * 1024,
};
const DOCX: ImportFormat = {
  name: "docx",
  label: "Word 文档",
  extensions: [".docx"],
  mediaType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  maxBytes: 20 * 1024 * 1024,
};

describe("链接型原件（chooseAndLink / signGrantedPath）", () => {
  it("returns null when the file dialog is cancelled", async () => {
    const service = new StagedFileService({
      dataDir: dir,
      showOpenDialog: async () => ({ canceled: true, filePaths: [] }),
    });

    await expect(service.chooseAndLink(PDF)).resolves.toBeNull();
  });

  it("offers the declared label and bare extensions to the dialog", async () => {
    let received: unknown;
    const service = new StagedFileService({
      dataDir: dir,
      showOpenDialog: async (options) => {
        received = options;
        return { canceled: true, filePaths: [] };
      },
    });

    await service.chooseAndLink(DOCX);

    // 后端声明的是带点的扩展名，对话框要的是不带点的。
    expect(received).toEqual({
      properties: ["openFile"],
      filters: [{ name: "Word 文档", extensions: ["docx"] }],
    });
  });

  const TOKEN = "session-token-for-test";
  // 自己的临时目录：这一组不能借用上面那个 describe 的 `dir`，否则测试之间的
  // 生命周期互相牵连（一处 afterEach 删掉另一个还在用的目录）。
  let dir: string;
  beforeEach(async () => {
    dir = await mkdtemp(join(tmpdir(), "docmind-linked-"));
  });
  afterEach(async () => {
    await rm(dir, { recursive: true, force: true });
  });

  /** `token: null` 表示"故意不给密钥"。不能传 undefined —— 那会触发默认参数。 */
  function serviceWith(shown: string | null, token: string | null = TOKEN) {
    return new StagedFileService({
      dataDir: dir,
      ...(token === null ? {} : { sessionToken: token }),
      showOpenDialog: async () =>
        shown === null
          ? { canceled: true, filePaths: [] }
          : { canceled: false, filePaths: [shown] },
    });
  }

  it("returns the path without writing a single byte into the data directory", async () => {
    const src = join(dir, "论文.pdf");
    await writeFile(src, "%PDF-1.7\nbody");
    const service = serviceWith(src);

    const picked = await service.chooseAndLink(PDF);

    expect(picked).toMatchObject({ kind: "linked_file", path: src, name: "论文.pdf" });
    // 关键：数据目录里没有出现任何暂存副本。
    await expect(stagingEntries(dir)).resolves.toEqual([]);
  });

  it("signs a path the dialog returned, and only that path", async () => {
    const src = join(dir, "论文.pdf");
    await writeFile(src, "%PDF-1.7\nbody");
    const service = serviceWith(src);
    await service.chooseAndLink(PDF);

    expect(service.signGrantedPath(src)).toBe(pathToken(TOKEN, FILE_DOMAIN, src));
    // 渲染层编一个路径过来 —— 没登记过，签不出名字，后端也就读不到东西。
    expect(() => service.signGrantedPath(join(dir, "id_rsa"))).toThrow(/not granted/);
  });

  it("refuses to sign when no dialog has run yet", () => {
    const service = serviceWith(null);

    expect(() => service.signGrantedPath(join(dir, "anything.pdf"))).toThrow(/not granted/);
  });

  it("fails loudly when there is no session token to sign with", async () => {
    const src = join(dir, "论文.pdf");
    await writeFile(src, "%PDF-1.7\nbody");
    // 生产接线漏传 token 时，宁可在这里炸，也不要静默地"授权成功但后端读不了"。
    const service = serviceWith(src, null);
    await service.chooseAndLink(PDF);

    expect(() => service.signGrantedPath(src)).toThrow(/Signing unavailable/);
  });

  it("validates extension, regular-file and size the same way staging does", async () => {
    const wrongExtension = join(dir, "notes.txt");
    await writeFile(wrongExtension, "hello");
    await expect(serviceWith(wrongExtension).chooseAndLink(PDF)).rejects.toThrow(
      /Unsupported extension/,
    );

    // 目录名也带 .pdf：否则先被扩展名检查挡下，测不到"必须是普通文件"这一条。
    const directory = join(dir, "其实是个目录.pdf");
    await mkdir(directory, { recursive: true });
    await expect(serviceWith(directory).chooseAndLink(PDF)).rejects.toThrow(
      /Regular file required/,
    );

    const tooBig = join(dir, "big.pdf");
    await writeFile(tooBig, Buffer.alloc(2048));
    await expect(serviceWith(tooBig).chooseAndLink({ ...PDF, maxBytes: 1024 })).rejects.toThrow(
      /size limit/,
    );
  });

  it("lets the user link a symlink to the real file", async () => {
    const real = join(dir, "真论文.pdf");
    await writeFile(real, "%PDF-1.7\nbody");
    const link = join(dir, "桌面上的论文.pdf");
    await symlink(real, link);
    const service = serviceWith(link);

    const picked = await service.chooseAndLink(PDF);

    // 用户选的是那串路径，签名保护的也是那串路径 —— 不必替他解析成真实文件。
    expect(picked?.path).toBe(link);
    expect(service.signGrantedPath(link)).toBe(pathToken(TOKEN, FILE_DOMAIN, link));
  });
});
