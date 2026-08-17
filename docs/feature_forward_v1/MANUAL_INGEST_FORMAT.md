# Manual Feature Ingest Format

## Scope

この経路は、ユーザーが手動で取得してローカルに配置したpre-race snapshotだけを
`data/research/feature_forward_v1/inbox` から受け付ける。自動ネットワーク取得、
ブラウザ操作、BUY、EV、投票、production/prospective書込みは行わない。

`config/feature_forward_v1/source_approval.json` の次の値を変更してはならない。

```text
manualIngestAllowed=true
automatedNetworkFetchAllowed=false
automatedCollectionAllowed=false
```

## Required Pair

各入力JSONには、同じディレクトリにSHA-256 sidecarを置く。

```text
<name>.json
<name>.json.sha256
```

sidecarはJSONのraw bytesに対する64桁小文字SHA-256だけを含む。JSONを手で修正した場合は
sidecarも再作成する。hash不一致、sidecar欠落、非JSONはappendしない。

## Preflight

collectorはappend前に次を読み取り専用で確認する。

- source type と source location がsource approvalに一致する
- race identity、UTC/JST timestamp、prediction deadline、clock driftが有効である
- 6艇とfeature groupのschema、値域、result leakage禁止を満たす
- 既存Storeとのduplicate/conflict、および同一inbox内のduplicate/conflictがない
- feature store hash chain と、存在するlifecycle ledger hash chainが有効である

1件でも不正なら `MANUAL_INGEST_PREFLIGHT_BLOCKED` で終了し、すべての入力を
移動・appendしない。inboxが空の場合は `WAITING_FOR_APPROVED_INPUT` が正常な待機状態である。

## Readiness Only

manual inputを追加した後も、OOF評価は自動で開始しない。次のreadiness reportだけを
更新し、Diagnostic/Decisionの残件を確認する。

```text
py -3 scripts/build_oof_data_readiness_v1.py --data-root <canonical-data-root>
```

`DECISION_DATA_READY_AWAITING_EXPLICIT_APPROVAL` になっても、明示承認なしに
metric evaluation、challenger selection、production adoptionを開始してはならない。
