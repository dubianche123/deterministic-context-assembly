"""Unit tests for the fast thinker weight calculation engine."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fast_thinker import _cosine_similarity, fast_think

def test_basic_intj():
    """Selecting only INTJ cards should yield INTJ."""
    selections = [
        {"id": "NORN_0001", "round": 1},  # INTJ
        {"id": "NORN_0017", "round": 1},  # INTJ (scene 2)
    ]
    result = fast_think(selections, total_rounds=1)
    assert result["mbti_type"] == "INTJ", f"Expected INTJ, got {result['mbti_type']}"
    assert result["data_density"] == "sparse_mode"
    print(f"✅ test_basic_intj: {result['mbti_type']} confidence={result['confidence']}")

def test_hesitation_weighting():
    """Longer hesitation should nudge, not dominate."""
    # Round 1: INTJ card, fast (2s)
    # Round 2: ENFP card, slow (20s)
    selections = [
        {"id": "NORN_0001", "round": 1},  # INTJ: E_I=-8, S_N=8, T_F=8, J_P=8
        {"id": "NORN_0008", "round": 2},  # ENFP: E_I=8, S_N=8, T_F=-8, J_P=-8
    ]
    durations = [
        {"round": 1, "duration_ms": 2000},
        {"round": 2, "duration_ms": 20000},  # 10x longer hesitation
    ]
    baseline = fast_think(selections, total_rounds=2)
    result = fast_think(selections, total_rounds=2, round_durations=durations)
    # The later ENFP card still leans positive, but hesitation only adds a small nudge.
    assert result["coords_avg"]["E_I"] > 0, f"Expected E, got E_I={result['coords_avg']['E_I']}"
    assert result["coords_avg"]["T_F"] < 0, f"Expected F, got T_F={result['coords_avg']['T_F']}"
    assert 0 < result["coords_avg"]["E_I"] - baseline["coords_avg"]["E_I"] < 1
    assert -1 < result["coords_avg"]["T_F"] - baseline["coords_avg"]["T_F"] < 0
    print(f"✅ test_hesitation_weighting: {result['mbti_type']} coords={result['coords_avg']}")

def test_round_decay():
    """Later rounds should have more influence even without hesitation."""
    # Round 1: 3 INTJ cards
    # Round 5: 1 ENFP card (but later round = higher weight)
    selections = [
        {"id": "NORN_0001", "round": 1},
        {"id": "NORN_0017", "round": 1},
        {"id": "NORN_0033", "round": 1},
        {"id": "NORN_0008", "round": 5},  # ENFP
    ]
    result = fast_think(selections, total_rounds=5)
    # Despite 3 INTJ vs 1 ENFP, round 5 has e^(5/5)=e^1=2.72 weight
    # vs round 1 having e^(1/5)=e^0.2=1.22 each (3 × 1.22 = 3.66 total)
    # So INTJ still wins in quantity, but ENFP pulls coords toward center
    print(f"✅ test_round_decay: {result['mbti_type']} coords={result['coords_avg']}")

def test_deselection_signal_nudges_revision_profile():
    """Cancelled selections should become a stronger uncertainty signal than timing."""
    selections = [
        {"id": "NORN_0001", "round": 1},
        {"id": "NORN_0017", "round": 2},
    ]
    baseline = fast_think(selections, total_rounds=3)
    result = fast_think(
        selections,
        total_rounds=3,
        deselection_events=[
            {"id": "NORN_0002", "round": 1},
            {"id": "NORN_0003", "round": 2},
            {"id": "NORN_0004", "round": 2},
        ],
    )
    assert result["choice_revision"]["total"] == 3
    assert result["choice_revision"]["signal"] == "high"
    assert result["coords_avg"]["J_P"] < baseline["coords_avg"]["J_P"]
    assert "反复校准" in result["top_traits"]
    print(f"✅ test_deselection_signal: revision={result['choice_revision']}")

def test_density_modes():
    """Verify correct density mode selection."""
    r1 = fast_think([{"id": "NORN_0001", "round": 1}], total_rounds=2)
    assert r1["data_density"] == "sparse_mode"

    r_boundary = fast_think([{"id": "NORN_0001", "round": 1}], total_rounds=5)
    assert r_boundary["data_density"] == "sparse_mode"

    r2 = fast_think([{"id": "NORN_0001", "round": 1}], total_rounds=10)
    assert r2["data_density"] == "dense_mode"

    r3 = fast_think([{"id": "NORN_0001", "round": 1}], total_rounds=25)
    assert r3["data_density"] == "overflow_mode"
    print(f"✅ test_density_modes: sparse={r1['data_density']}, dense={r2['data_density']}, overflow={r3['data_density']}")

def test_empty_selection():
    """No selections should return safe empty result."""
    result = fast_think([], total_rounds=3)
    assert result["mbti_type"] == "XXXX"
    print(f"✅ test_empty_selection: {result['mbti_type']}")


def test_signature_signal_and_cosine_similarity():
    """Signature signal should be grounded in selected motifs and cosine math should be stable."""
    selections = [
        {"id": "NORN_0001", "round": 1},
        {"id": "NORN_0017", "round": 2},
    ]
    result = fast_think(selections, total_rounds=2)
    assert result["signature_signal"] in result["top_motifs"]
    assert _cosine_similarity(
        {"E_I": 1, "S_N": 1, "T_F": 1, "J_P": 1},
        {"E_I": 2, "S_N": 2, "T_F": 2, "J_P": 2},
    ) == 1.0
    assert _cosine_similarity(
        {"E_I": 1, "S_N": 0, "T_F": 0, "J_P": 0},
        {"E_I": -1, "S_N": 0, "T_F": 0, "J_P": 0},
    ) < 0
    assert _cosine_similarity(
        {"E_I": 0, "S_N": 0, "T_F": 0, "J_P": 0},
        {"E_I": 1, "S_N": 1, "T_F": 1, "J_P": 1},
    ) == 0.0
    print(f"✅ test_signature_signal: {result['signature_signal']}")

if __name__ == "__main__":
    test_basic_intj()
    test_hesitation_weighting()
    test_round_decay()
    test_deselection_signal_nudges_revision_profile()
    test_density_modes()
    test_empty_selection()
    test_signature_signal_and_cosine_similarity()
    print("\n🎉 All tests passed!")
