/**
 * pre-commit 钩子本身的测试。
 *
 * 为什么值得测：钩子是唯一在"提交那一刻"拦住人的东西，而它拦的是**我犯过的那类错**
 * （按目录跑格式化、把无关文件卷进提交）。一个悄悄失效的钩子不会让任何测试变红 ——
 * 除非有人真的提交了不合格式的东西，而那正是它本该拦住的时刻。
 *
 * 做法照 `dev-script.test.ts`：在一个临时仓库里跑真实脚本（用软链把钩子、prettier 与
 * prettier 配置接进去），而不是把脚本内容读出来做字符串断言。
 */
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import { afterEach, describe, expect, it } from "vitest";

const run = promisify(execFile);
const root = join(__dirname, "../../..");

/**
 * 假密钥**在运行时拼出来**，不让密钥形态的字面量出现在文件里。
 *
 * 这个仓库的 pre-commit 会扫密钥形态，而这条用例恰恰需要一个像密钥的字符串。绕过钩子
 * （`DOCMIND_SKIP_SECRET_SCAN=1`）是最差的选择：它同时会放过同一个提交里真的密钥。
 * 拼接既保住了扫描器，又让"这里为什么这么写"看得见。
 */
const FAKE_KEY = ["sk", "abcdefghijklmnopqrstuvwx"].join("-");
const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporaryDirectories
      .splice(0)
      .map((directory) => rm(directory, { recursive: true, force: true })),
  );
});

async function fakeRepo(): Promise<string> {
  const directory = await mkdtemp(join(tmpdir(), "docmind-pre-commit-"));
  temporaryDirectories.push(directory);
  await mkdir(join(directory, ".githooks"), { recursive: true });
  await mkdir(join(directory, "node_modules", ".bin"), { recursive: true });
  await symlink(join(root, ".githooks", "pre-commit"), join(directory, ".githooks", "pre-commit"));
  await symlink(
    join(root, "node_modules", ".bin", "prettier"),
    join(directory, "node_modules", ".bin", "prettier"),
  );
  await symlink(join(root, ".prettierrc.json"), join(directory, ".prettierrc.json"));
  await run("git", ["init", "-q"], { cwd: directory });
  return directory;
}

async function stage(directory: string, name: string, content: string): Promise<void> {
  await writeFile(join(directory, name), content, "utf8");
  await run("git", ["add", name], { cwd: directory });
}

async function runHook(
  directory: string,
  env: Record<string, string> = {},
): Promise<{ code: number; output: string }> {
  try {
    const { stdout, stderr } = await run("bash", [join(directory, ".githooks", "pre-commit")], {
      cwd: directory,
      env: { ...process.env, ...env },
    });
    return { code: 0, output: stdout + stderr };
  } catch (error) {
    const failure = error as { code?: number; stdout?: string; stderr?: string };
    return { code: failure.code ?? 1, output: `${failure.stdout ?? ""}${failure.stderr ?? ""}` };
  }
}

describe("pre-commit 钩子", () => {
  it("拦下格式不合的暂存文件，并给出正确的命令", async () => {
    const directory = await fakeRepo();
    await stage(directory, "ugly.ts", "export const ugly = {a:1,   b:2}\n");

    const result = await runHook(directory);

    expect(result.code).not.toBe(0);
    // 提示必须指向"只格式化你改过的文件"，而不是让人去跑 `prettier --write .` ——
    // 后者正是这个检查要防的事。
    expect(result.output).toContain("npx prettier --write");
    expect(result.output).toContain("不要按目录");
    expect(result.output).toContain("ugly.ts");
  });

  it("格式正确时放行", async () => {
    const directory = await fakeRepo();
    await stage(directory, "clean.ts", "export const clean = { a: 1, b: 2 };\n");

    const result = await runHook(directory);

    expect(result.code).toBe(0);
  });

  it("没有暂存文件时放行", async () => {
    const directory = await fakeRepo();

    const result = await runHook(directory);

    expect(result.code).toBe(0);
  });

  it("不认识的文件类型不会被当成格式错误", async () => {
    // prettier 对 .py 没有解析器。少了 --ignore-unknown，这个钩子会把每一次包含
    // Python 改动的提交都拦下来 —— 那种误报会让人立刻开始用环境变量绕过它。
    const directory = await fakeRepo();
    await stage(directory, "script.py", "def f( a ,b ):\n  return  a+b\n");

    const result = await runHook(directory);

    expect(result.code).toBe(0);
  });

  it("可以用 DOCMIND_SKIP_FORMAT_CHECK=1 显式绕过", async () => {
    const directory = await fakeRepo();
    await stage(directory, "ugly.ts", "export const ugly = {a:1,   b:2}\n");

    const result = await runHook(directory, { DOCMIND_SKIP_FORMAT_CHECK: "1" });

    expect(result.code).toBe(0);
    expect(result.output).toContain("DOCMIND_SKIP_FORMAT_CHECK=1");
  });

  it("原有的密钥扫描仍然生效（加了新检查之后没被改坏）", async () => {
    const directory = await fakeRepo();
    // 故意写成格式正确的：这样失败只可能来自密钥扫描。
    await stage(directory, "leak.ts", `export const key = "${FAKE_KEY}";\n`);

    const result = await runHook(directory, { DOCMIND_SKIP_FORMAT_CHECK: "1" });

    expect(result.code).not.toBe(0);
    expect(result.output).toContain("疑似密钥");
  });
});
