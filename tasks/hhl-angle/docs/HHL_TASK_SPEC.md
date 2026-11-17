# HHL angle task — permitted research specification

This document describes the task, not an implementation of its solution.

## Required reversible map

Register 0 `X`: unsigned 21-bit, little-endian. Valid domain: `65536 <= X <= 2097151`.
Register 1 `k`: arbitrary unsigned 33-bit, little-endian input accumulator.

`h(X) = round_half_away_from_zero(asin(2^15 / X) * 2^32)`.

`|X>|k>  ->  |X>|(k + h(X)) mod 2^33>`.

The input `X` must be preserved exactly; `k` is NOT initialized to zero.
The accepted centered modular error for `k` is at most 429 units (one output unit = `2^-32` radians). All tested shots must pass. The evaluator also checks the required phase/sign and cleanliness of ancillary qubits.

Research goal: minimize `T × Q`, where T is executed Toffoli count and Q is peak qubit count, subject to correctness. Verified baseline metrics: T=16468, Q=300, T×Q=4940400. These metrics are comparison data, not a construction method.

No particular approximation, arithmetic circuit, memory representation, or algorithm is prescribed.
