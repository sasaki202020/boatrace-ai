# CONTROL_TOWER.md

## 目的
このファイルは、競艇AI運用の**司令塔**です。  
ここまで作った文書・台帳・チェックリストを、**いつ / 何のために / どの順番で使うか**を1枚で管理します。

---

## これが必要な理由
文書が増えると、次の問題が起きます。

- どのファイルを最初に見るべきか分からない
- 同じ内容を別ファイルで二重管理し始める
- 日次運用と週次判断が分離する
- 台帳はあるのに、実際の意思決定に使われない

このファイルの役割は、  
**「運用の入口」「判断の順番」「更新責任」を固定すること**です。

---

## 文書一覧と役割

| file | role | timing | owner | update_frequency |
|---|---|---|---|---|
| `OPERATIONS.md` | 日次運用の基本方針 | 常時参照 | 運用担当 | 必要時 |
| `RUNBOOK_COMMANDS.md` | 朝・昼・夜の実行コマンド | 実行時 | 運用担当 | コマンド変更時 |
| `DECISION_MATRIX.md` | 進める / 止めるの判定基準 | 実行直後 | 運用担当 | 基準変更時 |
| `COMPARISON_TARGET_DAYS.md` | TARGET / HOLD / EXCLUDE 台帳 | 夜・週次 | 評価担当 | 毎日 |
| `EXPERIMENT_LOG.md` | 改善実験の記録 | 実験時 | 改善担当 | 実験ごと |
| `WEEKLY_REVIEW.md` | 週次判断の記録 | 週末 | 責任者 | 毎週 |
| `IMPROVEMENT_BACKLOG.md` | 改善候補の優先順位 | 週末・着手前 | 責任者 | 毎週 |

---

## 運用の主ルート
毎日の運用は、必ずこの順番で見る。

### 朝
1. `RUNBOOK_COMMANDS.md`
2. `DECISION_MATRIX.md`
3. `OPERATIONS.md`

### 昼〜夕方
1. `RUNBOOK_COMMANDS.md`
2. `DECISION_MATRIX.md`

### 夜
1. `RUNBOOK_COMMANDS.md`
2. `COMPARISON_TARGET_DAYS.md`
3. `DECISION_MATRIX.md`

### 週末
1. `WEEKLY_REVIEW.md`
2. `EXPERIMENT_LOG.md`
3. `IMPROVEMENT_BACKLOG.md`

---

## 朝の使い方
### 目的
当日の判定を出せる状態にあるかを確認する。

### 見るファイル
- `RUNBOOK_COMMANDS.md`
- `DECISION_MATRIX.md`

### やること
- 朝のコマンドを実行
- BUY件数の異常確認
- 欠損や出力空を確認
- 単日でロジック変更しない

### 朝の出口条件
- 判定ファイルが出た
- BUY件数が異常でない
- 欠損が致命的でない

---

## 昼〜夕方の使い方
### 目的
オッズ供給の品質を確認する。

### 見るファイル
- `RUNBOOK_COMMANDS.md`
- `DECISION_MATRIX.md`

### やること
- `real_odds_available` 確認
- `pending_unpublished` 確認
- `real_odds_missing_fetch` 確認
- モデル問題と供給問題を分離する

### 昼の出口条件
- 供給状態が把握できた
- 夜の評価保留リスクが見えた

---

## 夜の使い方
### 目的
結果確定可否を判断し、比較対象日を更新する。

### 見るファイル
- `RUNBOOK_COMMANDS.md`
- `COMPARISON_TARGET_DAYS.md`
- `DECISION_MATRIX.md`

### やること
- `post-race` 実行
- 結果 TXT の揃い確認
- `raw_incomplete` 確認
- TARGET / HOLD / EXCLUDE を付与

### 夜の出口条件
- その日の status が決まった
- 翌日に再確認すべき日が分かった

---

## 週末の使い方
### 目的
今週の改善を残すか切るか決める。

### 見るファイル
- `WEEKLY_REVIEW.md`
- `EXPERIMENT_LOG.md`
- `IMPROVEMENT_BACKLOG.md`

### やること
- 今週の TARGET/HOLD/EXCLUDE を集計
- raw vs calibrated の扱いを判定
- KEEP / HOLD / DROP を決める
- 来週やる改善を 1〜2件に絞る

### 週末の出口条件
- 残す改善が決まった
- 切る改善が決まった
- バックログの優先順位が更新された

---

## ファイル間の関係図

### 1. 日次運用
`RUNBOOK_COMMANDS.md`
→ 実行  
→ `DECISION_MATRIX.md`
→ 判断  
→ `COMPARISON_TARGET_DAYS.md`
→ 台帳更新

### 2. 実験
`COMPARISON_TARGET_DAYS.md`
→ TARGET 日確保  
→ `EXPERIMENT_LOG.md`
→ 実験記録

### 3. 週次判断
`EXPERIMENT_LOG.md`
+ `COMPARISON_TARGET_DAYS.md`
→ `WEEKLY_REVIEW.md`
→ 今週の結論  
→ `IMPROVEMENT_BACKLOG.md`
→ 次週優先順位

---

## 更新責任のルール
ファイルごとに「誰が更新するか」を曖昧にしない。

### 日次で更新必須
- `COMPARISON_TARGET_DAYS.md`

### 実験時に更新必須
- `EXPERIMENT_LOG.md`

### 週次で更新必須
- `WEEKLY_REVIEW.md`
- `IMPROVEMENT_BACKLOG.md`

### コマンドや基準変更時のみ更新
- `RUNBOOK_COMMANDS.md`
- `DECISION_MATRIX.md`
- `OPERATIONS.md`

---

## やってはいけないこと
- 同じ日付判定を複数ファイルで持つ
- HOLD の理由を別紙に散らす
- 実験結果を週次レビューに転記しない
- バックログ更新を忘れて次週に入る
- 文書だけ増やして実データを埋めない

---

## 最小運用セット
時間がないときは、この4つだけ回せば最低限維持できる。

1. `RUNBOOK_COMMANDS.md`
2. `DECISION_MATRIX.md`
3. `COMPARISON_TARGET_DAYS.md`
4. `WEEKLY_REVIEW.md`

---

## いま最優先で埋めるべきファイル
優先順で並べる。

1. `COMPARISON_TARGET_DAYS.md`
2. `EXPERIMENT_LOG.md`
3. `WEEKLY_REVIEW.md`
4. `IMPROVEMENT_BACKLOG.md`

理由:
- 比較対象日が曖昧だと全部崩れる
- 実験ログが空だと改善判定できない
- 週次レビューが空だと残す/切るが決まらない
- バックログは最後に絞る

---

## 厳しめ結論
今の段階で価値が高いのは、新しい仕組みより**記録の一元化**です。  
運用が前に進むかどうかは、コード量ではなく、  
**「どのデータを、どの文書に、どの順番で残すか」が固定されているか** で決まります。
