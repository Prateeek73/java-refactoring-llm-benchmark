from typing import TypedDict, List, Optional


class RefactorState(TypedDict):
    # Input
    sha:             str
    before_dir:      str
    after_dir:       str
    rminer_types:    List[str]
    # ParseAgent output
    before_code:     str
    smells_before:   int
    # RefactorAgent output
    refactored_code: str
    attempt:         int
    # ValidatorAgent output
    smells_after:    int
    srr:             Optional[float]
    compile_ok:      bool
    test_pass_rate:  Optional[float]   # EvoSuite pass@1
    # RankAgent output
    confidence:      float
    needs_review:    bool
