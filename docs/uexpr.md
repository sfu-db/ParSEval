# U-expression translation

`UExprCompiler` lowers a checked compact query into a factorized U-expression.
`simplify_uexpr` beta-reduces that term and normalizes order. `to_espnf`
applies the size-preserving sum-product rules. `inspect_bag_espnf` reads the
resulting bag structure.

Concolic execution of U-expressions lives in `parseval.instance`, and branch
coverage in `parseval.generator.coverage`.
