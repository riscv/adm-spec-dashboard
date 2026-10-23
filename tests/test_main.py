"""main() end to end, with a fake Bedrock client and a temporary cache file."""
import hashlib
import json

import pytest

from conftest import TODAY, FakeClient, make_row


def spec_rows(rows):
    return [rows[n] for n in ("development_on_track", "stabilization_exposed_slip", "cancelled", "fast_track_freeze")]


def test_output_shape(ais, rows, run_main):
    out, client = run_main(spec_rows(rows))
    assert set(out) == {"RVS-2", "RVS-3", "RVS-5", "RVS-6"}
    for key, entry in out.items():
        assert set(entry) >= {"hash", "summary", "source", "generated"}
        assert entry["generated"] == TODAY.isoformat()
        assert len(entry["hash"]) == 16 and int(entry["hash"], 16) >= 0
        if entry["source"] == "ai":
            assert entry["model"] == ais.MODEL_ID
        else:
            assert entry["source"] == "template" and "model" not in entry
    assert out["RVS-5"] == {"hash": out["RVS-5"]["hash"], "summary": "The specification was cancelled.",
                            "source": "template", "generated": TODAY.isoformat()}
    # The <think> block the fake model returns is stripped.
    assert out["RVS-2"]["source"] == "ai"
    assert "think" not in out["RVS-2"]["summary"]
    assert run_main.out_path.read_text() == json.dumps(out, indent=1, sort_keys=True)


def test_hash_is_model_prompt_and_facts(ais, rows, run_main):
    out, _ = run_main([rows["development_on_track"]])
    facts = ais.build_facts(rows["development_on_track"], TODAY)
    expected = hashlib.sha256(json.dumps([ais.MODEL_ID, ais.SYSTEM, facts]).encode()).hexdigest()[:16]
    assert out["RVS-2"]["hash"] == expected


def test_request_sends_only_the_facts(ais, rows, run_main):
    _, client = run_main([rows["stabilization_exposed_slip"]])
    (call,) = client.calls
    facts = ais.build_facts(rows["stabilization_exposed_slip"], TODAY)
    assert call["modelId"] == ais.MODEL_ID
    assert call["system"] == [{"text": ais.SYSTEM}]
    assert call["messages"] == [{"role": "user", "content": [{"text": "Facts:\n- " + "\n- ".join(facts)}]}]
    assert call["inferenceConfig"]["temperature"] == 0


def test_cache_hit_makes_no_calls(rows, run_main):
    first, c1 = run_main(spec_rows(rows))
    assert len(c1.calls) == 3  # every spec except the cancelled one
    second, c2 = run_main(spec_rows(rows))
    assert c2.calls == []
    assert second == first


def test_changed_facts_trigger_a_call(rows, run_main):
    first, _ = run_main(spec_rows(rows))
    rows["stabilization_exposed_slip"]["Baseline Ratification Quarter"] = "25Q4"
    second, client = run_main(spec_rows(rows))
    assert len(client.calls) == 1
    assert "It is 6 quarters behind the baseline 25Q4" in client.calls[0]["messages"][0]["content"][0]["text"]
    assert second["RVS-3"]["hash"] != first["RVS-3"]["hash"]
    assert "25Q4" in second["RVS-3"]["summary"]
    assert {k: v for k, v in second.items() if k != "RVS-3"} == {k: v for k, v in first.items() if k != "RVS-3"}


@pytest.mark.parametrize("attr,value", [("SYSTEM", "A different prompt."), ("MODEL_ID", "other.model-v1")])
def test_prompt_or_model_change_invalidates_cache(ais, rows, run_main, monkeypatch, attr, value):
    first, _ = run_main(spec_rows(rows))
    monkeypatch.setattr(ais, attr, value)
    second, client = run_main(spec_rows(rows))
    assert len(client.calls) == 3
    assert all(second[k]["hash"] != first[k]["hash"] for k in first)
    if attr == "MODEL_ID":
        assert client.calls[0]["modelId"] == value
        assert second["RVS-2"]["model"] == value


def test_bedrock_error_falls_back_to_template(ais, rows, run_main, capsys):
    out, client = run_main(spec_rows(rows), client=FakeClient(error=RuntimeError("throttled")))
    assert len(client.calls) == 3
    for key, row in (("RVS-2", rows["development_on_track"]), ("RVS-3", rows["stabilization_exposed_slip"])):
        assert out[key]["source"] == "template"
        assert out[key]["summary"] == ais.template(ais.build_facts(row, TODAY))
    assert "Bedrock call failed: throttled" in capsys.readouterr().err


def test_rejected_output_falls_back_to_template(ais, rows, run_main):
    reply = lambda facts: ". ".join(facts) + ". This is a significant risk."  # noqa: E731
    out, _ = run_main([rows["stabilization_exposed_slip"]], client=FakeClient(reply=reply))
    entry = out["RVS-3"]
    assert entry["source"] == "template"
    assert entry["summary"] == ais.template(ais.build_facts(rows["stabilization_exposed_slip"], TODAY))
    assert "risk" not in entry["summary"]


def test_number_words_from_model_become_digits(rows, run_main):
    reply = lambda facts: ". ".join(f.replace("2 quarters", "two quarters") for f in facts) + "."  # noqa: E731
    out, _ = run_main([rows["stabilization_exposed_slip"]], client=FakeClient(reply=reply))
    assert out["RVS-3"]["source"] == "ai"
    assert "2 quarters behind the baseline 26Q4" in out["RVS-3"]["summary"]


def test_template_entries_are_retried(rows, run_main):
    run_main([rows["stabilization_exposed_slip"]], client=FakeClient(error=RuntimeError("down")))
    out, client = run_main([rows["stabilization_exposed_slip"]])
    assert len(client.calls) == 1
    assert out["RVS-3"]["source"] == "ai"


def test_cancelled_never_calls_the_model(rows, run_main):
    cancelled = [rows["cancelled"], make_row("RVS-99", Status="Cancelled", **{"Target Ratification Quarter": "27Q1"})]
    out, client = run_main(cancelled)
    assert client.calls == []
    assert {e["source"] for e in out.values()} == {"template"}
    # Template entries are not cached as final, but cancelled ones still never reach the model.
    _, client = run_main(cancelled)
    assert client.calls == []


def test_no_model_uses_templates_and_keeps_ai_cache(rows, run_main):
    first, _ = run_main(spec_rows(rows))
    rows["fast_track_freeze"]["Baseline Ratification Quarter"] = "26Q1"
    out, client = run_main(spec_rows(rows), extra=["--no-model"])
    assert client.calls == []
    assert out["RVS-2"] == first["RVS-2"]  # unchanged ai summary kept
    assert out["RVS-6"]["source"] == "template"
    assert "26Q1" in out["RVS-6"]["summary"]


def test_only_prints_and_does_not_write(rows, run_main, capsys):
    out, client = run_main(spec_rows(rows), extra=["--only", "RVS-3"])
    assert out is None
    assert len(client.calls) == 1
    printed = capsys.readouterr().out
    assert "RVS-3" in printed and "RVS-2" not in printed


def test_quoted_csv_fields(ais, rows, run_main):
    """get-specs-data.py quotes with ', so a comma or quote in a field must survive."""
    rows["development_on_track"]["Summary"] = "Foo, 'bar' baz"
    rows["development_on_track"]["Next Approval"] = "Development complete, part 1"
    out, client = run_main([rows["development_on_track"]])
    assert "The next approval is Development complete, part 1, projected for 2026-12-25" in \
        client.calls[0]["messages"][0]["content"][0]["text"]
    assert out["RVS-2"]["source"] == "ai"
