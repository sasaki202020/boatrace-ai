# Research Memory v1

## 目的

研究監督AIが過去の仮説、実験結果、既知の問題、次の検証条件を再利用するための
research-only記憶層です。予測値を生成せず、`tree_15`、prospective prediction、
settlement、BUY、EV、投票、productionへ接続しません。

## 保存先

- `reports/research_memory_v1/research_state.json`
- `reports/research_memory_v1/model_versions.json`
- `reports/research_memory_v1/daily_summary.md`
- `data/research/research_memory_v1/experiment_registry.sqlite3`

`data`と`reports`は生成物として扱い、Gitには追加しません。

## 実験registry

registryはappend-onlyです。同じ`experimentId`と同じ内容の再登録だけをidempotentに
許可し、内容の異なる再登録、UPDATE、DELETEは拒否します。hash chainを検証してから
研究監督AIが読み込みます。

実験結果にはROI、profit、odds、EV、BUY、betting情報を保存しません。研究のprimary
metricsはlog loss、Brier、Top-1、ECEとし、将来の評価条件を混ぜません。

## 更新

```powershell
py -3.13 scripts\refresh_research_memory_v1.py
```

このコマンドは固定modelとfeature order、既存research reportを読み、research-only
のsnapshotを更新します。prediction、settlement、feature-forward storeへ書き込みません。

## 現在の次仮説

`course_and_start_exhibition`を、30日forward収集、1,500 joined settled race、coverage
80%以上の条件後にchronological 5-fold OOFで評価します。条件未達時は評価を実行しません。
