# docmind-pages

让 DocMind 直接导入 Apple Pages 文档（`.pages`）。

- 支持的扩展名：`.pages`
- 依赖：**macOS + 已安装 Pages.app**。没有 pip 依赖
- 只在能用的时候出现：**没装 Pages、或不在 macOS 上时，导入对话框里不会出现「Pages 文档」这个选项**

## 安装

```bash
git clone https://github.com/alextanlf/docmind-desktop "$TMP"
ln -s "$TMP/plugins/docmind-pages" "$PLUGINS/docmind-pages"
rm -rf "$TMP"
```

`$PLUGINS` 是插件目录的实际路径：**设置 → 插件**，页面底部写着它，可以直接复制。

## 它怎么工作

`.pages` 是一个 IWA 包——zip 里装着一堆 protobuf 流，块用 snappy 压过，没有公开的格式
规范。**没有可用的纯 Python 读法**，macOS 自带的 `textutil` 也直接拒绝
（`The file isn't in the correct format`）。

所以转换交给唯一确定能做的程序：让 Pages 自己把它导出成 Word，再把导出的 `.docx`
交给 **DocMind 自己的 Word 解析器**。这里没有第二份读 docx 的实现——导出变好，
这个插件跟着变好。

代价是它只能在 macOS 上跑，而且要求装了 Pages。这是真实的代价，我们把这件事
**声明出来**（`DocumentFormat.availability`），而不是让用户点到一个只会失败的按钮。

## 实测（Pages 14.5，两份真实文档）

| 项 | 结果 |
|---|---|
| 转换耗时 | **热态 2.3 s**，**冷启动 3.8 s**（Pages 首次启动） |
| 是否抢焦点 | **不会**。冷启动和热态两种情况，前台应用全程没变过 |
| 导出 docx 的大小 | 约 11 KB（原 `.pages` 1.4 MB，大头是内嵌图片） |
| 正文提取 | 干净，中文正常，1970 字符 |

关于耗时：解析被移到工作线程执行，不会卡住后端的事件循环。同一时刻只允许一个导出
在跑——Pages 是一个 GUI 程序，目录导入一次跑三个，三个文档同时在它里面打开是
「脚本驱动的应用报错文档或卡在自己的对话框后面」的典型成因。

## 已知限制

- **拿不到标题。** 导出的 Word 文件 `docProps/core.xml` 是空的；
  `name of document` 报的是我们交给 Pages 的那个临时拷贝，不是用户的文件；
  包本身只存 UUID 和构建版本（`Metadata/Properties.plist` 里没有标题）。
  所以文档沿用宿主给的名字——和其它所有格式一样。真正的修法在暂存层
  （它把原始文件名丢了），不在这里。
- **结构取决于文档本身。** 实测这两份文档只用了 Pages 的「正文」段落样式，
  导出后整篇是平的，DocMind 只会报一个 section。用了 Pages 标题样式的文档
  会导出成 `Heading N`，大纲能保住。
- **导入期间 Pages 会短暂打开该文档**（导出后关闭）。不会打断你当前在用的应用，
  但 Pages 的窗口会出现。
- **能读什么由 Pages 的 Word 导出决定**：Pages 特有的版式、绘图、文本框
  会按它的导出规则处理。

## 测试

```bash
cd backend && env -u PYTHONPATH .venv/bin/python -m pytest tests/plugins/test_pages_plugin.py -q
```

覆盖：可用性声明（含它真的会让格式从选择器里消失）、载荷识别（`.pages` 和 `.docx`
都是 zip，所以必须读包索引而不是只看签名）、三种失败到错误码的映射（超时可重试、
「没装 Pages」不可重试）、导出目录一定会被清掉、并发导出被串行化、
以及**用 `osacompile` 检查 AppleScript 能编译**。

没覆盖的：真实导出需要 Pages 和一份真实文档，所以那一段是手动验证的。脚本最容易出的
问题是语法错误（写这个插件时就踩了：超时块结尾写成 `end with timeout`，不是合法的
AppleScript，每次导入都失败），`osacompile` 能在毫秒级抓到它，所以这一条留在了测试里。
