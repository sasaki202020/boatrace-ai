# TASK-002

## タスク名
ボトルネック分析を「実際の三連単候補集合」基準に修正する

## 背景
- `reports/eval_improvement_pack_result.json` では以下が確認できている。
  - `top1_hitrate`: `0.1119 -> 0.1903`
  - `trifecta_exact`: `0.0050 -> 0.0100`
  - `candidate_include_rate`: `1.0`
  - `trifecta_avg_rank`: `28.12`
- ただし `src/eval/ablation_and_bottleneck.py` の現在の `bottleneck_analysis.json` は、
  実三連単の順位を「lane の連続3件」で探しており、実際の三連単候補集合の順位になっていない。
- このため `not_in_60` が過大になり、次の改善判断を誤る。

## 目的
- `bottleneck_analysis.json` を、実際の三連単候補集合に対する正しい順位分布へ直す。
- 次に触るべき論点が
  - 候補集合不足
  - 集合内順位不足
  のどちらかを判断できる状態にする。

## 変更対象
- 原則 `src/eval/ablation_and_bottleneck.py` のみ
- 必要なら最小限で `reports/` の出力更新

## やること
1. `src/strategy/generate_trifecta_candidates.py` の候補生成ロジックを参照し、
   `top_n_win=6`, `max_trifecta_combinations=60` の条件で、
   各レースの三連単候補集合を `approx_prob` 順に再現する
2. `src/eval/ablation_and_bottleneck.py` の PART2 を修正し、
   実三連単がその候補集合の何位に入るかを測る
3. 少なくとも以下を `reports/bottleneck_analysis.json` に出す
   - `total_races`
   - `trifecta_rank_dist`
   - `not_in_60`
   - `winner_rank_when_trifecta_missed`
   - `diagnosis`
4. 可能なら `trifecta_avg_rank` 相当の値も bottleneck 側で再出力する
5. `reports/ablation_result.json` と `reports/bottleneck_analysis.json` を再生成する

## 制約
- 戦略本体ロジックは変えない
- `generate_trifecta_candidates.py` 自体は変更しない
- 分析用スクリプト側で再現する
- 大規模リファクタ禁止
- 変更は最小限

## 完了条件
- `bottleneck_analysis.json` が実候補集合ベースで更新される
- `not_in_60` が lane 連続判定由来の過大値でなくなる
- 次の改善対象を解釈できる

## 実行コマンド
```powershell
$env:PYTHONPATH='.'
py src/eval/ablation_and_bottleneck.py
```

## 最後に出してほしいもの
1. 変更ファイル
2. 修正内容
3. 実行コマンド
4. 更新後の主要数値
5. 次に直すべき1点
