#!/usr/bin/env python3
"""Independent-strength-vs-closing-market test.

Builds a walk-forward Elo model from football-data.co.uk RESULTS ONLY (no odds
input -> genuinely independent of the market), maps Elo difference to 1X2
probabilities via an ordered logit fit on a TRAIN period, then evaluates on a
held-out TEST period against the closing line:
  * log-loss / Brier vs the de-vigged closing odds (and a naive base-rate)
  * flat-stake betting ROI of the model's value picks at closing odds

If the model can't beat the closing line (lower log-loss / positive ROI),
'beat the market' is dead for public data.
"""
import glob, os, sys
import numpy as np, pandas as pd
from scipy.optimize import minimize
from scipy.special import expit

DATA_DIR = sys.argv[1] if len(sys.argv) > 1 else "work/football-data-leagues"
# sharpest closing odds first (Pinnacle closing -> Pinnacle -> avg closing -> ...)
ODDS_SETS = [("PSCH","PSCD","PSCA"),("PSH","PSD","PSA"),("AvgCH","AvgCD","AvgCA"),
             ("AvgH","AvgD","AvgA"),("B365CH","B365CD","B365CA"),
             ("B365H","B365D","B365A"),("BbAvH","BbAvD","BbAvA")]

def load():
    rows=[]
    for f in sorted(glob.glob(os.path.join(DATA_DIR,"*.csv"))):
        try: df=pd.read_csv(f, encoding="latin-1")
        except Exception: continue
        if not {"HomeTeam","AwayTeam","FTHG","FTAG"}.issubset(df.columns): continue
        oset=next((s for s in ODDS_SETS if all(c in df.columns for c in s)), None)
        if not oset: continue
        s=df[["Date","HomeTeam","AwayTeam","FTHG","FTAG",*oset]].copy()
        s.columns=["Date","Home","Away","HG","AG","OH","OD","OA"]
        s["league"]=os.path.basename(f).split("_")[-1].replace(".csv","")
        s["book"]=oset[0]
        rows.append(s)
    d=pd.concat(rows, ignore_index=True)
    d["Date"]=pd.to_datetime(d["Date"], dayfirst=True, errors="coerce")
    for c in ("HG","AG","OH","OD","OA"): d[c]=pd.to_numeric(d[c], errors="coerce")
    d=d.dropna(subset=["Date","Home","Away","HG","AG","OH","OD","OA"])
    d=d[(d.OH>1)&(d.OD>1)&(d.OA>1)]
    d["res"]=np.where(d.HG>d.AG,"H",np.where(d.HG<d.AG,"A","D"))
    return d.sort_values("Date").reset_index(drop=True)

def walk_forward_elo(d, HFA=65.0, K0=20.0):
    elo={}; dr=np.empty(len(d))
    for i,r in enumerate(d.itertuples()):
        rh=elo.get(r.Home,1500.0); ra=elo.get(r.Away,1500.0)
        dr[i]=rh+HFA-ra
        e=1/(1+10**(-((rh+HFA-ra)/400)))
        s=1.0 if r.res=="H" else 0.5 if r.res=="D" else 0.0
        K=K0*(1+abs(r.HG-r.AG))**0.5
        elo[r.Home]=rh+K*(s-e); elo[r.Away]=ra+K*((1-s)-(1-e))
    return dr

def ordered_logit_fit(z, y):  # y in {0:A,1:D,2:H}; z=scaled elo diff
    def nll(p):
        beta,c1,d=p; c2=c1+np.exp(d)            # c2>c1
        zz=beta*z
        pA=expit(c1-zz); pAD=expit(c2-zz)
        P=np.stack([pA, np.clip(pAD-pA,1e-9,1), 1-pAD],1)
        return -np.log(np.clip(P[np.arange(len(y)),y],1e-12,1)).mean()
    r=minimize(nll,[1.0,-0.5,0.0],method="Nelder-Mead",
               options={"maxiter":5000,"xatol":1e-6,"fatol":1e-8})
    beta,c1,d=r.x; return beta,c1,c1+np.exp(d)

