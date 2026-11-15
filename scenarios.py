#!/usr/bin/env python3
"""Hand-adjudicated scenarios shared by the tests and the acceptance runner.

Each entry is ``(name, pages, expected_clusters, expected_conflicts)``:

* ``pages`` is a ready-to-use ``input.pages`` list, deliberately out of order
  and sometimes containing duplicate records for one URL;
* ``expected_clusters`` lists the member URLs of each cluster; the
  representative is always the first (lexicographically smallest) member;
* ``expected_conflicts`` maps a URL to the exact ``conflict:`` verdict
  fragments it must carry, i.e. the recorded arbitration reason.

The expectations come from the documented rules, not from canon.py, so they
act as the reference adjudication.  ``LANGUAGES`` below is the pair of
language configurations used by the acceptance rerun.
"""

A = "https://a.test/p"
B = "https://b.test/p"
C = "https://c.test/p"
D = "https://d.test/p"
E = "https://e.test/p"
Z = "https://z.test/p"
EXT = "https://ext.test/q"

# Two language configurations that must produce identical cluster summaries.
LANGUAGES = (["en", "ja"], ["ja", "en", "de"])


def page(url, canonical=None, hl=None):
    """Build one ``input.pages`` entry.  ``hl`` is [(lang, target), ...]."""
    return {
        "url": url,
        "canonical": canonical,
        "hreflang": [{"lang": lang, "url": target} for lang, target in (hl or [])],
    }


# name, pages, expected clusters, expected conflicts
CASES = []


def case(name, pages, clusters, conflict_map=None):
    CASES.append((name, pages, clusters, conflict_map or {}))


# --- canonical rings and chains -------------------------------------------
case(
    "01_two_node_canonical_cycle",
    [page(B, A), page(A, B)],
    [[A, B]],
)
case(
    "02_three_node_canonical_cycle",
    [page(C, A), page(A, B), page(B, C)],
    [[A, B, C]],
)
case(
    "03_four_node_canonical_cycle",
    [page(D, A), page(A, B), page(B, C), page(C, D)],
    [[A, B, C, D]],
)
case(
    "04_cycle_with_feeder",
    [page(D, B), page(A, B), page(B, A)],
    [[A, B, D]],
)
case(
    "05_canonical_chain",
    [page(C, None), page(A, B), page(B, C)],
    [[A, B, C]],
)
case(
    "06_chain_into_cycle",
    [page(D, B), page(C, D), page(A, B), page(B, C)],
    [[A, B, C, D]],
)
case(
    "07_cycle_rep_is_smallest_url_not_first_input_page",
    [page(C, B), page(B, C), page(A, C)],
    [[A, B, C]],
)
case(
    "08_cross_domain_canonical",
    [page(Z, None), page(A, Z)],
    [[A, Z]],
)
case(
    "09_canonical_target_outside_input",
    [page(A, EXT)],
    [[A]],
)
case(
    "10_self_canonical",
    [page(A, A)],
    [[A]],
)
case(
    "11_null_canonical",
    [page(B, A), page(A, None)],
    [[A, B]],
)

