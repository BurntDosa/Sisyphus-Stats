"""Pure tier/role regression checks for the PR's reworked balancing core."""
import itertools
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sisyphus.balance import Player,TIERS,ROLES,balance,parse_tier

for tier in TIERS:
    assert parse_tier(tier)==tier
    assert Player(tier,tier,tier).rating==TIERS.index(tier)
for n in (8,10):
    roster=[Player(str(i),'Same name',TIERS[(i*3)%10],('mid','supp')) for i in range(n)]
    splits=balance(roster,omitted='adc')
    totals=[abs(sum(roster[i].rating for i in a)-sum(roster[i].rating for i in range(n) if i not in a)) for a in itertools.combinations(range(n),n//2)]
    assert all(s.gap==min(totals) for s in splits)
    for s in splits:
        slots=(*s.team_a,*s.team_b)
        assert s.score[1]==sum(x.fit=='Off-role' for x in slots)
        assert s.score[2]==sum(x.fit=='Secondary' for x in slots)
        assert len({x.role for x in s.team_a})==n//2
        assert len({x.role for x in s.team_b})==n//2
        assert all(x.role!='adc' for x in slots) if n==8 else True
print('Balance smoke passed: tier-only apex scale, identity, fairness and lane costs.')