def probs(z,beta,c1,c2):
    zz=beta*z; pA=expit(c1-zz); pAD=expit(c2-zz)
    return np.stack([1-pAD, np.clip(pAD-pA,1e-9,1), pA],1)  # [H,D,A]

def devig(o):
    inv=1/o; return inv/inv.sum(1,keepdims=True)

def logloss(P,y):  # y idx into [H,D,A]
    return -np.log(np.clip(P[np.arange(len(y)),y],1e-12,1)).mean()
def brier(P,y):
    Y=np.eye(3)[y]; return ((P-Y)**2).sum(1).mean()

def main():
    d=load()
    idx={"H":0,"D":1,"A":2}; y=d.res.map(idx).to_numpy()
    z=walk_forward_elo(d)/100.0
    test=d.Date>=pd.Timestamp("2022-07-01")     # held-out: 2022/23-2024/25
    tr=~test
    yo={"A":0,"D":1,"H":2}; y_ord=d.res.map(yo).to_numpy()
    beta,c1,c2=ordered_logit_fit(z[tr.to_numpy()], y_ord[tr.to_numpy()])
    M=probs(z,beta,c1,c2)                                   # model [H,D,A]
    O=d[["OH","OD","OA"]].to_numpy(); Q=devig(O)            # market [H,D,A]
    base=np.eye(3)[y[tr.to_numpy()]].mean(0)               # naive base rates (train)
    N=np.tile(base,(len(d),1))
    te=test.to_numpy()
    print(f"matches={len(d)}  train={tr.sum()}  test(2022/23-2024/25)={te.sum()}  leagues={d.league.nunique()}")
    print(f"Elo->prob fit: beta={beta:.3f} cuts=({c1:.2f},{c2:.2f})\n")
    print("TEST-set quality (lower = better):")
    print(f"  {'':14}{'log-loss':>10}{'Brier':>10}")
    for name,P in [("naive(base)",N),("MARKET(close)",Q),("Elo model",M)]:
        print(f"  {name:14}{logloss(P[te],y[te]):>10.4f}{brier(P[te],y[te]):>10.4f}")
    edge=logloss(Q[te],y[te])-logloss(M[te],y[te])
    print(f"\n  model minus market log-loss edge = {edge:+.4f}  "
          f"({'MODEL better' if edge>0 else 'market better'})")

    # betting ROI vs closing odds (flat stakes on model value picks)
    print("\nBetting ROI vs CLOSING odds (test set, flat 1u):")
    res_idx=y[te]; Mt=M[te]; Ot=O[te]
    for thr in (0.0,0.05,0.10):
        val=Mt*Ot-1.0
        bet=val>thr
        stake=bet.sum()
        # profit: for each (match,outcome) bet -> (odds-1) if hit else -1
        hit=np.eye(3)[res_idx].astype(bool)
        profit=np.where(bet, np.where(hit,Ot-1.0,-1.0),0.0).sum()
        roi=profit/stake if stake else float("nan")
        print(f"  edge>{thr:.0%}: bets={int(stake):6d}  ROI={roi:+.2%}")

    # per-league: where (if anywhere) does the model beat the market?
    print("\nPer-league model-minus-market log-loss edge (positive = model beats market):")
    dd=d[te].copy(); dd["mll"]=-np.log(np.clip(M[te][np.arange(te.sum()),y[te]],1e-12,1))
    dd["qll"]=-np.log(np.clip(Q[te][np.arange(te.sum()),y[te]],1e-12,1))
    g=dd.groupby("league").apply(lambda x:pd.Series(
        {"n":len(x),"edge":x.qll.mean()-x.mll.mean()})).sort_values("edge",ascending=False)
    for lg,row in g.iterrows():
        flag="  <-- model better" if row.edge>0 else ""
        print(f"  {lg:5} n={int(row.n):5d}  edge={row.edge:+.4f}{flag}")

if __name__=="__main__":
    main()
