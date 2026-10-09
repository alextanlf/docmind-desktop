#!/usr/bin/env bash
# 本地打包：构建自包含后端 runtime + 内置模型，再交给 electron-builder 出包。
#
# 同一份脚本支持两个平台，靠 uname -s 分支（Windows 需在 Git Bash / MSYS2 里跑）：
#   macOS   → dist/mac-arm64/DocMind.app，安装到 /Applications 并 ad-hoc 重签
#   Windows → dist/win-unpacked/，产出 NSIS 安装包 + 免安装 portable exe
#
# 为什么 Windows 分支不做重签：macOS 侧必须重签是因为 electron-builder 以
# identity: null 免证书构建，产物沿用了 Electron 运行时的旧签名，与实际打包
# 进去的 1.1G 资源不一致，macOS 会直接拒绝执行。Windows 没有等价的
# 「资源封印」校验，NSIS 自身也不要求重签才能运行。
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend_dir="$root/backend"
electron_dist="$root/build/electron-dist"

# ── 平台判定 ────────────────────────────────────────────────────────────────
os_name="$(uname -s)"
case "$os_name" in
  MINGW*|MSYS*|CYGWIN*) is_windows=1 ;;
  *) is_windows=0 ;;
esac

if [[ "$is_windows" = 1 ]]; then
  # Windows 上 venv 的脚本目录是 Scripts/，不是 bin/。
  backend_python="${DOCMIND_BACKEND_PYTHON:-$backend_dir/.venv/Scripts/python.exe}"
  electron_app="${DOCMIND_ELECTRON_APP:-$root/node_modules/electron/dist}"
  appdata="${APPDATA:-$HOME/AppData/Roaming}"
else
  backend_python="${DOCMIND_BACKEND_PYTHON:-$backend_dir/.venv/bin/python}"
  electron_app="${DOCMIND_ELECTRON_APP:-$HOME/Applications/Electron.app}"
  appdata="$HOME/Library/Application Support"
fi
out_app="${DOCMIND_APP_OUT:-}"
if [[ -z "$out_app" ]]; then
  if [[ "$is_windows" = 1 ]]; then
    out_app='C:\Program Files\DocMind'
  else
    out_app="/Applications/DocMind.app"
  fi
fi

if [[ ! -d "$electron_app" ]]; then
  echo "缺少 Electron 运行时：$electron_app" >&2
  exit 1
fi
if [[ ! -x "$backend_python" ]]; then
  echo "缺少后端解释器：$backend_python" >&2
  exit 1
fi

if [[ ! -x "$(command -v node)" ]]; then
  echo "缺少 node" >&2
  exit 1
fi

electron_version="$(node -p "require('./node_modules/electron/package.json').version")"

# 清空一个暂存目录。用mv 到临时目录代替 rm -rf：模型/runtime 动辄数千文件，
# 直接递归删除在受保护的环境里会被拦下，而且移走还能顺手排查上一轮产物。
reset_stage_dir() {
  local dir="$1"
  if [[ -d "$dir" ]]; then
    mv "$dir" "${TMPDIR:-${TEMP:-/tmp}}/$(basename "$dir").stale.$(date +%Y%m%d%H%M%S)" 2>/dev/null \
      || rm -rf "$dir"
  fi
  mkdir -p "$dir"
}

echo "构建渲染/主进程产物..."
npm run desktop:build >/dev/null

# 自包含后端 runtime：独立 CPython + 依赖 + 应用代码，electron-builder 会把它
# 放进 resources/backend-runtime。装好后的 app 不再依赖本机仓库的 backend/.venv。
# DOCMIND_SKIP_BACKEND_BUNDLE=1 可跳过（此时包内无后端，会回退到开发态 venv 路径）。
backend_runtime="$root/backend/build/backend-runtime"
if [[ "${DOCMIND_SKIP_BACKEND_BUNDLE:-0}" == "1" ]]; then
  echo "跳过内置后端 runtime（DOCMIND_SKIP_BACKEND_BUNDLE=1）"
  if [[ -d "$backend_runtime" ]]; then
    mv "$backend_runtime" "${TMPDIR:-${TEMP:-/tmp}}/docmind-runtime-skip.$(date +%s)" 2>/dev/null || true
  fi
else
  echo "构建自包含后端 runtime..."
  "$backend_dir/scripts/build-backend-runtime.sh"
fi

# 内置 embedding 模型：暂存到 build/embedding-models，
# electron-builder 会把它放进 resources/models（DOCMIND_BUNDLED_MODELS_DIR）。
# 候选来源（按顺序取第一个含 model.onnx 的）：
#   1. DOCMIND_MODEL_SOURCE 显式指定
#   2. Electron 开发态 userData（npm run dev 里后端实际下模型的位置）
#   3. 后端独立运行的默认数据目录 ~/.docmind
model_stage_root="$root/build/embedding-models"
model_src=""
for candidate in \
  "${DOCMIND_MODEL_SOURCE:-}" \
  "$appdata/docmind-desktop/models/onnx--BAAI--bge-m3" \
  "$HOME/.docmind/models/onnx--BAAI--bge-m3"; do
  if [[ -n "$candidate" && -f "$candidate/model.onnx" ]]; then
    model_src="$candidate"
    break
  fi
