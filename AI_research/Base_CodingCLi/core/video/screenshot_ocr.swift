// Local OCR for source screenshots. No network, browser login state or model account.
import Foundation
import Vision
import ImageIO

struct Line: Codable {
    let text: String
    let confidence: Float
    let x: Double
    let y: Double
    let width: Double
    let height: Double
}

do {
    guard CommandLine.arguments.count == 2 else { exit(2) }
    let url = URL(fileURLWithPath: CommandLine.arguments[1])
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { exit(3) }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = false
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    let lines = (request.results ?? []).compactMap { observation -> Line? in
        guard let text = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return Line(text: text.string, confidence: text.confidence, x: box.origin.x, y: box.origin.y,
                    width: box.width, height: box.height)
    }
    let data = try JSONEncoder().encode(lines)
    print(String(data: data, encoding: .utf8)!)
} catch {
    // Do not print file contents or a user's image text on failure.
    fputs("Screenshot text recognition failed\n", stderr)
    exit(1)
}
