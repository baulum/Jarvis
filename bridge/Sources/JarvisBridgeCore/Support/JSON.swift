import Foundation

/// A small, Sendable JSON value type used for request bodies and responses.
public enum JSON: Sendable, Equatable {
    case null
    case bool(Bool)
    case int(Int)
    case double(Double)
    case string(String)
    case array([JSON])
    case object([String: JSON])

    // MARK: Accessors

    public subscript(key: String) -> JSON? {
        if case .object(let dict) = self { return dict[key] }
        return nil
    }

    public var isNull: Bool {
        if case .null = self { return true }
        return false
    }

    public var stringValue: String? {
        if case .string(let s) = self { return s }
        return nil
    }

    public var boolValue: Bool? {
        if case .bool(let b) = self { return b }
        return nil
    }

    /// Integer value; accepts integral doubles (e.g. `3.0`).
    public var intValue: Int? {
        switch self {
        case .int(let i): return i
        case .double(let d):
            guard d.isFinite, d.rounded() == d, abs(d) < 9.0e15 else { return nil }
            return Int(d)
        default: return nil
        }
    }

    public var doubleValue: Double? {
        switch self {
        case .int(let i): return Double(i)
        case .double(let d): return d
        default: return nil
        }
    }

    public var arrayValue: [JSON]? {
        if case .array(let a) = self { return a }
        return nil
    }

    public var objectValue: [String: JSON]? {
        if case .object(let o) = self { return o }
        return nil
    }

    // MARK: Parsing

    public enum ParseError: Error {
        case invalid(String)
    }

    public static func parse(_ data: Data) throws -> JSON {
        let any: Any
        do {
            any = try JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])
        } catch {
            throw ParseError.invalid("Body is not valid JSON")
        }
        return try from(any: any)
    }

    static func from(any: Any) throws -> JSON {
        switch any {
        case is NSNull:
            return .null
        case let n as NSNumber:
            if CFGetTypeID(n) == CFBooleanGetTypeID() {
                return .bool(n.boolValue)
            }
            if CFNumberIsFloatType(n) {
                return .double(n.doubleValue)
            }
            return .int(n.intValue)
        case let s as String:
            return .string(s)
        case let a as [Any]:
            return .array(try a.map { try from(any: $0) })
        case let d as [String: Any]:
            var out: [String: JSON] = [:]
            out.reserveCapacity(d.count)
            for (k, v) in d { out[k] = try from(any: v) }
            return .object(out)
        default:
            throw ParseError.invalid("Unsupported JSON value")
        }
    }

    // MARK: Serialization

    public func serialized(pretty: Bool = false) -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = pretty
            ? [.sortedKeys, .withoutEscapingSlashes, .prettyPrinted]
            : [.sortedKeys, .withoutEscapingSlashes]
        // Encoding a JSON enum cannot fail: non-finite doubles are mapped to null.
        return (try? encoder.encode(self)) ?? Data("null".utf8)
    }

    public var serializedString: String {
        String(decoding: serialized(), as: UTF8.self)
    }
}

extension JSON: Encodable {
    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null: try c.encodeNil()
        case .bool(let b): try c.encode(b)
        case .int(let i): try c.encode(i)
        case .double(let d):
            if d.isFinite { try c.encode(d) } else { try c.encodeNil() }
        case .string(let s): try c.encode(s)
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        }
    }
}

extension JSON: ExpressibleByNilLiteral, ExpressibleByBooleanLiteral, ExpressibleByIntegerLiteral,
    ExpressibleByFloatLiteral, ExpressibleByStringLiteral, ExpressibleByArrayLiteral,
    ExpressibleByDictionaryLiteral
{
    public init(nilLiteral: ()) { self = .null }
    public init(booleanLiteral value: Bool) { self = .bool(value) }
    public init(integerLiteral value: Int) { self = .int(value) }
    public init(floatLiteral value: Double) { self = .double(value) }
    public init(stringLiteral value: String) { self = .string(value) }
    public init(arrayLiteral elements: JSON...) { self = .array(elements) }
    public init(dictionaryLiteral elements: (String, JSON)...) {
        var dict: [String: JSON] = [:]
        for (k, v) in elements { dict[k] = v }
        self = .object(dict)
    }
}

public extension JSON {
    /// Convenience for optional values: nil becomes `.null`.
    static func optional(_ s: String?) -> JSON { s.map { .string($0) } ?? .null }
    static func optional(_ i: Int?) -> JSON { i.map { .int($0) } ?? .null }
    static func optional(_ b: Bool?) -> JSON { b.map { .bool($0) } ?? .null }
}
