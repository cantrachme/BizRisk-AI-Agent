import pytest
from app.entity_resolution.resolver import resolve_entity


def test_entity_resolution_benchmark():
    # Define verification benchmark cases: (Target, Candidates List, Expected Match Index or None)
    benchmark_cases = [
        # 1. Exact Name Matching with differing casing and spacing
        (
            {"business_name": "ABC Foods Private Limited", "name": "ABC Foods Private Limited"},
            [{"business_name": "abc  foods   private   limited", "name": "abc  foods   private   limited"}],
            0
        ),
        # 2. Brand name/legal suffixes matching with matching CIN
        (
            {"business_name": "ABC Foods Pvt Ltd", "name": "ABC Foods Pvt Ltd", "cin": "L12345MH2020PLC000001"},
            [
                {"business_name": "Tata Sons Pvt Ltd", "name": "Tata Sons Pvt Ltd", "cin": "L12345MH1917PLC000001"},
                {"business_name": "ABC Foods Private Limited", "name": "ABC Foods Private Limited", "cin": "L12345MH2020PLC000001"},
                {"business_name": "Reliance LLP", "name": "Reliance LLP", "cin": "L12345MH2018PLC000002"}
            ],
            1
        ),
        # 3. Abbreviations matching with matching website
        (
            {"business_name": "abcfoods", "name": "abcfoods", "website": "abcfoods.in"},
            [
                {"business_name": "Tata Sons", "name": "Tata Sons", "website": "tatasons.com"},
                {"business_name": "ABC Foods Private Limited", "name": "ABC Foods Private Limited", "website": "abcfoods.in"}
            ],
            1
        ),
        # 4. Same brand/legal, different identifiers (should match if identifiers match)
        (
            {"business_name": "ABC Foods", "name": "ABC Foods", "gstin": "27ABCDE1234F1Z5"},
            [
                {"business_name": "ABC Foods Private Limited", "name": "ABC Foods Private Limited", "gstin": "27ABCDE1234F1Z5"},
                {"business_name": "Tata Sons Limited", "name": "Tata Sons Limited", "gstin": "27ABCDE1234F2Z6"}
            ],
            0
        ),
        # 5. Distinct entities with similar names but different locations/identifiers (should NOT merge)
        (
            {"business_name": "ABC Foods Delhi", "name": "ABC Foods Delhi", "location": "Delhi"},
            [
                {"business_name": "ABC Foods Mumbai", "name": "ABC Foods Mumbai", "location": "Mumbai"},
                {"business_name": "Tata Sons", "name": "Tata Sons", "location": "Mumbai"}
            ],
            None # Should not match Mumbai branch due to location mismatch or threshold limits
        ),
        # 6. Completely unmatched businesses
        (
            {"business_name": "Completely Unrelated Corp", "name": "Completely Unrelated Corp"},
            [
                {"business_name": "ABC Foods Private Limited", "name": "ABC Foods Private Limited"},
                {"business_name": "Tata Sons Private Limited", "name": "Tata Sons Private Limited"}
            ],
            None
        ),
    ]

    total_correct = 0
    total_distinct_pairs_evaluated = 0
    false_merges = 0

    for index, (target, candidates, expected_match_idx) in enumerate(benchmark_cases):
        res = resolve_entity(target, candidates)
        matched = res["matched"]
        matched_entity = res["entity"]

        if expected_match_idx is not None:
            expected_entity = candidates[expected_match_idx]
            if matched and matched_entity == expected_entity:
                total_correct += 1
            else:
                print(f"Failed to match Case {index}: expected {expected_entity}, got {matched_entity}")
        else:
            if not matched or matched_entity is None:
                total_correct += 1
            else:
                # Target incorrectly matched/merged with a candidate that it should not merge with
                false_merges += 1
                print(f"False Merge in Case {index}: Target {target} matched with candidate {matched_entity}")

        # Compute evaluated pairs for False Entity Merge Rate calculation
        for candidate in candidates:
            # If the candidate was not supposed to match target, it's a distinct pair
            is_distinct = True
            if expected_match_idx is not None and candidate == candidates[expected_match_idx]:
                is_distinct = False
            
            if is_distinct:
                total_distinct_pairs_evaluated += 1
                # If the resolver matched this candidate, it's a false merge
                if matched and matched_entity == candidate:
                    pass # already incremented false_merges above

    # 1. Verify accuracy (must be >= 80%)
    accuracy = total_correct / len(benchmark_cases)
    print(f"Benchmark Accuracy: {accuracy * 100:.2f}% ({total_correct}/{len(benchmark_cases)})")
    assert accuracy >= 0.80

    # 2. Calculate and verify False Entity Merge Rate (must be < 5%)
    false_merge_rate = 0.0
    if total_distinct_pairs_evaluated > 0:
        false_merge_rate = false_merges / total_distinct_pairs_evaluated
    print(f"False Entity Merge Rate: {false_merge_rate * 100:.2f}% ({false_merges}/{total_distinct_pairs_evaluated})")
    assert false_merge_rate < 0.05
