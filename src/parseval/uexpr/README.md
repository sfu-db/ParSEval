# U-expression translation

`UExprCompiler` lowers a checked compact query into a factorized U-expression.
`simplify_uexpr` beta-reduces that term and normalizes order. `to_espnf`
applies the size-preserving sum-product rules. `inspect_bag_espnf` reads the
resulting bag structure.

Concrete evaluation, witness plans, and coverage conditions live in
`parseval.coverage`.