done
if [[ "${DOCMIND_SKIP_MODEL_BUNDLE:-0}" == "1" ]]; then
  echo "跳过内置 embedding 模型（DOCMIND_SKIP_MODEL_BUNDLE=1），安装后将回退到用户目录/在线下载"
  reset_stage_dir "$model_stage_root"
elif [[ -z "$model_src" ]]; then
  echo "缺少内置 embedding 模型（候选来源均无 model.onnx）" >&2
  echo "可在应用设置中准备模型，或运行：cd backend && <venv>/python scripts/prepare_onnx_embedding.py" >&2
  echo "或设置 DOCMIND_SKIP_MODEL_BUNDLE=1 跳过（不推荐，安装后需用户自行下载模型）" >&2
  exit 1
else
  echo "暂存内置 embedding 模型（来源：${model_src}）..."
  reset_stage_dir "$model_stage_root"
  mkdir -p "$model_stage_root"
  if [[ "$is_windows" = 1 ]]; then
    # APFS 的 clone 复制（cp -c）和 ditto 都是 macOS 独有的，Windows 走普通递归复制。
    cp -R "$model_src" "$model_stage_root/onnx--BAAI--bge-m3"
  else
    # APFS 上优先 clone（秒级、不占额外空间），失败再退回普通复制
    cp -cR "$model_src" "$model_stage_root/onnx--BAAI--bge-m3" 2>/dev/null \
      || ditto "$model_src" "$model_stage_root/onnx--BAAI--bge-m3"
  fi
fi

echo "准备本地 Electron 分发目录..."
mkdir -p "$electron_dist"
if [[ "$is_windows" = 1 ]]; then
  # Windows 的 Electron 运行时就是一个含 electron.exe 的目录，直接复制。
  # 不用 ln -s：NTFS 的符号链接默认需要管理员权限或开发者模式。
  rm -rf "${electron_dist:?}/electron"
  cp -R "$electron_app" "$electron_dist/electron"
else
  ln -sfn "$electron_app" "$electron_dist/Electron.app"
fi
printf '%s\n' "$electron_version" > "$electron_dist/version"

echo "使用 electron-builder 生成应用包..."
# 不再传 DOCMIND_BACKEND_*：electron-builder.config.js 在检测到
# backend/build/backend-runtime 存在时会自行写入 $RESOURCES 占位符，
# 指向包内自包含后端。这里传 env 反而会把它覆盖回开发机 venv 绝对路径。
# 只有跳过内置后端时才显式回退到 venv。
#注意 LSEnvironment 只存在于 macOS；Windows 侧由 main 进程从
# process.resourcesPath 自解析后端路径，无需任何 env 注入。
if [[ "$is_windows" == 1 ]]; then
  platform_flag="--win"
else
  platform_flag="--mac dir"
fi
if [[ "${DOCMIND_SKIP_BACKEND_BUNDLE:-0}" == "1" ]]; then
  DOCMIND_BACKEND_COMMAND="$backend_python" \
  DOCMIND_BACKEND_CWD="$backend_dir" \
  DOCMIND_BACKEND_ARGS='["-m", "app"]' \
    npx electron-builder --config "$root/electron-builder.config.js" $platform_flag --publish never
else
  npx electron-builder --config "$root/electron-builder.config.js" $platform_flag --publish never
fi

if [[ "$is_windows" == 1 ]]; then
  # --win 会同时产出 nsis 安装包与portable exe（见配置里的 win.target）。
  # 这里只校验免安装目录存在：NSIS 产物名带版本号，用通配符找第一个 *.exe。
  built_dir="$root/dist/win-unpacked"
  if [[ ! -d "$built_dir" ]]; then
    echo "构建输出不存在：$built_dir" >&2
    exit 1
  fi
  echo "打包完成。未安装版：$built_dir\\DocMind.exe"
  echo "安装包与免安装版：$root/dist/*.exe"
  echo
  echo "未做自动安装（Program Files 需要管理员权限）。如需安装请手动运行安装包，"
  echo "或设置 DOCMIND_APP_OUT 指向自定义目录后手动复制。"
  exit 0
fi

built_app="$root/dist/mac-arm64/DocMind.app"
if [[ ! -d "$built_app" ]]; then
  echo "构建输出不存在：$built_app" >&2
  exit 1
fi

