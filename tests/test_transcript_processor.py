"""Tests for Meeting Transcript Processor."""

from tpllm.workflows.transcript_processor import TranscriptProcessor


SAMPLE_TRANSCRIPT = """\
Alice: The auth migration is going well. I'll have it done soon.
Bob: We should probably update the docs, someone should handle that.
Alice: Yeah, maybe we could add a dark mode too. It depends on the timeline.
Charlie: I think the API rate limits are fine for now. Hopefully we won't hit them.
Bob: Let's circle back on the caching strategy next week.
Alice: The beta release is planned for end of quarter.
Charlie: I guess the load testing can wait. ASAP would be nice though.
"""


def test_detect_vague_commitments():
    processor = TranscriptProcessor()
    flags = processor.analyze(SAMPLE_TRANSCRIPT, attendees=["Alice", "Bob", "Charlie"])

    # Should detect multiple ambiguities
    assert len(flags) > 0

    categories = {f.category for f in flags}
    # Should detect vague timeline ("soon", "ASAP", "end of quarter")
    assert "vague_timeline" in categories or "vague_date" in categories


def test_detect_no_owner():
    processor = TranscriptProcessor()
    flags = processor.analyze(SAMPLE_TRANSCRIPT, attendees=["Alice", "Bob", "Charlie"])

    no_owner = [f for f in flags if f.category == "no_owner"]
    assert len(no_owner) > 0  # "someone should handle that"


def test_detect_deferred_untracked():
    processor = TranscriptProcessor()
    flags = processor.analyze(SAMPLE_TRANSCRIPT)

    deferred = [f for f in flags if f.category == "deferred_untracked"]
    assert len(deferred) > 0  # "let's circle back"


def test_detect_low_confidence():
    processor = TranscriptProcessor()
    flags = processor.analyze(SAMPLE_TRANSCRIPT)

    low_conf = [f for f in flags if f.category == "low_confidence"]
    assert len(low_conf) > 0  # "I think", "I guess"


def test_flags_have_quick_options():
    processor = TranscriptProcessor()
    flags = processor.analyze(SAMPLE_TRANSCRIPT)

    for flag in flags:
        assert len(flag.quick_options) > 0, f"Flag {flag.category} missing quick options"


def test_generate_clarifications():
    processor = TranscriptProcessor()
    flags = processor.analyze(SAMPLE_TRANSCRIPT, attendees=["Alice", "Bob", "Charlie"])
    clarifications = processor.generate_clarifications(flags, "Sprint Planning")

    assert len(clarifications) > 0
    # Should have some targeted at specific people
    targeted = [c for c in clarifications if c.target_person]
    assert len(targeted) > 0


def test_speaker_detection():
    processor = TranscriptProcessor()
    flags = processor.analyze(SAMPLE_TRANSCRIPT, attendees=["Alice", "Bob", "Charlie"])

    # Alice said "soon" — should be attributed to Alice
    alice_flags = [f for f in flags if f.suggested_owner == "Alice"]
    assert len(alice_flags) > 0


def test_summary_rendering():
    processor = TranscriptProcessor()
    flags = processor.analyze(SAMPLE_TRANSCRIPT)
    summary = processor.get_summary(flags)

    assert "Transcript Analysis" in summary
    assert "ambiguities detected" in summary


def test_clean_transcript():
    processor = TranscriptProcessor()
    flags = processor.analyze("Alice: The migration is complete. All tests pass. Ship date is March 15.")

    # Should detect minimal or no ambiguity
    assert len(flags) == 0


def test_batched_clarifications():
    """Multiple flags for the same person should be batched."""
    processor = TranscriptProcessor()
    # Alice has multiple vague statements
    transcript = "Alice: I'll do it soon. Alice: I think it's fine. Alice: Maybe next week."
    flags = processor.analyze(transcript, attendees=["Alice"])
    clarifications = processor.generate_clarifications(flags, "Meeting")

    alice_requests = [c for c in clarifications if c.target_person == "Alice"]
    # Should batch into 1 request (not 3 separate ones)
    assert len(alice_requests) == 1
    assert "clarifications" in alice_requests[0].subject
