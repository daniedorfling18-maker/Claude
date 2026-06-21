#!/usr/bin/env python3
"""Exact-score chasing: tilt the pick toward higher P(exact) for ceiling.

The engine picks argmax EV where EV = 3*P(exact)+1.5*P(close)+1*P(outcome) --
i.e. exact already valued at its true 3 points. This OVER-weights the exact term
in the decision (objective = EV + (w-1)*3*P(exact)), biasing toward the single
most-likely scoreline. w=1 == the engine's EV-optimal pick; w>1 chases exacts at
an EV cost. Use it when you're behind and need variance/ceiling.

Rebuilds the market scoreline distribution from predictions.json lambdas (same
self-checked machinery as ev_contrarian.py).
"""
import json, sys, argparse
from datetime import datetime, timezone
import numpy as np, pandas as pd
sys.path.insert(0, "scripts")
from ev_contrarian import dc_matrix, PTS, CAND, ocf

def decompose(M, ph, pa):
    P=PTS[(ph,pa)]
    pe=float(M[ph,pa])
    pc=float((M*(P==1.5)).sum())
    po=float((M*(P==1.0)).sum())
    return pe, pc, po, pe*3+pc*1.5+po*1.0

def pick(M, w):
    best=None
    for ph in range(CAND):
        for pa in range(CAND):
            pe,pc,po,ev=decompose(M,ph,pa)
            obj=ev+(w-1.0)*3.0*pe
            if best is None or obj>best[0]:
                best=(obj,(ph,pa),pe,ev)
    return best[1],best[2],best[3]   # scoreline, p_exact, true_EV

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("predictions_json")
    ap.add_argument("--weight", type=float, default=2.0)
    ap.add_argument("--cutoff", default="2026-06-16"); ap.add_argument("--out", default="outputs/exact_chase_report.csv")
    a=ap.parse_args()
    now=datetime(2026,6,16,5,40,tzinfo=timezone.utc)
    rows=[]; sweep={w:[ ] for w in (1.0,1.5,2.0,3.0)}
    for ev in json.load(open(a.predictions_json)):
        ct=datetime.fromisoformat(ev["commence_time"].replace("Z","+00:00"))
        if ct<now: continue
        dg=ev["diagnostics"]; lh,la=dg.get("lambda_home"),dg.get("lambda_away")
        if not lh or not la: continue
        M=dc_matrix(lh,la)
        b_pk,b_pe,b_ev=pick(M,1.0)               # EV-optimal baseline
        x_pk,x_pe,x_ev=pick(M,a.weight)          # exact-chase
        for w in sweep:
            _,pe,evv=pick(M,w); sweep[w].append((pe,evv))
        rows.append(dict(match=f'{ev["home_team"]} v {ev["away_team"]}',
            base=f'{b_pk[0]}-{b_pk[1]}', base_pexact=round(b_pe,3), base_ev=round(b_ev,3),
            chase=f'{x_pk[0]}-{x_pk[1]}', chase_pexact=round(x_pe,3), chase_ev=round(x_ev,3),
            changed=(b_pk!=x_pk), dPexact=round(x_pe-b_pe,3), dEV=round(x_ev-b_ev,3)))
    rep=pd.DataFrame(rows)
    import os; os.makedirs(os.path.dirname(a.out),exist_ok=True); rep.to_csv(a.out,index=False)

    print("Slate-level trade-off (avg per game across upcoming fixtures):")
    print(f"  {'exact-weight':14}{'avg P(exact)':>14}{'avg EV':>10}{'  (vs w=1)'}")
    base_ev=np.mean([e for _,e in sweep[1.0]]); base_pe=np.mean([p for p,_ in sweep[1.0]])
    for w in (1.0,1.5,2.0,3.0):
        pe=np.mean([p for p,_ in sweep[w]]); evv=np.mean([e for _,e in sweep[w]])
        print(f"  w={w:<12}{pe:>14.3f}{evv:>10.3f}   P(exact){pe-base_pe:+.3f}  EV{evv-base_ev:+.3f}")

    chg=rep[rep.changed]
    print(f"\nAt w={a.weight}: {len(chg)}/{len(rep)} picks change. Where they differ "
          f"(EV-optimal -> exact-chase):")
    print(f"  {'match':34}{'EV pick':>10}{'chase pick':>12}{'P(exact)':>18}{'EV cost':>9}")
    for r in chg.sort_values('dEV').itertuples():
        print(f"  {r.match[:33]:34}{r.base:>10}{r.chase:>12}"
              f"{f'{r.base_pexact:.2f}->{r.chase_pexact:.2f}':>18}{r.dEV:>9.3f}")
    print(f"\nfull report: {a.out}")

if __name__=="__main__":
    main()