echo "安装到 $out_app ..."
if [[ -d "$out_app" ]]; then
  backup="$HOME/.Trash/DocMind.app.$(date +%Y%m%d%H%M%S)"
  mkdir -p "$HOME/.Trash"
  mv "$out_app" "$backup"
fi
ditto "$built_app" "$out_app"

# 必须重签，否则装出来的 app 打不开。
# electron-builder 配置里 identity: null（本地免证书分发），产物会沿用
# build/electron-dist 里那个未改名的 Electron.app 运行时签名：
# Identifier=Electron、Sealed Resources=none，而实际 bundle 带着
# resources/{models,backend-runtime} 共 1.1G 资源。签名与实际资源不一致时，
# codesign 校验报 "code has no resources but signature indicates they must be
# present"，macOS 直接拒绝执行 —— 表现为双击后进程静默退出、无任何日志。
# 症状很像主进程崩了，但根因在签名层，别往index.ts 里找。
# 前置：先清扩展属性，否则报 "resource fork, Finder information, or similar
# detritus not allowed"。--deep 负责递归签嵌套的 Helper 与 Framework。
echo "重签应用包（清理扩展属性 → 修rpath → ad-hoc 签名 → 校验）..."
xattr -cr "$out_app"

# 🔴 签名绝对不能带 --identifier。
# `--identifier local.docmind` 会把 Electron Framework、DocMind Helper 等
# 所有嵌套 bundle 强行签成同一个 identifier，但它们各自的 Info.plist 里是
# com.github.Electron.framework / local.docmind.helper 等不同值。签名标识与
# plist 不一致时，macOS 拒绝加载 Electron Framework，进程在 whenReady 之前
# 就静默死掉（退出码 0、零日志、无崩溃报告）——极易误判成主进程逻辑问题。
# 正确做法：--deep 不带 --identifier，让 codesign 沿用每个 bundle 自己的 id。
codesign --force --deep --sign - "$out_app"

# 🔴 必须补Electron Framework 的 rpath，否则 Squirrel 等依赖 dlopen 失败。
# framework 二进制自带的 rpath 只有 @loader_path/Libraries（只含 dylib），
# 缺少指向 Contents/Frameworks/ 顶层那一跳，而 Squirrel/ReactiveObjC/Mantle
# 都躺在那里。症状同样是秒退 + 零日志，dyld 报
# "Library not loaded: @rpath/Squirrel.framework/Squirrel"。
fw_bin="$out_app/Contents/Frameworks/Electron Framework.framework/Versions/A/Electron Framework"
if [[ -f "$fw_bin" ]] && ! otool -l "$fw_bin" | grep -q '@loader_path/\.\./\.\./\.\./\.\./Frameworks'; then
  echo "补齐 Electron Framework 的 rpath..."
  install_name_tool -add_rpath "@loader_path/../../../../Frameworks" "$fw_bin"
  # 改动二进制会作废签名，必须重签一次
  codesign --force --deep --sign - "$out_app"
fi

if ! codesign --verify --deep --strict "$out_app"; then
  echo "签名校验失败，打出的 app 无法运行：$out_app" >&2
  exit 1
fi

# 🔴 打包完必须验证 app 真能起来，不能只看签名通过。
# 这里最容易踩的坑：调用方 shell 里若残留 ELECTRON_RUN_AS_NODE=1（部分沙箱/
# CI 会注入），Electron 会进入纯 Node 模式、不初始化 GUI，直接秒退——此时
# 签名、框架、rpath 全都正常，日志只有一行
#   "Node.js environment variables are disabled because this process is invoked by other apps."
# 于是被误判成打包产物有问题。诊断时务必先 env | grep ELECTRON 确认。
#
# 🔴 第二个同源陷阱（2026-10-09 实测）：`open` 会把**调用方的环境**传给被启动的
# app，所以只清 ELECTRON_* 不够 —— 注入的 `NODE_OPTIONS=--require=…` 同样会让
# 打包后的 Electron 拒绝初始化，stderr 只有上面那一行加一条
# `codesign_util.cc:79 task_name_for_pid failure`，而 app 秒退。本机上 agent 宿主
# 恰好注入 NODE_OPTIONS，所以这一条必须一起 -u 掉。
echo "验证应用可启动（清空 ELECTRON_* 与 NODE_OPTIONS 注入）..."