# --- hreflang pairs -------------------------------------------------------
case(
    "12_reciprocal_hreflang_same_language",
    [page(B, None, [("en", A)]), page(A, None, [("en", B)])],
    [[A, B]],
)
case(
    "13_reciprocal_hreflang_language_mismatch",
    [page(B, None, [("ja", A)]), page(A, None, [("en", B)])],
    [[A, B]],
)
case(
    "14_one_way_hreflang_is_not_used",
    [page(B, None), page(A, None, [("en", B)])],
    [[A], [B]],
    {A: ["conflict:hreflang-one-way:en->" + B]},
)
case(
    "15_hreflang_target_outside_input",
    [page(A, None, [("en", EXT)])],
    [[A]],
    {A: ["conflict:hreflang-external-target:en->" + EXT]},
)
case(
    "16_one_language_two_targets",
    [page(C, None, [("en", A)]), page(A, None, [("en", B), ("en", C)]), page(B, None, [("en", A)])],
    [[A, B, C]],
    {A: ["conflict:hreflang-lang-duplicate:en->" + B + "|" + C]},
)
case(
    "17_self_hreflang_plus_reciprocal",
    [page(B, None, [("en", A)]), page(A, None, [("en", A), ("en", B)])],
    [[A, B]],
)
case(
    "18_cross_domain_hreflang_pair",
    [page(Z, None, [("en", A)]), page(A, None, [("en", Z)])],
    [[A, Z]],
)
case(
    "19_reciprocal_hreflang_across_canonical_groups",
    [page(D, C), page(B, A), page(C, None, [("en", A)]), page(A, None, [("en", C)])],
    [[A, B, C, D]],
)
case(
    "20_cycle_bridged_by_hreflang",
    [page(C, None, [("ja", A)]), page(B, A), page(A, B, [("ja", C)]), page(A, B)],
    [[A, B, C]],
)
case(
    "21_canonical_groups_joined_by_hreflang",
    [
        page(D, None, [("en", C)]),
        page(C, None, [("en", D)]),
        page(B, D, [("en", A)]),
        page(A, C, [("en", B)]),
    ],
    [[A, B, C, D]],
)

# --- language dimension ---------------------------------------------------
case(
    "22_multi_language_alternate_set",
    [page(B, None, [("ja", B), ("en", A)]), page(A, None, [("en", A), ("ja", B)])],
    [[A, B]],
)
case(
    "23_three_page_partial_triangle",
    [
        page(C, None, [("de", B)]),
        page(B, None, [("en", A), ("ja", C)]),
        page(A, None, [("en", B)]),
    ],
    [[A, B, C]],
)
case(
    "24_one_way_member_of_triangle",
    [page(C, None, [("ja", A)]), page(B, None, [("en", A)]), page(A, None, [("en", B)])],
    [[A, B], [C]],
    {C: ["conflict:hreflang-one-way:ja->" + A]},
)
case(
    "25_region_tags_are_one_language",
    [
        page(B, None, [("en-GB", B), ("en-GB", A)]),
        page(A, None, [("en-US", A), ("en-US", B)]),
    ],
    [[A, B]],
)

# --- contradictory / duplicated declarations -----------------------------
case(
    "26_duplicate_records_conflicting_canonical",
    [page(C, None), page(A, B), page(B, None), page(A, C)],
    [[A, B], [C]],
    {A: ["conflict:canonical-multiple:" + B + "|" + C]},
)
case(
    "27_duplicate_records_self_and_target_canonical",
    [page(B, None), page(A, A), page(A, B)],
    [[A, B]],
    {A: ["conflict:canonical-self-and-target:" + B]},
)
case(
    "28_duplicate_records_hreflang_are_merged",
    [
        page(B, None, [("en", A)]),
        page(A, None, [("en", B)]),
        page(A, None, [("en", B)]),
    ],
    [[A, B]],
)

# --- degenerate inputs ----------------------------------------------------
case(
    "29_empty_input",
    [],
    [],
)
case(
    "30_isolated_pages",
    [page(C), page(A), page(B)],
    [[A], [B], [C]],
)
case(
    "31_mixed_ring_cross_domain_and_one_way",
    [
        page(Z, None, [("en", B)]),
        page(E, None, [("de", A)]),
        page(A, B),
        page(C, Z),
        page(B, A, [("en", Z)]),
        page(D, A),
        page(B, A),
    ],
    [[A, B, C, D, Z], [E]],
    {E: ["conflict:hreflang-one-way:de->" + A]},
)

# --- several candidates canonicalised to one off-input target -------------
case(
    "32_shared_external_canonical_merges_candidates",
    [page(B, EXT), page(C, B), page(A, EXT)],
    [[A, B, C]],
)
case(
    "33_distinct_external_canonicals_stay_apart",
    [page(A, EXT), page(B, "https://ext.test/other")],
    [[A], [B]],
)
case(
    "34_hreflang_against_canonical_direction",
    [page(A, B), page(B, A, [("en", A)])],
    [[A, B]],
    {B: ["conflict:hreflang-one-way:en->" + A]},
)

