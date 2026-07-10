# Architecture V2 Completion

## Definition

Architecture v2の「完成」は二段階で管理する。

1. `implementationComplete`: A-Dの監査・再評価・ゲートを再実行できる。
2. `evidenceComplete`: A-Dの品質・期間・live証拠がすべて合格している。

実装が揃っていても証拠不足なら `architecture_v2_implementation_complete_evidence_blocked` とする。

## Phases

- A: candidate trace contractと候補単位join
- B: model/calibration/market/policy境界監査
- C: expanding-windowの同一split再評価
- D: 60日/500 settledを含むlive shadow証拠ゲート

## Production boundary

この完成レポートはBUY/EV/投票/本番採用を許可しない。`productionAdoptionAllowed` は常にfalseで、採用判断は別の明示的な承認フェーズに隔離する。

## Output

- `reports/monitoring/architecture_v2_completion.json`
- `reports/monitoring/architecture_v2_completion.md`

監視生成物はGit対象外。script/test/docだけがGit追加候補である。
