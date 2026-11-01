// Apple on-device Foundation Models bridge for the workbench AI provider.
//
// The ONLY model this program can reach is `SystemLanguageModel.default`, i.e.
// the on-device model already installed on this Mac. There is no HTTP client,
// no URL, no socket and no credential anywhere in this file: the process reads
// stdin, calls the on-device model, and writes stdout. Zero network, zero cost.
//
// Contract
// --------
// Build (once, at build/install time -- never at request time):
//     tools/apple_fm_bridge/build.sh
//
// Invoke, request on stdin as exactly ONE JSON object:
//     {"prompt": "...",            // required, non-empty
//      "system": "...",            // optional instructions
//      "temperature": 0.0,         // optional, omitted when absent
//      "max_tokens": 1024,         // optional cap on response tokens
//      "schema": { ... }}          // optional JSON Schema (guided generation)
//
// Response on stdout as exactly ONE JSON object:
//     {"ok": true,
//      "text": "...",
//      "model": "apple-on-device-foundation-model",
//      "usage": {"input_tokens": N|null, "output_tokens": N|null}}
//
// Any failure prints {"ok": false, "error": {"code": "...", "reason": "..."}}
// and exits non-zero. Failure codes are stable, machine-readable tokens:
//     unavailable       -- SystemLanguageModel.default is not usable here
//     bad_request       -- stdin was not the documented JSON request
//     context_size_exceeded
//     guardrail_violation / refusal / rate_limited / timeout
//     unsupported_language
//     model_error       -- anything else from the session
//
// `--probe` performs the pinned availability check only (no model call, no
// stdin) and reports the harness facts the Python side reports to the UI:
//     {"ok": true, "available": true, "model": "...", "guided_generation": B,
//      "context_tokens": N|null, "probe_tokens": N|null}
// `--probe` exits 3 when the on-device model is unavailable.
//
// Availability is PINNED to `SystemLanguageModel.default.availability`: when the
// device is ineligible, Apple Intelligence is off, or the model is still
// downloading, this program says so with the reason string and exits non-zero.
// It never generates text in that state and never claims success.
//
// Honesty about limits: the on-device model returns free text. It has a small
// context window and does not enforce a JSON schema, so callers must validate
// every reply and fail closed. This program never repairs, coerces or
// fabricates a reply.
//
// Build note: see tools/apple_fm_bridge/README.md.

import Foundation
import FoundationModels

/// Stable model identity reported to the workbench (matches the registry row).
let modelIdentity = "apple-on-device-foundation-model"

let exitBadRequest: Int32 = 2
let exitUnavailable: Int32 = 3
let exitModelError: Int32 = 4

func emit(_ object: [String: Any]) {
    let data: Data
    if let encoded = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) {
        data = encoded
    } else {
        data = Data(#"{"ok":false,"error":{"code":"model_error","reason":"result could not be serialized"}}"#.utf8)
    }
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

func emitError(_ code: String, _ reason: String, exit exitCode: Int32) -> Never {
    emit(["ok": false, "error": ["code": code, "reason": reason]])
    exit(exitCode)
}

struct BridgeRequest {
    let prompt: String
    let system: String?
    let temperature: Double?
    let maxTokens: Int?
}

func parseRequest(_ input: Data) -> BridgeRequest {
    guard !input.isEmpty else {
        emitError("bad_request", "no JSON request on stdin", exit: exitBadRequest)
    }
    guard let any = try? JSONSerialization.jsonObject(with: input) else {
        emitError("bad_request", "stdin is not JSON", exit: exitBadRequest)
    }
    guard let object = any as? [String: Any] else {
        emitError("bad_request", "stdin JSON is not an object", exit: exitBadRequest)
    }
    guard let prompt = object["prompt"] as? String,
          !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
        emitError("bad_request", "request needs a non-empty string \"prompt\"", exit: exitBadRequest)
    }
    var system: String?
    if let value = object["system"] {
        guard let text = value as? String else {
            emitError("bad_request", "\"system\" must be a string", exit: exitBadRequest)
        }
        system = text.isEmpty ? nil : text
    }
    var temperature: Double?
    if let value = object["temperature"], !(value is NSNull) {
        guard let number = value as? NSNumber else {
            emitError("bad_request", "\"temperature\" must be a number", exit: exitBadRequest)
        }
        temperature = number.doubleValue
    }
    var maxTokens: Int?
    if let value = object["max_tokens"], !(value is NSNull) {
        guard let number = value as? NSNumber, number.doubleValue >= 1,
              number.doubleValue == number.doubleValue.rounded() else {
            emitError("bad_request", "\"max_tokens\" must be a positive integer", exit: exitBadRequest)
        }
        maxTokens = number.intValue
    }
    return BridgeRequest(prompt: prompt, system: system,
                         temperature: temperature, maxTokens: maxTokens)
}

