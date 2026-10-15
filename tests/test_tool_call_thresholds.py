"""Tool-call fatigue thresholds must use context detection after module import."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT_DIRS = [
    "skills/token-optimizer/scripts",
    "plugins/token-optimizer/skills/token-optimizer/scripts",
    "cowork/token-optimizer/skills/token-optimizer/scripts",
]


def _score_in_fresh_process(tmp_path, script_dir, *, model="opus", env=None,
                            calls=41, fill=0.60, cli_window=None, session_window=None,
                            session_model=None, repeat_env=None):
    home = tmp_path / "home"
    config = home / ".claude"
    config.mkdir(parents=True)
    (config / "settings.json").write_text(json.dumps({"model": model}), encoding="utf-8")
    child_env = {k: v for k, v in os.environ.items() if not k.startswith(
        ("TOKEN_OPTIMIZER_", "CLAUDE_", "ANTHROPIC_", "CODEX_", "OPENAI_", "XDG_"))}
    child_env.update({"HOME": str(home), "USERPROFILE": str(home),
                      "CLAUDE_CONFIG_DIR": str(config),
                      "TOKEN_OPTIMIZER_RUNTIME": "claude",
                      "TOKEN_OPTIMIZER_SNAPSHOT_DIR": str(home / "snap"),
                      "PYTHONPATH": str(REPO / script_dir)})
    child_env.update(env or {})
    code = f"""
import json
import measure as m
m._cli_context_size = {cli_window!r}
window = m.detect_context_window()[0]
session_window = {session_window!r} or window
compact = m._resolve_compact_window({session_model!r})
denominator = compact["tokens"] if compact["user_override"] and compact["tokens"] < session_window else session_window
data = {{'messages': [(0, 'user', 100, True)], 'tool_results': [],
        'system_reminders': [], 'reads': [], 'writes': [], 'compactions': 0,
        'tool_calls': {calls!r}, 'agent_dispatches': [], 'decisions': [],
        'context_tokens': int(denominator * {fill!r}), 'model_context_window': session_window,
        'model': {session_model!r}}}
result = m.compute_quality_score(data)
output = {{'window': window, 'warning': result['tool_call_warning']}}
if {repeat_env!r} is not None:
    import os
    os.environ.update({repeat_env!r})
    output['next_warning'] = m.compute_quality_score(data)['tool_call_warning']
print(json.dumps(output))
"""
    run = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                         env=child_env, capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.mark.parametrize("script_dir", SCRIPT_DIRS)
@pytest.mark.parametrize("calls,level", [(41, None), (201, None), (202, "WARNING"),
                                         (323, "WARNING"), (324, "CRITICAL")])
def test_one_million_settings_window_does_not_freeze_200k_thresholds(tmp_path, script_dir, calls, level):
    result = _score_in_fresh_process(tmp_path, script_dir, calls=calls)
    assert result['window'] == 1_000_000
    warning = result['warning']
    if level is None:
        assert warning is None
    else:
        assert warning['level'] == level
        assert warning['tool_calls'] == calls
        assert warning['message'] == ('324+ tool calls, adherence degraded' if level == 'CRITICAL'
                                      else '202+ tool calls, consider fresh session')


@pytest.mark.parametrize("script_dir", SCRIPT_DIRS)
@pytest.mark.parametrize("calls,level", [(24, None), (25, 'WARNING'), (39, 'WARNING'), (40, 'CRITICAL')])
def test_explicit_200k_retains_baseline_thresholds(tmp_path, script_dir, calls, level):
    result = _score_in_fresh_process(tmp_path, script_dir, calls=calls,
        env={'CLAUDE_CODE_DISABLE_1M_CONTEXT': '1'})
    assert result['window'] == 200_000
    assert (result['warning']['level'] if result['warning'] else None) == level


@pytest.mark.parametrize("script_dir", SCRIPT_DIRS)
@pytest.mark.parametrize("fill,level", [(0.499, None), (0.50, 'CRITICAL')])
def test_fill_gate_survives_lazy_resolution(tmp_path, script_dir, fill, level):
    result = _score_in_fresh_process(tmp_path, script_dir, calls=500, fill=fill)
    assert (result['warning']['level'] if result['warning'] else None) == level


@pytest.mark.parametrize("script_dir", SCRIPT_DIRS)
@pytest.mark.parametrize("calls,level,message", [
    (10, 'WARNING', '10+ tool calls, consider fresh session'),
    (12, 'CRITICAL', '12+ tool calls, adherence degraded'),
])
def test_environment_threshold_overrides_survive(tmp_path, script_dir, calls, level, message):
    result = _score_in_fresh_process(tmp_path, script_dir, calls=calls,
        env={'TOKEN_OPTIMIZER_TOOL_CALL_WARN': '10', 'TOKEN_OPTIMIZER_TOOL_CALL_CRITICAL': '12'})
    assert result['warning'] == {'level': level, 'tool_calls': calls, 'message': message}


@pytest.mark.parametrize('script_dir', SCRIPT_DIRS)
@pytest.mark.parametrize('source', ['env', 'cli'])
def test_context_overrides_are_applied_after_import(tmp_path, script_dir, source):
    result = _score_in_fresh_process(tmp_path, script_dir, calls=41,
        model='claude-sonnet-4-5',
        env={'TOKEN_OPTIMIZER_CONTEXT_SIZE': '1000000'} if source == 'env' else {},
        cli_window=1_000_000 if source == 'cli' else None)
    assert result == {'window': 1_000_000, 'warning': None}


@pytest.mark.parametrize("script_dir", SCRIPT_DIRS)
@pytest.mark.parametrize("compact", [False, True])
def test_thresholds_follow_the_sessions_effective_window(tmp_path, script_dir, compact):
    result = _score_in_fresh_process(tmp_path, script_dir, calls=40,
        session_window=1_000_000 if compact else 200_000,
        session_model='opus' if compact else 'claude-sonnet-4-5',
        env={'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '100000'} if compact else {})
    assert result['window'] == 1_000_000
    assert result['warning'] == {'level': 'CRITICAL', 'tool_calls': 40,
                                'message': '40+ tool calls, adherence degraded'}


@pytest.mark.parametrize("script_dir", SCRIPT_DIRS)
def test_threshold_overrides_are_reread_for_each_score(tmp_path, script_dir):
    result = _score_in_fresh_process(tmp_path, script_dir, calls=40,
        repeat_env={'TOKEN_OPTIMIZER_TOOL_CALL_CRITICAL': '40'})
    assert result['warning'] is None
    assert result['next_warning'] == {'level': 'CRITICAL', 'tool_calls': 40,
                                     'message': '40+ tool calls, adherence degraded'}


@pytest.mark.parametrize("script_dir", SCRIPT_DIRS)
@pytest.mark.parametrize("calls,level,message", [
    (65, "WARNING", "65+ tool calls, consider fresh session"),
    (104, "CRITICAL", "104+ tool calls, adherence degraded"),
])
def test_intermediate_window_thresholds_truncate_not_round(tmp_path, script_dir, calls, level, message):
    result = _score_in_fresh_process(tmp_path, script_dir, calls=calls,
        env={"TOKEN_OPTIMIZER_CONTEXT_SIZE": "420000"})
    assert result['warning'] == {'level': level, 'tool_calls': calls, 'message': message}
