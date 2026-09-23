import json
import threading
from unittest.mock import Mock
import pytest
from app import ai_advisor as mod

@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, 'MEMORY_PATHS', [str(tmp_path/'memory.json')])
    clock = [1800000000.]
    monkeypatch.setattr(mod.time, 'time', lambda: clock[0])
    response = Mock()
    response.json.return_value = {
        'choices': [{'finish_reason':'stop','message':{'content':json.dumps({'action':'HOLD','confidence':20})}}],
        'usage': {'prompt_tokens':100,'completion_tokens':30,'total_tokens':130,'prompt_cache_hit_tokens':20}}
    post = Mock(return_value=response)
    monkeypatch.setattr(mod.requests,'post',post)
    return mod.DeepSeekAdvisor(api_key='test-key'), post, response, clock


def advise(advisor, symbol='EURUSD', candle=100):
    return advisor.get_market_advice(symbol, rates=[{'time':candle,'close':1},{'time':candle+900,'close':2}],
                                    tick={'bid':1,'ask':1.1,'spread':10})


def test_same_closed_candle_cached_even_when_expired(setup):
    a,post,res,clock=setup
    assert advise(a)['success']
    assert advise(a)['cached']
    clock[0]+=1000
    cached=advise(a)
    assert cached['cached'] and cached['stale']
    assert post.call_count==1
    assert not a.execute_recommendation(cached['recommendation'])['success']
    clock[0]+=60
    assert advise(a,candle=1000)['success']
    assert post.call_count==2


def test_cache_and_usage_survive_restart(setup):
    a,post,res,clock=setup
    advise(a)
    b=mod.DeepSeekAdvisor(api_key='test-key')
    assert advise(b)['cached']
    assert post.call_count==1
    status=b.get_status()
    assert status['usage']['calls']==1
    assert status['usage']['total_tokens']==130
    assert status['usage']['cache_hit_tokens']==20
    assert status['usage']['unknown_usage_calls']==0


def test_daily_call_limit_shared_by_symbols(setup):
    a,post,res,clock=setup
    a.daily_call_limit=1
    assert advise(a)['success']
    assert not advise(a,symbol='GBPUSD')['success']
    assert post.call_count==1
    assert advise(a)['cached']  # cached answers remain free even after quota


def test_reported_token_budget_stops_next_request(setup):
    a,post,res,clock=setup
    a.daily_token_limit=100
    assert advise(a)['success']
    assert not advise(a,symbol='GBPUSD')['success']
    assert post.call_count==1


def test_error_throttle_and_unknown_usage(setup):
    a,post,res,clock=setup
    post.side_effect=TimeoutError('provider did not return usage')
    assert not advise(a)['success']
    assert not advise(a)['success']
    assert post.call_count==1
    assert a.get_status()['usage']['unknown_usage_calls']==1


def test_no_paid_request_without_data_or_key(setup):
    a,post,res,clock=setup
    assert not a.get_market_advice('EURUSD')['success']
    a.api_key=''
    assert not advise(a)['success']
    post.assert_not_called()


def test_truncated_response_accounted_not_cached_or_retried(setup):
    a,post,res,clock=setup
    data=res.json.return_value
    data['choices'][0]['finish_reason']='length'
    assert not advise(a)['success']
    assert not a.cost_state['advice_cache']
    assert a.get_status()['usage']['total_tokens']==130
    assert not advise(a)['success']
    assert post.call_count==1


def test_unchanged_history_does_not_relearn(setup):
    a,post,res,clock=setup
    res.json.return_value['choices'][0]['message']['content']=json.dumps({'persona':{},'learned_rules':[]})
    trades=[{'ticket':i,'symbol':'EURUSD','type':'BUY','profit':1} for i in range(50)]
    assert a.analyze_user_trades(trades)['success']
    assert a.analyze_user_trades(trades)['cached']
    prompt=json.loads(post.call_args.kwargs['json']['messages'][1]['content'].split('\n',1)[1])
    assert len(prompt['recent_examples'])==8
    assert prompt['by_symbol_side']['EURUSD:BUY']['count']==50
    assert post.call_count==1
    clock[0]+=61
    assert a.analyze_user_trades([{'ticket':51,'profit':2}]+trades)['success']
    assert post.call_count==2


def test_learning_language_changes_profile_and_prompt(setup):
    a, post, response, clock = setup
    response.json.return_value['choices'][0]['message']['content'] = json.dumps({'persona': {}, 'learned_rules': []})
    trades = [{'ticket': 1, 'symbol': 'EURUSD', 'type': 'BUY', 'profit': 1}]
    assert a.analyze_user_trades(trades, language='tr')['success']
    clock[0] += 61
    assert a.analyze_user_trades(trades, language='en')['success']
    assert a.memory['language'] == 'en'
    assert 'natural English' in post.call_args.kwargs['json']['messages'][0]['content']
    assert a.analyze_user_trades(trades, language='en')['cached']
    assert post.call_count == 2


def test_advice_cache_is_separate_for_each_language(setup):
    a, post, response, clock = setup
    rates = [{'time': 100, 'close': 1}, {'time': 1000, 'close': 2}]
    tick = {'bid': 1, 'ask': 1.1, 'spread': 10}
    def request(language):
        return a.get_market_advice('EURUSD', rates=rates, tick=tick, language=language)
    assert request('tr')['success']
    clock[0] += 61
    english = request('en')
    assert english['success'] and english['recommendation']['language'] == 'en'
    assert 'clear English rationale' in post.call_args.kwargs['json']['messages'][0]['content']
    assert request('en')['cached']
    assert request('tr')['cached']
    assert post.call_count == 2


def test_autopilot_holds_and_errors_cannot_repeat_within_bucket(setup):
    a,post,res,clock=setup
    assert a.claim_autopilot_cycle()
    advise(a)  # HOLD must not reset cadence
    assert not a.claim_autopilot_cycle()
    restarted=mod.DeepSeekAdvisor(api_key='test-key')
    assert not restarted.claim_autopilot_cycle()
    clock[0]+=900
    assert restarted.claim_autopilot_cycle()


def test_simultaneous_requests_make_only_one_paid_call(setup):
    a,post,res,clock=setup
    entered,release=threading.Event(),threading.Event()
    def slow(*args,**kwargs):
        entered.set(); assert release.wait(2); return res
    post.side_effect=slow
    worker=threading.Thread(target=advise,args=(a,))
    worker.start()
    try:
        assert entered.wait(1)
        assert not advise(a)['success']
    finally:
        release.set();worker.join(2)
    assert post.call_count==1


def test_failed_persistence_prevents_paid_request(setup, monkeypatch):
    a,post,res,clock=setup
    monkeypatch.setattr(a,'save_memory',lambda:False)
    assert not advise(a)['success']
    post.assert_not_called()


def test_quota_resets_on_utc_day_boundary(setup, monkeypatch):
    from datetime import datetime, timezone
    a,post,res,clock=setup
    a.daily_call_limit=1
    date=[datetime(2026,9,19,23,59,tzinfo=timezone.utc)]
    class FixedDate:
        @staticmethod
        def now(tz): return date[0]
    monkeypatch.setattr(mod,'datetime',FixedDate)
    assert advise(a)['success']
    assert not advise(a,symbol='GBPUSD')['success']
    date[0]=datetime(2026,9,20,0,0,tzinfo=timezone.utc)
    assert advise(a,symbol='GBPUSD')['success']
    assert a.get_status()['usage']['calls']==1
