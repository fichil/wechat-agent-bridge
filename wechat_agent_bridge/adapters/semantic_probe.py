"""Read-only interface inventory. Availability never grants write permission."""

PATTERNS = {"LegacyIAccessible": (10018, 30090), "ObjectModel": (10022, 30112)}


def inspect_patterns(element):
    results = {}
    for name, (pattern_id, property_id) in PATTERNS.items():
        record = {"status": "probe_failed", "write": "unverified", "source": "current_uia_pattern"}
        try:
            available = element.GetCurrentPropertyValue(property_id)
            if available is False or available == 0:
                record["status"] = "unsupported"
            elif available is True or available == 1:
                record["status"] = "available" if element.GetCurrentPattern(pattern_id) else "probe_failed"
        except Exception:
            pass  # COM messages can contain private UI text.
        results[name] = record
    return results


def candidate_decision(patterns, legacy_origin=None):
    if (patterns.get("ObjectModel", {}).get("status") == "unsupported"
            and patterns.get("LegacyIAccessible", {}).get("status") == "available"
            and legacy_origin and legacy_origin.get("native_accessible") is False):
        return "blocked_no_independent_native_write_evidence"
    if any(p["status"] == "available" for p in patterns.values()):
        return "needs_independent_documented_implementation_evidence"
    if all(p["status"] == "unsupported" for p in patterns.values()):
        return "no_exposed_alternative"
    return "probe_inconclusive"


def inspect_legacy_origin(interface):
    """GetIAccessible is a read-only native-server discriminator, not a write."""
    try:
        native = interface.GetIAccessible()
        return {"status": "queried", "native_accessible": bool(native),
                "independent_write_path": "unverified" if native else "not_established_no_native_accessible"}
    except Exception:
        return {"status": "probe_failed", "native_accessible": None, "independent_write_path": "unverified"}
