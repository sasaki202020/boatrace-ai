# Offline Model v4 Final Report

- Status: `NO_CHALLENGER_FOUND`
- Challenger: `NONE`
- Evaluation: `RESEARCH_WALK_FORWARD`
- Existing period is not an unused holdout.
- ROI was not calculated.
- Production/prospective integration: none.

## Mean Metrics

modelName,raceLogLoss,multiclassBrier,top1Accuracy,ece10,raceCount
tree_15,1.243307167837679,0.6050476943773007,0.5580636674559326,0.021683223201739977,38010
tree15_temp085,1.246430296776952,0.6071669842252028,0.5580636674559326,0.049943690027597736,38010
residual_c10_a10,1.252042821208026,0.6083142636804605,0.5591160220994476,0.017507396593896427,38010
tree15_temp115,1.2563037613689612,0.6107616966399101,0.5580636674559326,0.0624765934193049,38010
residual_c01_a05,1.286017467387018,0.620668988515902,0.5530386740331492,0.04722870906369213,38010
lane_frequency,1.3571475451182935,0.6462868228105749,0.5530386740331492,0.009424323797828827,38010
ranking_leaf31,1.5471807259403019,0.7358287226251932,0.5530386740331492,0.2858055170109197,38010
ranking_leaf15,1.55245259793754,0.7380724341760168,0.5530386740331492,0.28593782635565196,38010
lane1_always,12.349997835796247,0.8939226519283382,0.5530386740331492,0.4469613259618504,38010

## Gap Reset

{"status": "PASS", "tree15VsLaneFrequencyLogLossImprovedFolds": 5, "tree15VsLaneFrequencyBrierImprovedFolds": 5, "challengerAudits": []}

## Remaining Risks

- Historical pre-race capture timestamps unavailable.
- Complete scheduled-race denominator unavailable.
- 2020-03 through 2023-12 coverage gap.
- Model-selection bias remains; only future prospective data can confirm the challenger.
