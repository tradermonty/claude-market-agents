# Manifest test snapshots

These config-only snapshots come from the local canonical backtest manifest
(`reports/backtest/run_manifest.json`, data end 2026-02-14) and the VIX20 variant
(`reports/backtest/vix20/run_manifest.json`, data end 2026-03-13).

The snapshots keep default/manifest parity and alternate-manifest loading covered
in a clean checkout, where generated reports are absent. The tests also check the
generated manifests when available. Update these snapshots deliberately when the
accepted strategy configuration changes; they are test inputs, not runtime
manifests or evidence of a current backtest run.
