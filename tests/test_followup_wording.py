"""Factual explanation regressions for the follow-up repair batch."""
from factories import at, night, user
from app.services.nap_recc import recommend_nap


def test_fine_report_explains_underestimated_impairment_without_week_threshold():
    data = user([night(-k,'01:00','07:00',eff=1) for k in range(7,0,-1)])
    result = recommend_nap(data,at(0,'14:00'),'fine')
    explanation = ' '.join(result.reasons)
    assert 'underestimate their growing performance impairment' in explanation
    assert 'stop noticing how tired' not in explanation
    assert 'After a week' not in explanation
