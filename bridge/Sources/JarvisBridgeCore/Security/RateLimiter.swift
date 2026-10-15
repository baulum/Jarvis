import Foundation
import os

/// Thread-safe token bucket rate limiter.
public final class TokenBucket: Sendable {
    public typealias Clock = @Sendable () -> TimeInterval

    private struct State {
        var tokens: Double
        var last: TimeInterval
    }

    public let capacity: Double
    public let refillPerSecond: Double
    private let clock: Clock
    private let state: OSAllocatedUnfairLock<State>

    public init(
        capacity: Double = 20, refillPerSecond: Double = 20,
        clock: @escaping Clock = { ProcessInfo.processInfo.systemUptime }
    ) {
        self.capacity = capacity
        self.refillPerSecond = refillPerSecond
        self.clock = clock
        self.state = OSAllocatedUnfairLock(initialState: State(tokens: capacity, last: clock()))
    }

    /// Consumes one token if available.
    public func tryConsume() -> Bool {
        let now = clock()
        return state.withLock { s in
            let elapsed = max(0, now - s.last)
            s.tokens = min(capacity, s.tokens + elapsed * refillPerSecond)
            s.last = now
            if s.tokens >= 1 - 1e-9 {  // tolerate floating-point drift
                s.tokens = max(0, s.tokens - 1)
                return true
            }
            return false
        }
    }
}
