# Model / Policy Separation Contract

## Purpose

予測確率、校正、市場情報、policy判断を別の監査契約として扱う。
本契約はsidecar監査用であり、既存のBUY/EV/hard guardの挙動を変更しない。

## Layer outputs

| layer | owns | must not own |
| --- | --- | --- |
| model | `rawProbability`, `modelVersion`, `featureVersion`, `predictionHash` | BUY/WATCH/SKIP判断 |
| calibration | `rawProbability`, `calibratedProbability`, `calibratorVersion` | odds閾値、BUY判断 |
| market | `odds`, `oddsCapturedAt`, `deadlineAt`, `marketProbability` | モデル確率の生成 |
| policy | `estimatedEdge`, `policyDecision`, `guardDecision`, `guardReason`, `policyVersion` | モデル学習、確率校正 |

全レイヤーは `candidateId` で接続する。存在しないversionや時刻を推測で補完しない。

## Dependency rule

- model層からpolicy層へのimportは禁止する。
- calibration層からpolicy層へのimportは禁止する。
- policy層がmodel/calibrationの出力を読むことは許可する。
- 現行 `StrategyEvaluator` 内の校正処理は `legacy coupling` としてwarningにする。
- legacy couplingの解消は挙動互換性を証明できる別タスクとし、本タスクではproduction codeを変更しない。

## Quality

- `separation_ready`: reverse dependencyとlegacy couplingがない。
- `separation_warning`: reverse dependencyはないがlegacy couplingが残る。
- `separation_blocked`: model/calibrationからpolicyへの逆依存、または正本source欠損がある。

## Output

- `reports/monitoring/model_policy_separation_audit.json`
- `reports/monitoring/model_policy_separation_audit.csv`
- `reports/monitoring/model_policy_separation_audit.md`

生成物は監視用でありGit対象外。script/test/docだけがGit追加候補である。
