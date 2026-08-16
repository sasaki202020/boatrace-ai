from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from src.data.parse_fixed_width import BoatRaceParser


RESULT_MARKERS = (
    "result confirmed",
    "final_odds",
    "finish_position",
    "final odds",
    "winner",
    "payout",
    "refund",
    "settlement",
    "actual",
    "target",
    "着順",
    "結果",
    "結果確定",
    "払戻",
    "返還",
    "確定オッズ",
)
ENTRY_COLUMNS = (
    "date",
    "jcd",
    "race_no",
    "deadline",
    "lane",
    "racer_id",
    "racer_class",
    "national_win_rate",
    "national_2ren_rate",
    "local_win_rate",
    "local_2ren_rate",
    "motor_2ren_rate",
    "boat_2ren_rate",
)
MODEL_FEATURES = (
    "lane",
    "jcd",
    "race_no",
    "lane_prior_count",
    "lane_prior_win_rate",
    "venue_lane_prior_count",
    "venue_lane_prior_win_rate",
    "racer_prior_count",
    "racer_prior_win_rate",
    "racer_prior_top2_rate",
    "racer_prior_mean_finish",
    "racer_prior5_win_rate",
    "racer_prior10_win_rate",
    "days_since_previous_race",
    "feature_availability_count",
)
KNOWN_B_SCHEMA_SIGNATURES = {
    "2345e069dc738464f488cb7a776e2cf0f018a88856bf779d6cbcc1890a7d2836",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decode(raw: bytes) -> tuple[str, str]:
    try:
        return raw.decode("cp932"), "cp932"
    except UnicodeDecodeError:
        return raw.decode("cp932", errors="replace"), "cp932_with_replacement"


def _result_like_lines(text: str) -> list[str]:
    normalized = text.lower()
    return [marker for marker in RESULT_MARKERS if marker in normalized]


def _canonical_lane(value: Any) -> int:
    if isinstance(value, (int, np.integer)) and not isinstance(value, (bool, np.bool_)):
        lane = int(value)
    elif isinstance(value, str) and value in {"1", "2", "3", "4", "5", "6"}:
        lane = int(value)
    else:
        raise ValueError("lane_integrity:noncanonical_representation")
    if lane not in range(1, 7):
        raise ValueError("lane_integrity:out_of_range")
    return lane


def schema_signature(raw: bytes) -> str:
    text, encoding = _decode(raw)
    lines = raw.splitlines()
    section_count = sum(bool(re.match(br"^\s*\d{2}BBGN\s*$", line)) for line in lines)
    race_header_count = sum(bool(re.match(r"^\s*\d{1,2}R(?:\s|$)", line.decode("cp932", errors="replace").translate(str.maketrans("０１２３４５６７８９Ｒ：　", "0123456789R: ")))) for line in lines)
    entry_lengths = sorted({len(line) for line in lines if re.match(br"^[1-6]\s+\d{4}", line)})
    contract = {
        "encoding": encoding,
        "startMarker": text.startswith("STARTB"),
        "endMarker": text.rstrip().endswith("END"),
        "sectionMarker": section_count > 0,
        "raceHeader": race_header_count > 0,
        "entryPrefix": "lane-space-racer4",
        "entryLengths": entry_lengths,
        "fixedSlices": ["22:24", "25:30", "30:36", "36:41", "41:47", "50:56", "59:65"],
    }
    return hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_runtime_bfile(path: Path, *, supported_signatures: set[str] | None = None) -> pd.DataFrame:
    raw = path.read_bytes()
    text, _ = _decode(raw)
    if not text.startswith("STARTB") or not re.search(r"(?m)^\d{2}BBGN\s*$", text):
        raise ValueError("unsupported_schema")
    if not (text.rstrip().endswith("END") or text.rstrip().endswith("FINALB")):
        raise ValueError("end_marker_missing")
    signature = schema_signature(raw)
    if supported_signatures is not None and signature not in supported_signatures:
        raise ValueError("schema_signature_not_allowlisted")
    markers = _result_like_lines(text)
    if markers:
        raise ValueError(f"result_like_record:{','.join(markers)}")
    fullwidth_trans = str.maketrans("０１２３４５６７８９Ｒ：　", "0123456789R: ")
    for raw_line in raw.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped in {b"STARTB", b"END", b"FINALB"} or re.match(br"^\d{2}BEND$", stripped) or set(stripped) <= {45}:
            continue
        if re.match(br"^\d{2}BBGN\s*$", stripped) or re.match(br"^[1-6]\s+\d{4}", raw_line):
            continue
        decoded = raw_line.decode("cp932", errors="replace").translate(fullwidth_trans)
        if re.match(r"^\s*\d{1,2}R(?:\s|$)", decoded):
            continue
        # Official descriptive headers contain CP932 multibyte text. Unknown
        # ASCII records are not part of the allowlisted fixed-width contract.
        if all(byte < 128 for byte in stripped):
            raise ValueError("unknown_record_type")
    frame = BoatRaceParser.parse_entries_file(path)
    if frame.empty:
        raise ValueError("required_identity_missing")
    allowed = set(ENTRY_COLUMNS) | {"race_id", "union_key", "venue", "source_file"}
    frame = frame[[column for column in frame.columns if column in allowed]].copy()
    required = {"date", "jcd", "race_no", "lane", "racer_id"}
    if not required.issubset(frame.columns) or frame[list(required)].isna().any().any():
        raise ValueError("required_identity_missing")
    frame["lane"] = pd.Series((_canonical_lane(value) for value in frame["lane"]), index=frame.index, dtype="int64")
    for race_id, group in frame.groupby("race_id", sort=True):
        lanes = group["lane"]
        if len(group) != 6:
            raise ValueError(f"six_boats_required:{race_id}")
        if set(lanes.dropna().astype(int)) != set(range(1, 7)) or lanes.duplicated().any():
            raise ValueError(f"lane_integrity:{race_id}")
        racers = group["racer_id"].astype(str).str.strip()
        if racers.eq("").any() or racers.duplicated().any():
            raise ValueError(f"racer_identity:{race_id}")
    return frame.sort_values(["date", "jcd", "race_no", "lane"]).reset_index(drop=True)


def audit_bfile(path: Path, *, relative_root: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    text, encoding = _decode(raw)
    markers = _result_like_lines(text)
    try:
        frame = validate_runtime_bfile(path)
        parse_status = "SUPPORTED"
        quarantine = ""
    except ValueError as exc:
        frame = BoatRaceParser.parse_entries_file(path)
        parse_status = "QUARANTINED"
        quarantine = str(exc)
    dates = sorted(set(frame.get("date", pd.Series(dtype=str)).dropna().astype(str)))
    venues = sorted(set(frame.get("jcd", pd.Series(dtype=str)).dropna().astype(str)))
    return {
        "relativePath": path.relative_to(relative_root).as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "byteSize": len(raw),
        "encoding": encoding,
        "parseStatus": parse_status,
        "raceDate": "|".join(dates),
        "venue": "|".join(venues),
        "recordCount": len(raw.splitlines()),
        "raceCount": int(frame["race_id"].nunique()) if "race_id" in frame else 0,
        "laneRowCount": int(len(frame)),
        "schemaSignature": schema_signature(raw),
        "schemaVersion": "OFFICIAL_B_FIXED_WIDTH_V1" if parse_status == "SUPPORTED" else "UNKNOWN",
        "recordTypes": "STARTB|VENUE_SECTION|RACE_HEADER|ENTRY_ROW|TEXT_HEADER|END",
        "requiredFieldCoverage": float(frame[[c for c in ("date", "jcd", "race_no", "lane", "racer_id") if c in frame]].notna().mean().mean()) if not frame.empty else 0.0,
        "unknownFieldCount": 0,
        "unknownRecordTypeCount": 0 if parse_status == "SUPPORTED" else 1,
        "resultLikeFieldCount": 0,
        "resultLikeRecordCount": len(markers),
        "quarantineReason": quarantine,
    }


def build_frozen_features(entries: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    target_date = str(entries["date"].iloc[0])
    prior = history[pd.to_datetime(history["date"]) < pd.Timestamp(target_date)].copy()
    prior = prior.sort_values(["date", "jcd", "race_no", "lane"])
    lane_hist: defaultdict[int, list[int]] = defaultdict(list)
    venue_lane_hist: defaultdict[tuple[int, int], list[int]] = defaultdict(list)
    racer_hist: defaultdict[int, deque[tuple[str, int]]] = defaultdict(lambda: deque(maxlen=30))
    for row in prior.itertuples(index=False):
        lane = int(row.lane); venue = int(row.jcd); racer = int(row.racer_id); finish = int(row.finish_position)
        lane_hist[lane].append(int(finish == 1))
        venue_lane_hist[(venue, lane)].append(int(finish == 1))
        racer_hist[racer].append((str(row.date), finish))
    output = []
    for row in entries.itertuples(index=False):
        lane = int(row.lane); venue = int(row.jcd); racer = int(row.racer_id)
        lh = lane_hist[lane]; vlh = venue_lane_hist[(venue, lane)]; rh = list(racer_hist[racer])
        finishes = [item[1] for item in rh]
        record = {
            "race_id": row.race_id, "lane": lane, "jcd": venue, "race_no": int(row.race_no),
            "lane_prior_count": len(lh), "lane_prior_win_rate": (sum(lh) + 1) / (len(lh) + 6),
            "venue_lane_prior_count": len(vlh), "venue_lane_prior_win_rate": (sum(vlh) + 1) / (len(vlh) + 6),
            "racer_prior_count": len(rh), "racer_prior_win_rate": np.mean([v == 1 for v in finishes]) if rh else np.nan,
            "racer_prior_top2_rate": np.mean([v <= 2 for v in finishes]) if rh else np.nan,
            "racer_prior_mean_finish": np.mean(finishes) if rh else np.nan,
            "racer_prior5_win_rate": np.mean([v == 1 for v in finishes[-5:]]) if rh else np.nan,
            "racer_prior10_win_rate": np.mean([v == 1 for v in finishes[-10:]]) if rh else np.nan,
            "days_since_previous_race": (pd.Timestamp(target_date) - pd.Timestamp(rh[-1][0])).days if rh else np.nan,
            "feature_availability_count": int(bool(lh)) + int(bool(vlh)) + int(bool(rh)),
        }
        output.append(record)
    return pd.DataFrame(output)


def audit_only_inference(entries: pd.DataFrame, history: pd.DataFrame, *, model_path: Path, expected_model_sha256: str | None = None) -> dict[str, Any]:
    if expected_model_sha256 and sha256_file(model_path) != expected_model_sha256:
        raise ValueError("model_hash_mismatch")
    features = build_frozen_features(entries, history)
    model = joblib.load(model_path)
    expected_schema = hashlib.sha256(json.dumps(list(MODEL_FEATURES), separators=(",", ":")).encode()).hexdigest()
    actual_order = tuple(str(value) for value in getattr(model, "feature_names_in_", ()))
    if expected_schema != "a3853bdbdb75d13d4a596928c13eaa034307b14a5a8c534d31fca9acdab623dd" or actual_order != MODEL_FEATURES:
        raise ValueError("feature_schema_mismatch")
    raw = model.predict_proba(features[list(MODEL_FEATURES)])[:, 1]
    probabilities = np.zeros(len(raw), dtype=float)
    for _, indexes in features.groupby("race_id", sort=True).groups.items():
        idx = np.asarray(list(indexes)); total = float(raw[idx].sum())
        if not np.isfinite(total) or total <= 0:
            raise ValueError("invalid_probability_total")
        probabilities[idx] = raw[idx] / total
    if not np.isfinite(probabilities).all() or ((probabilities < 0) | (probabilities > 1)).any():
        raise ValueError("invalid_probability")
    sums = pd.Series(probabilities).groupby(features["race_id"]).sum()
    if not np.allclose(sums.to_numpy(), 1.0, atol=1e-9):
        raise ValueError("probability_sum_mismatch")
    digest_rows = [f"{race}|{lane}|{value:.12f}" for race, lane, value in zip(features["race_id"], features["lane"], probabilities)]
    return {
        "raceCount": int(features["race_id"].nunique()),
        "rowCount": int(len(features)),
        "featureCoverage": float(features[list(MODEL_FEATURES)].notna().mean().mean()),
        "probabilityContract": "PASS",
        "deterministicOutputHash": hashlib.sha256("\n".join(digest_rows).encode()).hexdigest(),
    }


def generate_prediction_rows(entries: pd.DataFrame, history: pd.DataFrame, *, model_path: Path, expected_model_sha256: str) -> list[dict[str, Any]]:
    if sha256_file(model_path) != expected_model_sha256:
        raise ValueError("model_hash_mismatch")
    if not pd.api.types.is_integer_dtype(entries["lane"]):
        raise ValueError("noncanonical_lane_type")
    features = build_frozen_features(entries, history)
    model = joblib.load(model_path)
    if tuple(str(value) for value in getattr(model, "feature_names_in_", ())) != MODEL_FEATURES:
        raise ValueError("feature_schema_mismatch")
    raw = model.predict_proba(features[list(MODEL_FEATURES)])[:, 1]
    output: list[dict[str, Any]] = []
    entries_by_key = entries.set_index(["race_id", "lane"])
    for race_id, group in features.groupby("race_id", sort=True):
        indexes = group.index.to_numpy()
        values = raw[indexes]
        total = float(values.sum())
        if not np.isfinite(total) or total <= 0:
            raise ValueError("invalid_probability_total")
        probabilities = values / total
        order = np.argsort(-probabilities, kind="stable")
        ranks = np.empty(len(order), dtype=int); ranks[order] = np.arange(1, len(order) + 1)
        for offset, (_, feature) in enumerate(group.iterrows()):
            source = entries_by_key.loc[(race_id, int(feature["lane"]))]
            output.append({
                "raceId": str(race_id), "venue": str(source["jcd"]).zfill(2),
                "raceNumber": int(source["race_no"]), "lane": int(feature["lane"]),
                "racerId": str(source["racer_id"]), "predictedProbability": float(probabilities[offset]),
                "probabilityRank": int(ranks[offset]), "topPrediction": int(ranks[offset]) == 1,
            })
    return output
