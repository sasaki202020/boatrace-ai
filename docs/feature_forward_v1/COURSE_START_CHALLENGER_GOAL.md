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
- forward日数は末尾の連続した暦日で判定し、scopeまたはfeatureが欠損した日は翌日から再起算する
- schedule denominatorに基づくcoverageが80%以上
- 全snapshotが締切前、timestamp/provenance/schema検証済み
- result leakage、duplicate、schema drift、parser failureが0
- `tree_15` model SHA-256が固定値と一致
- deterministic rerunが一致
- OOF validationが1,250 race以上、25日以上、各fold 250 race以上

全体のsettlement件数だけでは評価開始条件を満たしたとみなさない。
feature snapshotとsettlementがrace単位で結合できる件数を使用する。

coverageの分母は、Bファイルとappend-only request stateから復元した
`collector_selected_venues` の範囲に限定する。これは公式サイト全開催のcoverageではない。
collectorは検証済みcollection日数に応じて1会場、2会場、5会場へ段階拡張する。
このscope外のraceを未取得だからといって、source全体の品質問題とは判定しない。

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
- 100 race以上のsegmentでcandidateのlog loss悪化が0.002以内
- segment別指標、leakage、非決定性に問題がない

未達時は `NO_CHALLENGER_FOUND` とし、`tree_15` を維持する。
候補が合格しても、別途新規prospective parallel shadowを通過するまで
個人用predictionへ反映しない。`productionAdoptionAllowed` は常にfalseとする。

## 自動監視

`BOATRACE-CourseStart-Challenger-Gate-V1` は30分ごとにread-only runnerを実行する。
閾値未達時はreadiness reportだけを更新し、モデル学習、prediction、settlement、
prospective ledger、production領域への書込みは行わない。閾値到達後にだけOOF評価を開始し、
合格しても自動採用しない。既存のB/K、prediction、feature collector taskとは分離する。

## 固定コホート

評価開始時に、評価対象日、selected scopeのBファイルSHA-256、model/schema SHA-256、
joined raceのdigestを `course_start_evaluation_cohort.json` へ一度だけ保存する。
以後digestが変わった場合は再評価せず、レビュー待ちでfail-closedにする。
同じdigestと既存評価結果がある場合は、OOFを再実行せず結果を再利用する。
