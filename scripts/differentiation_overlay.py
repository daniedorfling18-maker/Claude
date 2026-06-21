#!/usr/bin/env python3
"""Pool-differentiation overlay for World Cup Superbru.

Independent national-team Elo (built ONLY from past international results, no
odds) vs the market (de-vigged WC closing odds). It flags upcoming fixtures
where the independent model CONFIDENTLY DISAGREES with the field's consensus --
the principled contrarian spots that can move you up a pool.

Honest framing: the independent model is sub-market on accuracy (proven), so
these picks carry LOWER expected points but LOWER correlation with the field.
That trade is the point when you're chasing rank from behind.

Usage:
  python scripts/differentiation_overlay.py <odds_snapshot.json> [--cutoff 2026-06-16]
"""
import argparse, json, sys, math
from datetime import datetime, timezone
import numpy as np, pandas as pd
from scipy.optimize import minimize
from scipy.special import expit
from superbru_score_engine.model.team_names import canonical_team_key as ck

IR = "work/international_results.csv"
HFA = 65.0          # home-field Elo bump (0 at neutral venues)
K0 = 20.0

def tour_weight(t):
    t=(t or "").lower()
    if "friendly" in t: return 0.5
    if any(k in t for k in ("world cup","uefa euro","copa am","african cup","confederations")): return 1.4
    if "qualif" in t or "nations league" in t: return 1.1
    return 1.0

def build_elo(cutoff):
    df=pd.read_csv(IR)
    df["d"]=pd.to_datetime(df["date"], errors="coerce")
    df=df.dropna(subset=["d","home_team","away_team","home_score","away_score"])
    df=df[df.d < cutoff].sort_values("d")
    elo={}; hist=[]   # hist = (eff_diff, outcome) for fitting, non-leaky
    for r in df.itertuples():
        h=ck(r.home_team); a=ck(r.away_team)
        rh=elo.get(h,1500.0); ra=elo.get(a,1500.0)
        hfa=0.0 if bool(r.neutral) else HFA
        eff=rh+hfa-ra
        if r.home_score>r.away_score: o=2
        elif r.home_score<r.away_score: o=0
        else: o=1
        if r.d>=pd.Timestamp("2006-01-01"): hist.append((eff/100.0,o))
        e=1/(1+10**(-(eff/400)))
        s=1.0 if o==2 else 0.5 if o==1 else 0.0
        K=K0*tour_weight(r.tournament)*(1+abs(r.home_score-r.away_score))**0.5
        elo[h]=rh+K*(s-e); elo[a]=ra+K*((1-s)-(1-e))
    return elo, np.array(hist)

def fit_map(hist):
    z=hist[:,0]; y=hist[:,1].astype(int)
    def nll(p):
        beta,c1,d=p; c2=c1+np.exp(d); zz=beta*z
        pA=expit(c1-zz); pAD=expit(c2-zz)
        P=np.stack([pA,np.clip(pAD-pA,1e-9,1),1-pAD],1)
        return -np.log(np.clip(P[np.arange(len(y)),y],1e-12,1)).mean()
    r=minimize(nll,[1.,-.5,0.],method="Nelder-Mead",options={"maxiter":5000,"fatol":1e-9})
    b,c1,d=r.x; return b,c1,c1+np.exp(d)

def model_probs(eff_diff, p):           # returns [H,D,A]
    b,c1,c2=p; zz=b*(eff_diff/100.0)
    pA=expit(c1-zz); pAD=expit(c2-zz)
    return np.array([1-pAD, max(pAD-pA,1e-9), pA])

