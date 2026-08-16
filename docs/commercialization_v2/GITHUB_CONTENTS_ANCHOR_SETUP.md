# GitHub Contents Anchor Setup

synthetic external anchorはGitHub Contents APIで専用public repositoryの固定pathへ保存する。Issue APIは使用しない。公開するのはcommitment最小metadataだけで、予測内容、salt、raw input、選手情報、結果は含めない。

## Exact allowlist

- owner: `sasaki202020`
- repository: `boatrace-prediction-anchors`
- branch: `main`
- path prefix: `anchors/synthetic/`
- record type: `synthetic_anchor`
- file: `anchors/synthetic/<commitment>.json`

## Environment

- `BOATRACE_ANCHOR_GITHUB_OWNER=sasaki202020`
- `BOATRACE_ANCHOR_GITHUB_REPO=boatrace-prediction-anchors`
- branchは固定値`main`
- path prefixは固定値`anchors/synthetic/`
- `BOATRACE_ANCHOR_GITHUB_TOKEN`（値をログやファイルへ保存しない）
- `BOATRACE_ANCHOR_GITHUB_API_BASE`（唯一の許可値は`https://api.github.com`）

credential未設定、allowlist不一致、path異常、未知record typeではネットワーク要求前に停止する。
同じhashと同じ内容はidempotent成功とし、同じpathの異なる内容は上書きしない。

## Approval manifest

`github_anchor_approval_manifest.json`には以下を設定する。

```json
{
  "transportMode": "branch_path_commit",
  "owner": "sasaki202020",
  "repository": "boatrace-prediction-anchors",
  "branch": "main",
  "allowedPathPrefix": "anchors/synthetic/",
  "allowedRecordTypes": ["synthetic_anchor"],
  "credentialEnvironmentVariable": "BOATRACE_ANCHOR_GITHUB_TOKEN",
  "transportModeIssue": false,
  "repositoryAllowlist": ["sasaki202020/boatrace-prediction-anchors"],
  "humanApproved": true,
  "syntheticPublishApproved": true,
  "realPredictionPublishApproved": false
}
```

最初の実書込みはrepository作成、限定credential設定、独立review完了後に1回だけ行う。
