# Course/Start Challenger Goal

## 目的

固定した `tree_15` をchampionとして維持したまま、結果判明前に収集した
`course_and_start_exhibition` だけを追加した個人研究challengerを、
時系列OOFで検証する。production predictor、prospective prediction、
settlement、BUY、EV、bettingには接続しない。

## 到達条件

評価開始には、次のすべてが必要である。

- 完全なcourse/start snapshot付きのsettled raceが1,500以上
- forward collectionが連続30日以上
- schedule denominatorに基づくcoverageが80%以上
- 全snapshotが締切前、timestamp/provenance/schema検証済み
- result leakage、duplicate、schema drift、parser failureが0
- `tree_15` model SHA-256が固定値と一致
- deterministic rerunが一致

全体のsettlement件数だけでは評価開始条件を満たしたとみなさない。
feature snapshotとsettlementがrace単位で結合できる件数を使用する。

## 評価契約

- chronological expanding-window 5-fold
- random split禁止
- 各foldのpreprocessingはtrain raceだけでfit
- baselineは保存済みtree_15 probabilityを使用
- candidateはtree_15 champion logitとcourse/start値の固定logistic設定
- primary: race log loss、Brier、Top-1
- secondary: ECE、date-block bootstrap 95% CI、venue/raceNo/month別
- 条件未達時は学習・評価を実行しない

## 採用判定

次をすべて満たした場合だけ `PERSONAL_OFFLINE_CHALLENGER` とする。

- log lossが5fold中4fold以上で改善
- aggregate log loss差のdate-block bootstrap 95% CI上限が0未満
- Brierが安定改善
- ECEの悪化が0.005以内
- Top-1が悪化しない
- 最悪foldのlog loss悪化が0.002以内
- venue/month/top predicted boatの分布が単一segmentに偏らない
- segment別指標、leakage、非決定性に問題がない

未達時は `NO_CHALLENGER_FOUND` とし、`tree_15` を維持する。
候補が合格しても、別途新規prospective parallel shadowを通過するまで
個人用predictionへ反映しない。`productionAdoptionAllowed` は常にfalseとする。
