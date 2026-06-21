#!/usr/bin/env python3
"""Where is being contrarian CHEAPEST? Two rankings:

  (1) cheapest deviation off the consensus to ANY other outcome (almost always
      the 1-1 draw in tight games) -- the smallest EV sacrifice for max
      differentiation, and
  (2) cheapest deviation that FOLLOWS the independent Elo's lean (its preferred
      underdog/draw outcome) -- the model-backed contrarian direction.

EVs are computed against the market scoreline distribution rebuilt from the
engine's own lambda_home/lambda_away (predictions.json diagnostics); a self-
check reproduces the engine's expected-points exactly. Host nations
(USA/Canada/Mexico) are flagged because the neutral-venue Elo under-rates them.
"""
import json, sys, argparse
from datetime import datetime, timezone
import numpy as np, pandas as pd
from scipy.stats import poisson
sys.path.insert(0, "scripts")
from differentiation_overlay import build_elo, fit_map, model_probs
from superbru_score_engine.model.team_names import canonical_team_key as ck

RHO=-0.08; N=11; CAND=7
HOSTS={"United States","USA","Canada","Mexico"}

def dc_matrix(lh,la,rho=RHO,n=N):
    M=np.outer(poisson.pmf(np.arange(n),lh), poisson.pmf(np.arange(n),la))
    M[0,0]*=1-lh*la*rho; M[0,1]*=1+lh*rho; M[1,0]*=1+la*rho; M[1,1]*=1-rho
    return M/M.sum()

def ocf(h,a): return 'H' if h>a else 'A' if a>h else 'D'
def points(ph,pa,i,j):
    if ph==i and pa==j: return 3.0
    if ocf(ph,pa)!=ocf(i,j): return 0.0
    d=abs(ph-i)+abs(pa-j)
    return 1.5 if (d==1 or (d==2 and (ph-pa)==(i-j))) else 1.0
PTS={(ph,pa): np.array([[points(ph,pa,i,j) for j in range(N)] for i in range(N)])
     for ph in range(CAND) for pa in range(CAND)}
def ev_all(M): return {k:float((M*PTS[k]).sum()) for k in PTS}

def analyse(pred_json, cutoff, now):
    elo,hist=build_elo(pd.Timestamp(cutoff)); p=fit_map(hist)
    rows=[]; val_err=[]
    for ev in json.load(open(pred_json)):
        ct=datetime.fromisoformat(ev["commence_time"].replace("Z","+00:00"))
        if ct<now: continue
        dg=ev["diagnostics"]; lh,la=dg.get("lambda_home"),dg.get("lambda_away")
        if not lh or not la: continue
        M=dc_matrix(lh,la); E=ev_all(M)
        cons=max(E,key=E.get); Ec=E[cons]; Oc=ocf(*cons)
        best={}
        for (ph,pa),e in E.items():
            o=ocf(ph,pa)
            if o not in best or e>best[o][1]: best[o]=((ph,pa),e)
        alts={o:v for o,v in best.items() if o!=Oc}
        cheap_o=min(alts,key=lambda o:Ec-alts[o][1]); (cp,ce)=alts[cheap_o]
        rc=ev["recommended"]; rpk=(rc["home_goals"],rc["away_goals"])
        if rpk in E: val_err.append(abs(E[rpk]-rc["expected_points"]))
        nm={'H':ev["home_team"],'D':"Draw",'A':ev["away_team"]}
        rh=elo.get(ck(ev["home_team"])); ra=elo.get(ck(ev["away_team"]))
        mlean=None; m_pick=''; m_cost=None
        if rh is not None and ra is not None:
            mp=model_probs(rh-ra,p); mlean=['H','D','A'][int(np.argmax(mp))]
            if mlean!=Oc:
                (mp_,me)=best[mlean]; m_pick=f'{mp_[0]}-{mp_[1]} ({nm[mlean]})'; m_cost=round(Ec-me,3)
        host = ev["home_team"] in HOSTS or ev["away_team"] in HOSTS
        rows.append(dict(kickoff=ct.strftime("%m-%d %H:%MZ"),
            match=f'{ev["home_team"]} v {ev["away_team"]}',
            consensus=f'{cons[0]}-{cons[1]} ({nm[Oc]})', cons_ev=round(Ec,3),
            cheapest_contrarian=f'{cp[0]}-{cp[1]} ({nm[cheap_o]})', cheap_ev_cost=round(Ec-ce,3),
            model_lean=nm.get(mlean,'?'), model_pick=m_pick, model_ev_cost=m_cost,
            host='host' if host else ''))
    return pd.DataFrame(rows), (np.mean(val_err) if val_err else float('nan'))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("predictions_json")
    ap.add_argument("--cutoff",default="2026-06-16"); ap.add_argument("--out",default="outputs/ev_contrarian_report.csv")
    a=ap.parse_args()
    rep,verr=analyse(a.predictions_json,a.cutoff,datetime(2026,6,16,5,40,tzinfo=timezone.utc))
    import os; os.makedirs(os.path.dirname(a.out),exist_ok=True); rep.to_csv(a.out,index=False)
    print(f"self-check: mean |my EV - engine EV| = {verr:.4f} (0 = exact match)\n")

    print("(1) CHEAPEST CONTRARIAN — smallest EV sacrifice to leave the consensus (mostly draws):")
    t1=rep.sort_values("cheap_ev_cost")
    print(f"  {'match':34}{'consensus':18}{'pick instead':20}{'EVcost':>7}")
    for r in t1.head(10).itertuples():
        print(f"  {r.match[:33]:34}{r.consensus:18}{r.cheapest_contrarian:20}{r.cheap_ev_cost:>7.3f}")

    print("\n(2) MODEL-BACKED CONTRARIAN — follow the independent Elo lean, cheapest EV cost first:")
    t2=rep[rep.model_ev_cost.notna()].sort_values("model_ev_cost")
    print(f"  {'match':34}{'consensus':18}{'Elo pick':22}{'EVcost':>7}  note")
    for r in t2.head(12).itertuples():
        note='<- HOST: Elo under-rates, ignore' if r.host else ''
        print(f"  {r.match[:33]:34}{r.consensus:18}{r.model_pick:22}{r.model_ev_cost:>7.3f}  {note}")
    print(f"\nfull report: {a.out}")

if __name__=="__main__":
    main()
