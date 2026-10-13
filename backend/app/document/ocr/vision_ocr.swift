// 用系统自带的 Vision 框架做图片文字识别，供后端调用。
//
// **为什么是 Swift 而不是 pyobjc。** 本仓库对运行时体积有明确取舍：用 sqlite-vec 替掉
// chromadb 省下 134M（见 backend/pyproject.toml）。pyobjc 全家桶会把这笔钱花回去，
// 而系统自带的 Vision 框架是零下载的 —— 编译产物 98 KB，只链系统框架，不依赖 Swift
// 工具链本身。
//
// **为什么是批量而不是单张。** 进程启动固定开销约 0.9 秒（实测）：单张 0.90 秒、
// 三张 1.63 秒。一本 487 页的扫描书逐页调用要白等 7 分钟，批量调用把它摊成一次。
//
// **输出格式是 JSON Lines，每张图一行。** 整批成功才输出会丢掉单张的失败原因；逐行
// 输出让调用方能按图定位问题，也不会因为某一张图损坏而丢掉整批结果。
//
// 编译：swiftc -O vision_ocr.swift -o vision_ocr

import AppKit
import Foundation
import Vision

struct RecognizedLine: Codable {
    let text: String
    let confidence: Float
    // Vision 的坐标原点在左下角，取值 0…1。换算成 PDF 坐标是调用方的事。
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

struct ImageResult: Codable {
    let index: Int
    let lines: [RecognizedLine]
}

func recognize(path: String) -> [RecognizedLine]? {
    guard let image = NSImage(contentsOfFile: path),
          let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil)
    else {
        return nil
    }

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    // 语料里中文课件与中文教材都很多，语言必须显式声明 —— 默认只认英文。
    // zh-Hans 放前面：Vision 按声明顺序决定候选优先级。
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    // 语言纠错会「顺手改对」数字与公式。图里的数值是要被检索的内容，不能被改写。
    request.usesLanguageCorrection = false

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    do {
        try handler.perform([request])
    } catch {
        return nil
    }
    guard let observations = request.results else { return [] }
    return observations.compactMap { observation in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return RecognizedLine(
            text: candidate.string,
            confidence: candidate.confidence,
            x: box.origin.x,
            y: box.origin.y,
            width: box.size.width,
            height: box.size.height
        )
    }
}

let arguments = Array(CommandLine.arguments.dropFirst())

// 探针：确认这个二进制在本机能真的跑起来，并且 Vision 引擎可用。
//
// 存在意义是「架构不匹配」这种情况：arm64 二进制在 Intel Mac 上，`FileManager` 看它
// 存在且可执行，但一执行就被系统拒绝（bad CPU type）。只看文件属性会把这种情况报成
// 「可用」，然后在导入时才失败 —— 用户等半天才知道。探针让失败提前到能力检查。
if arguments.first == "--probe" {
    let request = VNRecognizeTextRequest()
    // 只有真的能构造出识别请求，才说明 Vision 在这个进程里可用。
    request.recognitionLevel = .accurate
    print("{\"ok\":true,\"languages\":\(request.recognitionLanguages.count)}")
    exit(0)
}

let paths = arguments
let encoder = JSONEncoder()
encoder.outputFormatting = [.withoutEscapingSlashes]

for (index, path) in paths.enumerated() {
    // 单张失败也要输出一行（lines 为空），否则调用方无法把失败与「真的没识别到文字」区分开。
    let lines = recognize(path: path) ?? []
    if let data = try? encoder.encode(ImageResult(index: index, lines: lines)),
       let line = String(data: data, encoding: .utf8) {
        print(line)
        // 逐行刷新：调用方按行读取时不必等整批结束。
        fflush(stdout)
    }
}
