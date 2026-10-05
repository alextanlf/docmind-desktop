import { chmod, mkdtemp, mkdir, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { afterEach, describe, expect, it } from "vitest";

const run = promisify(execFile);
const root = join(__dirname, "../../..");
const temporaryDirectories: string[] = [];

afterEach(async () => {
  await Promise.all(
    temporaryDirectories
      .splice(0)
      .map((directory) => rm(directory, { recursive: true, force: true })),
  );
});

/**
 * Windows 平台的脚本分支无法在 macOS CI 上真实执行，但分支判据是纯 shell：
 * `uname -s` 的返回值。把 uname stub 成 MINGW64_NT-* 就能在真 bash 里
 * 跑到 Windows 分支，而不是在 TS 里复刻一份 shell 逻辑（那样测的就不是被测代码了）。
 */
async function fakeWindowsPath() {
  const directory = await mkdtemp(join(tmpdir(), "docmind-win-path-"));
  temporaryDirectories.push(directory);
  for (const command of ["node", "npm", "uv", "python", "netstat"]) {
    const file = join(directory, command);
    await writeFile(file, "#!/bin/sh\nexit 0\n", "utf8");
    await chmod(file, 0o755);
  }
  // 覆盖 uname：Git Bash / MSYS2 / Cygwin 的真实返回值都匹配这个模式。
  await writeFile(join(directory, "uname"), '#!/bin/sh\necho "MINGW64_NT-10.0-22631"\n', "utf8");
  await chmod(join(directory, "uname"), 0o755);
  return directory;
}

async function fakeRepoWithVenv(venvRelative: string) {
  const directory = await mkdtemp(join(tmpdir(), "docmind-win-repo-"));
  temporaryDirectories.push(directory);
  await mkdir(join(directory, "scripts", "lib"), { recursive: true });
  await symlink(
    join(root, "scripts", "lib", "runtime-checks.sh"),
    join(directory, "scripts", "lib", "runtime-checks.sh"),
  );
  const python = join(directory, "backend", ".venv", venvRelative);
  await mkdir(join(python, ".."), { recursive: true });
  await writeFile(python, "#!/bin/sh\n", "utf8");
  await chmod(python, 0o755);
  return directory;
}

function runShell(script: string, repo: string, path: string) {
  return run("/bin/bash", ["-c", script], {
    cwd: root,
    env: { ...process.env, PATH: `${path}:/bin:/usr/bin`, DOCMIND_REPO: root, DOCMIND_FAKE_ROOT: repo },
  }).then(
    (result) => ({ status: 0, stdout: result.stdout, stderr: result.stderr }),
    (error: any) => ({
      status: error.code ?? 1,
      stdout: error.stdout ?? "",
      stderr: error.stderr ?? "",
    }),
  );
}

describe("Windows platform branches in the shell scripts", () => {
  it("detects a Git Bash host", async () => {
    const path = await fakeWindowsPath();
    const result = await runShell(
      `source "$DOCMIND_REPO/scripts/lib/runtime-checks.sh"
       if is_windows_host; then echo yes; else echo no; fi`,
      root,
      path,
    );
    expect(result.stdout.trim()).toBe("yes");
  });

  // venv 的入口目录两平台不同。判据写死成 bin/python 会让 Windows 开发态
  // 每次都误判「venv 缺失」并重跑 setup-backend.sh。
  it("looks for python.exe under Scripts/ instead of bin/python", async () => {
    const path = await fakeWindowsPath();
    const repo = await fakeRepoWithVenv(join("Scripts", "python.exe"));
    const result = await runShell(
      `source "$DOCMIND_REPO/scripts/lib/runtime-checks.sh"
       if ensure_backend_venv "$DOCMIND_FAKE_ROOT"; then echo found; else echo missing; fi`,
      repo,
      path,
    );
    expect(result.stdout.trim()).toBe("found");
  });

  it("reports a POSIX-only venv as missing on Windows", async () => {
    const path = await fakeWindowsPath();
    const repo = await fakeRepoWithVenv(join("bin", "python"));
    const result = await runShell(
      `source "$DOCMIND_REPO/scripts/lib/runtime-checks.sh"
       if ensure_backend_venv "$DOCMIND_FAKE_ROOT"; then echo found; else echo missing; fi`,
      repo,
      path,
    );
    expect(result.stdout.trim()).toBe("missing");
  });

  // lsof 在 Git Bash 里不存在，端口检查会直接 die 掉整个 dev 流程。
  it("falls back to netstat when lsof is unavailable", async () => {
    const path = await fakeWindowsPath();
    const result = await runShell(
      `source "$DOCMIND_REPO/scripts/lib/runtime-checks.sh"
       if check_port_available 18900; then echo free; else echo occupied; fi`,
      root,
      path,
    );
    expect(result.stdout.trim()).toBe("free");
  });

  // Windows 的 Electron 运行时是 dist/electron.exe，没有 .app 包结构。
  it("accepts dist/electron.exe as the local Electron runtime", async () => {
    const path = await fakeWindowsPath();
    const repo = await mkdtemp(join(tmpdir(), "docmind-win-electron-"));
    temporaryDirectories.push(repo);
    const exe = join(repo, "node_modules", "electron", "dist", "electron.exe");
    await mkdir(join(exe, ".."), { recursive: true });
    await writeFile(exe, "#!/bin/sh\n", "utf8");
    await chmod(exe, 0o755);
    const result = await runShell(
      `source "$DOCMIND_REPO/scripts/lib/runtime-checks.sh"
       resolve_electron_exec "$DOCMIND_FAKE_ROOT"
       echo "resolved:$?"`,
      repo,
      path,
    );
    expect(result.stdout).toContain("resolved:0");
  });
});

describe("packaged runtime layout expectations", () => {
  // 打包契约：main 进程按平台在 runtime 目录里找解释器，脚本必须产出对应名字。
  // 两边任一侧改了名字而另一侧没改，Windows 包会在启动时静默失败。
  it("keeps the interpreter candidates aligned with the build script", async () => {
    const contract = await readFile(join(root, "electron/main/backend-runtime-contract.ts"), "utf8");
    const buildScript = await readFile(
      join(root, "backend/scripts/build-backend-runtime.sh"),
      "utf8",
    );
    // Windows 侧：候选 python.exe，构建脚本也必须把 exe 留在 runtime 顶层。
    expect(contract).toContain('"python.exe"');
    expect(buildScript).toContain('runtime_python="$runtime_dir/python.exe"');
    // POSIX 侧：候选 bin/python3，构建脚本必须建这个符号链接。
    expect(contract).toContain('join("bin", "python3")');
    expect(buildScript).toContain('ln -sf "python${python_version}" "$runtime_dir/bin/python3"');
  });

  it("keeps the entry script name aligned between contract and build script", async () => {
    const contract = await readFile(join(root, "electron/main/backend-runtime-contract.ts"), "utf8");
    const buildScript = await readFile(
      join(root, "backend/scripts/build-backend-runtime.sh"),
      "utf8",
    );
    expect(contract).toContain('RUNTIME_ENTRY_ARGS = ["run.py"]');
    expect(buildScript).toContain('> "$runtime_dir/run.py"');
  });
});
