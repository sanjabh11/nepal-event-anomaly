"""Arm C daily lane — prespecified ascent-spell statistics (v16 Phase 4).

Descriptive-only companion statistics on the byte-bound daily frame.
Frozen definitions (declared here before any result is seen):
- ascent day: w500_mean < 0 (negative pressure-velocity = synoptic ascent)
- moist day:  tcwv_mean >= basin-median over train rows
- spell: maximal run of consecutive days satisfying the condition
- per basin-year: spell count, mean length, max length; per basin:
  JJA totals across all 25 years
No event labels are joined; this output cannot promote or rescue the
regime result.  Output: daily-frame-v1/ascend_spell_stats_v0.json
"""
import json, sys, hashlib
from pathlib import Path
sys.path.insert(0, "scripts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from p5_safe_io import write_once_json, write_once_sidecar

ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence/p5-armc-pressure-levels-2026-09-22")
FRAME = ROOT / "daily-frame-v1" / "armc_daily_frame_v0.csv"
OUT = ROOT / "daily-frame-v1" / "ascend_spell_stats_v0.json"
MIN_LEN = 3

def _sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def spells(cond: pd.Series, dates: pd.Series):
    """Return list of (start_date, length) for runs of True of len>=MIN_LEN."""
    out, run_start, run_len = [], None, 0
    prev_d = None
    for ok, d in zip(cond.values, dates.values):
        if ok:
            if prev_d is not None and int((d - prev_d) / np.timedelta64(1, 'D')) == 1:
                run_len += 1
            else:
                if run_len >= MIN_LEN:
                    out.append((str(start), run_len))
                run_start, run_len = d, 1
        else:
            if run_len >= MIN_LEN:
                out.append((str(start), run_len))
            run_len = 0
            run_start = d
        start = run_start
        prev_d = d
    if run_len >= MIN_LEN:
        out.append((str(run_start), run_len))
    return out

def main():
    df = pd.read_csv(FRAME)
    df["date"] = pd.to_datetime(df["date"])
    train = df[(df["date"] >= "2001-06-01") & (df["date"] <= "2017-08-31")]
    result = {"schema": "P5_ARMC_SPELL_STATS_V0",
              "claim_scope": "research_only_no_operational_authorization",
              "frame_sha256": _sha(FRAME),
              "definitions": {"ascent_day": "w500_mean < 0",
                              "moist_day": "tcwv_mean >= basin train median",
                              "min_spell_len_days": MIN_LEN},
              "by_basin": {}}
    for basin, bdf in df.sort_values("date").groupby("unit_id"):
        btrain = bdf[(bdf["date"] >= "2001-06-01") & (bdf["date"] <= "2017-08-31")]
        med = float(btrain["tcwv_mean"].median())
        rec = {"tcwv_train_median": med, "ascent_spells": {}, "moist_spells": {}}
        for label, cond in (("ascent_spells", bdf["w500_mean"] < 0),
                            ("moist_spells", bdf["tcwv_mean"] >= med)):
            for year, ydf in bdf.groupby(bdf["date"].dt.year):
                ycond = cond.loc[ydf.index]
                sp = spells(ycond, ydf["date"])
                lens = [l for _, l in sp]
                rec[label][int(year)] = {"count": len(sp),
                                         "mean_len": round(float(np.mean(lens)), 2) if lens else 0.0,
                                         "max_len": int(max(lens)) if lens else 0}
        result["by_basin"][basin] = rec
    write_once_json(OUT, result, indent=2)
    write_once_sidecar(OUT)
    print(json.dumps({"status": "SPELL_STATS_WRITTEN", "out": str(OUT)}, indent=2))

if __name__ == "__main__":
    main()