# 🔴 第二个坑（比签名问题更隐蔽）：验证必须只认「本次启动的新进程」。
# pgrep -f 是纯路径匹配，若上一轮装好的实例还在跑（比如上一次验证失败后
# 没退出、或用户自己开着），新 app 因为单实例锁可能根本没起来，而 pgrep
# 立刻就会把那个旧 pid 报成「验证通过」——于是签名坏、秒退、缺 rpath 这些
# 真故障全被这个假阳性盖过去，打包脚本一路绿灯到底。
# 先清场：优雅退出 → 等 → 强杀残留，确保之后的 pid 只能是新进程。
app_name="$(basename "$out_app" .app)"
if pgrep -f "$out_app/Contents/MacOS/" >/dev/null 2>&1; then
  echo "检测到旧实例在运行，先退出..."
  osascript -e "quit app \"$app_name\"" >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5; do
    sleep 1
    pgrep -f "$out_app/Contents/MacOS/" >/dev/null 2>&1 || break
  done
  # 优雅退出没成功（卡在 before-quit、或后端还占着 18900）就强杀。
  if pgrep -f "$out_app/Contents/MacOS/" >/dev/null 2>&1; then
    echo "旧实例未响应退出，强制结束..."
    pkill -f "$out_app/Contents/MacOS/" >/dev/null 2>&1 || true
    sleep 1
  fi
fi

# 记录基线：此刻已存在的 pid 集合。验证时用它把旧进程排除掉。
baseline_pids="$(pgrep -f "$out_app/Contents/MacOS/" 2>/dev/null || true)"
# 后端也要基线，否则「有后端在跑」这条会被上一轮遗留的后端满足。
baseline_backends="$(pgrep -f "backend-runtime/bin/python3" 2>/dev/null || true)"

env -u ELECTRON_RUN_AS_NODE -u ELECTRON_ENABLE_LOGGING -u NODE_OPTIONS open -a "$out_app"
verify_pid=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  # 只接受不在基线里的 pid —— 这才是本次真正拉起来的进程。
  while read -r candidate; do
    [[ -n "$candidate" ]] || continue
    if ! grep -qxF "$candidate" <<<"$baseline_pids"; then
      verify_pid="$candidate"
      break
    fi
  done < <(pgrep -f "$out_app/Contents/MacOS/" 2>/dev/null || true)
  if [[ -n "$verify_pid" ]]; then
    break
  fi
  verify_pid=""
done
if [[ -z "$verify_pid" ]]; then
  echo "打包产物无法启动：$out_app" >&2
  echo "排查顺序：① env | grep -E 'ELECTRON|NODE_OPTIONS'（都会让 GUI 模式秒退）" >&2
  echo "          ② 嵌套 bundle 签名 id 是否与各自 Info.plist 一致" >&2
  echo "          ③ Electron Framework 的 rpath 是否含 ../../../../Frameworks" >&2
  echo "          ④ 直接跑 Contents/MacOS/ 下的二进制看 stderr（日志只写 stdout/stderr）" >&2
  exit 1
fi

# 🔴 看到 pid 不等于起来了。进程完全可能在 1 秒后自己退出（签名不一致、缺 rpath、
# 注入的环境变量都会这样），而上面那个循环一看到 pid 就 break —— 于是把秒退报成
# 「验证通过」，装出一个打不开的 app 还一路绿灯。（2026-10-09 实测：清掉 NODE_OPTIONS
# 之前，脚本报通过，实际上进程随即消失。）
# 所以再等一会儿，要求它 settle 之后仍然活着；顺带要求它的后端子进程在跑 —— 后端是
# whenReady 里才拉起的，它存在就说明主进程真的走过了启动流程，而不只是被 launchd 拉起过。
verify_settle_seconds="${DOCMIND_VERIFY_SETTLE_SECONDS:-5}"
sleep "$verify_settle_seconds"
if ! kill -0 "$verify_pid" 2>/dev/null; then
  echo "打包产物启动了但随即退出（pid=${verify_pid}，存活不足 ${verify_settle_seconds}s）：$out_app" >&2
  echo "排查顺序：① env | grep -E 'ELECTRON|NODE_OPTIONS'（都会让 GUI 模式秒退）" >&2
  echo "          ② 嵌套 bundle 签名 id 是否与各自 Info.plist 一致" >&2
  echo "          ③ Electron Framework 的 rpath 是否含 ../../../../Frameworks" >&2
  exit 1
fi
# 空基线要单独处理：`grep -vxF ""` 会把每一行都排除掉，于是「有后端在跑」这条
# 会变成永远失败。
if [[ -z "$baseline_backends" ]]; then
  backend_pids="$(pgrep -f "backend-runtime/bin/python3" 2>/dev/null || true)"
else
  backend_pids="$(pgrep -f "backend-runtime/bin/python3" 2>/dev/null | grep -vxF "$baseline_backends" || true)"
fi
if [[ -z "$backend_pids" ]]; then
  echo "app 进程在，但它的后端没起来（pid=${verify_pid}）" >&2
  echo "看 app 的 stderr：直接跑 Contents/MacOS/ 下的二进制，或 open --stderr <文件>" >&2
  exit 1
fi
echo "启动验证通过（pid=${verify_pid}，settle ${verify_settle_seconds}s 后仍存活，后端在跑）"

echo "打包完成：$out_app"
