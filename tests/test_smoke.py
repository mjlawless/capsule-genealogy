"""Smoke test — the package imports and the TreeAdapter boundary exists."""

def test_package_imports():
    import capsule  # noqa: F401


def test_tree_adapter_protocol_exists():
    from capsule.contracts.tree_adapter import TreeAdapter
    assert TreeAdapter is not None
