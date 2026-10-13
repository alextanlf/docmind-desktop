import { createHmac, timingSafeEqual } from "node:crypto";

/**
 * 路径能力（capability）：主进程只对"文件对话框真的返回过的路径"签名。
 *
 * 后端只读签名对得上的路径，于是渲染层（插件的 UI 就是渲染层代码）依然没有任何办法
 * 指名一个它自己挑的文件 —— 这是导入改成直读原件之后必须保住的那条性质。
 *
 * 与后端 `app/document/path_tokens.py` 是一对**跨语言实现**：算法、消息构造、编码
 * 任何一处改动都必须同步改另一边，`path-tokens.test.ts` 与
 * `tests/document/test_path_tokens.py` 共用同一个测试向量来钉住这件事。
 *
 * 签的是路径字符串的原始 UTF-8 字节，**两边都不做规范化**：TS 与 Python 的路径解析在
 * 符号链接、大小写、macOS 的 Unicode 归一化上并不一致，各自规范化会让签名保护的
 * 不再是同一个文件。理由详见 Python 那边的模块注释。
 */

/** 签名域。分开是为了让"授权一个目录"不能被当成"授权某个文件路径"使用。 */
export const FILE_DOMAIN = "linked-file";
export const DIRECTORY_DOMAIN = "linked-directory";

export function pathToken(secret: string, domain: string, path: string): string {
  return createHmac("sha256", Buffer.from(secret, "utf-8"))
    .update(Buffer.from(`${domain}:${path}`, "utf-8"))
    .digest("base64url");
}

/**
 * 验签：主进程**自己**确认"这个路径是后端签过的"，而不是照后端说的打开文件。
 *
 * 为什么主进程也要会验：`documents:openOriginal` 会把这个路径交给系统程序打开 —— 那是
 * 真正动文件系统的一步。若主进程只是"请求后端 → 拿 path → 打开"，那么权限实际上落在
 * "谁能影响这次请求"上（渲染层能影响的正是 documentId），签名就白签了。
 *
 * 比较用 `timingSafeEqual`：与后端一致，不给"逐字节猜签名"留时间差。长度不同直接返回
 * false —— `timingSafeEqual` 对不等长会抛错，而这个分支本身就泄露"长度不对"，长度不是秘密。
 */
export function pathTokenMatches(
  secret: string,
  domain: string,
  path: string,
  token: string,
): boolean {
  const expected = Buffer.from(pathToken(secret, domain, path), "utf-8");
  const provided = Buffer.from(token, "utf-8");
  if (expected.length !== provided.length) return false;
  return timingSafeEqual(expected, provided);
}
