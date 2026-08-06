from security_response_generator.generation.terminology import expand_query


def test_expand_query_appends_matched_nist_term():
    assert expand_query("what is the password length requirement?") == (
        "what is the password length requirement? authenticator"
    )


def test_expand_query_is_case_insensitive():
    assert expand_query("PASSWORD policy") == "PASSWORD policy authenticator"


def test_expand_query_leaves_unmatched_text_unchanged():
    text = "what is the encryption at rest requirement?"
    assert expand_query("no matching terms here") == "no matching terms here"
    assert expand_query(text) == f"{text} cryptographic protection"


def test_expand_query_dedupes_repeated_nist_terms():
    result = expand_query("password and passphrase requirements")
    assert result.count("authenticator") == 1


def test_expand_query_appends_multiple_distinct_terms_in_order():
    result = expand_query("badge and vulnerability scan policy")
    assert result == (
        "badge and vulnerability scan policy physical access control "
        "physical access device vulnerability monitoring and scanning"
    )


def test_expand_query_matches_whole_words_only():
    # "vm" must not match inside "advmark" or similar -- word-boundary match only.
    assert expand_query("advmarketing team") == "advmarketing team"