func describe(_ reason: SystemLanguageModel.Availability.UnavailableReason) -> String {
    switch reason {
    case .deviceNotEligible: return "device not eligible for Apple Intelligence"
    case .appleIntelligenceNotEnabled: return "Apple Intelligence is not enabled"
    case .modelNotReady: return "on-device model is not ready (still downloading)"
    @unknown default: return "on-device model is unavailable for an unknown reason"
    }
}

/// The pinned availability gate. Returns the unavailable reason, or nil.
func unavailableReason() -> String? {
    switch SystemLanguageModel.default.availability {
    case .available: return nil
    case .unavailable(let reason): return describe(reason)
    @unknown default: return "on-device model availability is unknown"
    }
}

func supportsGuidedGeneration() -> Bool {
    if #available(macOS 27.0, *) {
        return SystemLanguageModel.default.capabilities.contains(.guidedGeneration)
    }
    return false
}

/// Token count of `text` under the on-device tokenizer, when the OS reports it.
func tokenCount(_ text: String) async -> Int? {
    if #available(macOS 26.4, *) {
        return try? await SystemLanguageModel.default.tokenCount(for: text)
    }
    return nil
}

func errorCode(_ error: Error) -> (String, String) {
    if let modelError = error as? LanguageModelError {
        switch modelError {
        case .contextSizeExceeded(let info):
            return ("context_size_exceeded",
                    "input needed \(info.tokenCount) tokens; the on-device context holds \(info.contextSize)")
        case .rateLimited(let info):
            return ("rate_limited", info.debugDescription)
        case .guardrailViolation(let info):
            return ("guardrail_violation", info.debugDescription)
        case .refusal(let info):
            return ("refusal", info.debugDescription)
        case .unsupportedCapability(let info):
            return ("unsupported_capability", info.debugDescription)
        case .unsupportedLanguageOrLocale(let info):
            return ("unsupported_language", info.debugDescription)
        case .timeout(let info):
            return ("timeout", info.debugDescription)
        case .unsupportedTranscriptContent(let info):
            return ("model_error", info.debugDescription)
        case .unsupportedGenerationGuide(let info):
            return ("model_error", info.debugDescription)
        @unknown default:
            return ("model_error", modelError.localizedDescription)
        }
    }
    if error is LanguageModelSession.Error { return ("model_error", error.localizedDescription) }
    return ("model_error", error.localizedDescription)
}

// MARK: - Probe

func runProbe() async {
    if let reason = unavailableReason() {
        emit(["ok": false, "available": false, "model": modelIdentity,
              "error": ["code": "unavailable", "reason": reason]])
        exit(exitUnavailable)
    }
    let sample = "Reply with the single word: ready"
    let probeTokens = await tokenCount(sample)
    var payload: [String: Any] = [
        "ok": true,
        "available": true,
        "model": modelIdentity,
        "guided_generation": supportsGuidedGeneration(),
    ]
    if let tokens = probeTokens { payload["probe_tokens"] = tokens }
    emit(payload)
}

// MARK: - Generation

func runGeneration(_ request: BridgeRequest) async {
    if let reason = unavailableReason() {
        emit(["ok": false, "model": modelIdentity, "available": false,
              "error": ["code": "unavailable", "reason": reason]])
        exit(exitUnavailable)
    }

    var options = GenerationOptions()
    if let temperature = request.temperature { options.temperature = temperature }
    if let maxTokens = request.maxTokens { options.maximumResponseTokens = maxTokens }

    let session = LanguageModelSession(model: SystemLanguageModel.default,
                                       instructions: request.system)
    do {
        let response = try await session.respond(to: request.prompt, options: options)
        var usage: [String: Any] = ["input_tokens": NSNull(), "output_tokens": NSNull()]
        if #available(macOS 27.0, *) {
            usage["input_tokens"] = response.usage.input.totalTokenCount
            usage["output_tokens"] = response.usage.output.totalTokenCount
        }
        emit(["ok": true, "text": response.content, "model": modelIdentity, "usage": usage])
    } catch {
        let (code, reason) = errorCode(error)
        // context_size_exceeded carries the real ceiling, measured by the OS.
        if case LanguageModelError.contextSizeExceeded(let info) = error {
            emit(["ok": false, "model": modelIdentity,
                  "error": ["code": code, "reason": reason],
                  "context_tokens": info.contextSize, "requested_tokens": info.tokenCount])
            exit(exitModelError)
        }
        emit(["ok": false, "model": modelIdentity, "error": ["code": code, "reason": reason]])
        exit(exitModelError)
    }
}

// Single-file program: top-level async/await is available here, so no
// `-parse-as-library` flag is needed and `run()` runs as the main entry point.
func main() async {
    if CommandLine.arguments.dropFirst().contains("--probe") {
        await runProbe()
        return
    }
    let input = FileHandle.standardInput.readDataToEndOfFile()
    let request = parseRequest(input)
    await runGeneration(request)
}

await main()
