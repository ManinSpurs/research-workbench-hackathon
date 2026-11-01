// Apple on-device AI bridge (FoundationModels / SystemLanguageModel).
//
// Contract: read exactly ONE JSON object from stdin
//     {"prompt": "...", "system": "..."}      ("system" is optional)
// and print exactly ONE JSON object to stdout
//     {"ok": true,  "text": "..."}
//     {"ok": false, "error": "..."}
//
// There is no network code path in this file: FoundationModels resolves to the
// on-device model only. Every failure is reported as {"ok": false}; the process
// never traps, so the caller always gets a structured answer.
//
// Build: swiftc -sdk $(xcrun --sdk macosx --show-sdk-path) -O tools/apple_ai_bridge.swift -o <cache>/apple_ai_bridge

import Foundation
import FoundationModels

struct BridgeRequest: Decodable {
    let prompt: String
    let system: String?
}

func emit(_ object: [String: Any]) {
    let data: Data
    if let encoded = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) {
        data = encoded
    } else {
        data = Data(#"{"ok":false,"error":"result could not be serialized"}"#.utf8)
    }
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

func fail(_ message: String) -> Never {
    emit(["ok": false, "error": message])
    exit(0)  // a structured unavailability answer is a success for the contract
}

func describe(_ reason: SystemLanguageModel.Availability.UnavailableReason) -> String {
    switch reason {
    case .deviceNotEligible: return "Apple on-device model: device not eligible"
    case .appleIntelligenceNotEnabled: return "Apple on-device model: Apple Intelligence not enabled"
    case .modelNotReady: return "Apple on-device model: model not ready"
    @unknown default: return "Apple on-device model: unavailable"
    }
}

func run() async {
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard !input.isEmpty else { fail("no JSON request on stdin") }
    let request: BridgeRequest
    do {
        request = try JSONDecoder().decode(BridgeRequest.self, from: input)
    } catch {
        fail("request is not a JSON object with a string \"prompt\": \(error.localizedDescription)")
    }
    guard !request.prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
        fail("prompt is empty")
    }

    guard #available(macOS 26.0, *) else { fail("FoundationModels needs macOS 26 or newer") }

    let model = SystemLanguageModel.default
    switch model.availability {
    case .available:
        break
    case .unavailable(let reason):
        fail(describe(reason))
    @unknown default:
        fail("Apple on-device model: availability unknown")
    }

    let session = LanguageModelSession(model: model, instructions: request.system)
    do {
        let response = try await session.respond(to: request.prompt)
        emit(["ok": true, "text": response.content])
    } catch {
        fail("model call failed: \(error.localizedDescription)")
    }
}

// Single-file program: the compiler treats this as the main file, so top-level
// async/await is available without `-parse-as-library`.
await run()
