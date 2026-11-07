# Live Shadow Evidence Gate

## Purpose

paper候補の存在ではなく、締切前情報で凍結され公式結果でsettleされたlive shadow候補の収益性と安定性を判定する。

## Fixed gates

- observation days: 60日以上
- settled shadow candidates: 500件以上
- settlement coverage: 既存 `tuning_gate.json` の閾値以上
- pre-deadline odds coverage: 同じcoverage閾値以上
- unresolved candidates: 0
- 最大の正利益日/区分が全正利益の25%以下
- feature drift status: `ok`
- candidateId duplicate: 0

これらは後から良く見えるように変更しない。ROIやcoverageがnullの場合は不合格ではなく「証拠未取得によるblocked」とする。

## Interpretation

- `paperValidationReady=True` はlive profitabilityの証明ではない。
- `live_shadow_ready` でもBUY/EV/投票への自動接続は行わない。
- `live_shadow_blocked` は実装失敗とは限らず、観測期間・settled件数・証拠項目の不足を示す。

## Output

- `reports/monitoring/live_shadow_evidence.json`
- `reports/monitoring/live_shadow_evidence.csv`
- `reports/monitoring/live_shadow_evidence.md`

生成物は監視用でGit対象外。script/test/docだけがGit追加候補である。