def market_probs(event):
    acc={}
    for bk in event.get("bookmakers",[]):
        for m in bk.get("markets",[]):
            if m.get("key")!="h2h": continue
            for o in m["outcomes"]:
                acc.setdefault(o["name"],[]).append(o["price"])
    if len(acc)<3: return None
    home,away=event["home_team"],event["away_team"]
    def avg(n):
        v=[x for x in acc.get(n,[]) if x and x>1]; return sum(v)/len(v) if v else None
    oH,oA=avg(home),avg(away)
    oD=avg("Draw") or (lambda d=[v for k,v in acc.items() if k not in (home,away)]: sum(d[0])/len(d[0]) if d and d[0] else None)()
    if not(oH and oD and oA): return None
    inv=np.array([1/oH,1/oD,1/oA]); return inv/inv.sum()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("snapshot")
    ap.add_argument("--cutoff", default="2026-06-16")
    ap.add_argument("--out", default="outputs/differentiation_report.csv")
    a=ap.parse_args()
    cutoff=pd.Timestamp(a.cutoff, tz="UTC").tz_localize(None)
    now=datetime.now(timezone.utc) if False else datetime(2026,6,16,5,0,tzinfo=timezone.utc)

    elo,hist=build_elo(cutoff); p=fit_map(hist)
    print(f"International Elo built from results < {a.cutoff} | teams rated={len(elo)} | fit n={len(hist)}")
    top=sorted(elo.items(),key=lambda kv:-kv[1])[:6]
    print("  top Elo:", ", ".join(f"{k}={v:.0f}" for k,v in top))

    events=json.load(open(a.snapshot))
    rows=[]
    for ev in events:
        ct=datetime.fromisoformat(ev["commence_time"].replace("Z","+00:00"))
        if ct < now: continue                      # upcoming only
        mkt=market_probs(ev)
        if mkt is None: continue
        h,aw=ev["home_team"],ev["away_team"]
        rh=elo.get(ck(h)); ra=elo.get(ck(aw))
        if rh is None or ra is None: continue       # unrated team
        mdl=model_probs(rh-ra, p)                   # WC = neutral, HFA=0
        lbl=["H","D","A"]; names=[h,"Draw",aw]
        mk=int(np.argmax(mkt)); md=int(np.argmax(mdl))
        agree = mk==md
        opp = float(mdl[md]-mkt[md])                # model's conviction of disagreement
        tier = "CONSENSUS" if agree else ("STRONG" if opp>0.10 else "LEAN" if opp>0.05 else "slight")
        rows.append(dict(kickoff=ct.strftime("%m-%d %H:%MZ"), home=h, away=aw,
            mkt_pick=names[mk], mkt_p=round(float(mkt[mk]),2),
            mdl_pick=names[md], mdl_p=round(float(mdl[md]),2),
            mkt_on_mdlpick=round(float(mkt[md]),2), opportunity=round(opp,3), tier=tier,
            elo_diff=round(rh-ra)))
    rep=pd.DataFrame(rows)
    if rep.empty: print("No upcoming rated fixtures in snapshot."); return
    rep=rep.sort_values(["tier","opportunity"], key=lambda s: s.map({"STRONG":0,"LEAN":1,"slight":2,"CONSENSUS":3}) if s.name=="tier" else -s)
    import os; os.makedirs(os.path.dirname(a.out),exist_ok=True); rep.to_csv(a.out,index=False)

    contr=rep[rep.tier.isin(["STRONG","LEAN"])]
    print(f"\nUpcoming fixtures: {len(rep)} | differentiation opportunities (model disagrees, conviction>5%): {len(contr)}\n")
    print("=== CONTRARIAN OPPORTUNITIES (rank-climbing gambles, ranked) ===")
    print(f"{'kickoff':12}{'match':34}{'FIELD picks':>20}{'MODEL says':>22}{'tier':>9}")
    for r in contr.itertuples():
        print(f"{r.kickoff:12}{(r.home+' v '+r.away)[:33]:34}"
              f"{(r.mkt_pick+' '+format(r.mkt_p,'.0%'))[:19]:>20}"
              f"{(r.mdl_pick+' '+format(r.mdl_p,'.0%')+' (mkt '+format(r.mkt_on_mdlpick,'.0%')+')')[:21]:>22}{r.tier:>9}")
    print(f"\nFull report (incl. consensus games to play safe): {a.out}")
    print("Reminder: contrarian picks = lower expected points, lower field-correlation. "
          "Use them to gain rank when behind; play CONSENSUS when protecting position.")

if __name__=="__main__":
    main()
