def test_fraction_limits_configurable() -> None:
    """preproc.bad_channels.max_fraction and epochs.reject.max_rejected_fraction move the fail
    limit (GABA infant VEP: 129-channel infant data); defaults are unchanged."""
    from eegunify.erp import qa

    bads = [f"E{i}" for i in range(40)]  # 40/129 = 31 %
    assert qa.bads_fraction_check(bads, 129).level == "fail"
    relaxed = qa.bads_fraction_check(bads, 129, fail_above=0.4)
    assert relaxed.level == "warn" and relaxed.threshold == [0.10, 0.4]
    assert qa.rejected_fraction_check(58, 100, {}).level == "fail"
    assert qa.rejected_fraction_check(58, 100, {}, fail_above=0.7).level == "warn"
    # A limit below the warning level: warn and fail coincide.
    assert qa.bads_fraction_check(bads[:6], 129, fail_above=0.05).threshold == [0.05, 0.05]
