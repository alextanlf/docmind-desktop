import { chmod, mkdtemp, mkdir, rm, symlink, writeFile } from "node:fs/promises";
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

async function fakeRoot(options: {
  nodeModules?: boolean;
  venv?: boolean;
  portOccupied?: boolean;
}) {
  const directory = await mkdtemp(join(tmpdir(), "docmind-dev-script-"));
  temporaryDirectories.push(directory);
  await mkdir(join(directory, "scripts", "lib"), { recursive: true });
  await symlink(
    join(root, "scripts", "lib", "runtime-checks.sh"),
    join(directory, "scripts", "lib", "runtime-checks.sh"),
  );
  await symlink(
    join(root, "scripts", "setup-backend.sh"),
    join(directory, "scripts", "setup-backend.sh"),
  );
  if (options.nodeModules) {
    await mkdir(join(directory, "node_modules", ".bin"), { recursive: true });
    const electron = join(directory, "node_modules", ".bin", "electron");
    await writeFile(electron, "#!/bin/sh\n", "utf8");
    await chmod(electron, 0o755);
  }
  if (options.venv) {
    await mkdir(join(directory, "backend", ".venv", "bin"), { recursive: true });
    const python = join(directory, "backend", ".venv", "bin", "python");
    await writeFile(python, "#!/bin/sh\n", "utf8");
    await chmod(python, 0o755);
  }
  return directory;
}

async function fakePath(commandNames: string[]) {
  const directory = await mkdtemp(join(tmpdir(), "docmind-path-"));
  temporaryDirectories.push(directory);
  for (const command of commandNames) {
    const file = join(directory, command);
    await writeFile(file, "#!/bin/sh\nexit 0\n", "utf8");
    await chmod(file, 0o755);
  }
  return directory;
}

async function runDevScript(options: { venv?: boolean; nodeModules?: boolean }) {
  const fakeRepo = await fakeRoot(options);
  const path = await fakePath(["node", "npm", "python3", "uv", "lsof"]);
  await writeFile(join(path, "lsof"), "#!/bin/sh\nexit 1\n", "utf8");
  await chmod(join(path, "lsof"), 0o755);
  return run("/bin/bash", [join(root, "scripts/dev.sh")], {
    cwd: root,
    env: {
      ...process.env,
      PATH: `${path}:/bin:/usr/bin`,
      DOCMIND_REPO_ROOT: fakeRepo,
      DOCMIND_DEV_DRY_RUN: "1",
    },
  }).then(
    (result) => ({ status: 0, stdout: result.stdout, stderr: result.stderr }),
    (error: any) => ({
      status: error.code ?? 1,
      stdout: error.stdout ?? "",
      stderr: error.stderr ?? "",
    }),
  );
}

async function runChecks(options: {
  commands?: string[];
  nodeModules?: boolean;
  venv?: boolean;
  setupExit?: number;
  portOccupied?: boolean;
}) {
  const fakeRepo = await fakeRoot(options);
  const path = await fakePath(options.commands ?? ["node", "npm", "python3", "uv"]);
  const script = `
    source "$DOCMIND_REPO/scripts/lib/runtime-checks.sh"
    check_local_dependencies "$DOCMIND_FAKE_ROOT"
    if ! ensure_backend_venv "$DOCMIND_FAKE_ROOT"; then
      echo setup-backend.sh
      exit "${options.setupExit ?? 0}"
    fi
    if [[ "${options.portOccupied ? "1" : "0"}" == "1" ]]; then
      echo "18900 已被其他服务占用" >&2
      exit 1
    fi
  `;
  return run("/bin/bash", ["-c", script], {
    cwd: root,
    env: {
      ...process.env,
      PATH: `${path}:/bin:/usr/bin`,
      DOCMIND_REPO: root,
      DOCMIND_FAKE_ROOT: fakeRepo,
    },
  }).then(
    (result) => ({ status: 0, stdout: result.stdout, stderr: result.stderr }),
    (error: any) => ({
      status: error.code ?? 1,
      stdout: error.stdout ?? "",
      stderr: error.stderr ?? "",
    }),
  );
}

describe("local development startup diagnostics", () => {
  it("reports a missing command before starting Electron", async () => {
    const result = await runChecks({ commands: ["node", "npm", "python3"] });
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain("缺少命令：uv");
  });

  it("reports missing Electron dependencies with the npm install action", async () => {
    const result = await runChecks({ nodeModules: false, venv: true });
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain("请先运行 npm install");
  });

  it("runs setup only when the backend virtual environment is absent", async () => {
    const existing = await runChecks({ nodeModules: true, venv: true });
    const missing = await runChecks({ nodeModules: true, venv: false, setupExit: 0 });
    expect(existing.stdout).not.toContain("setup-backend.sh");
    expect(missing.stdout).toContain("setup-backend.sh");
  });

  it("rejects an occupied fixed port", async () => {
    const result = await runChecks({ nodeModules: true, venv: true, portOccupied: true });
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain("18900 已被其他服务占用");
  });

  it("delegates to desktop:dev after all checks pass", async () => {
    const result = await runDevScript({ nodeModules: true, venv: true });
    expect(result.status).toBe(0);
    expect(result.stdout).toContain("desktop:dev");
  });
});
