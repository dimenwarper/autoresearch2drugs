from __future__ import annotations

from typing import Any

from .constants import COST_BANDS, DURATION_BANDS
from .models import PortfolioState, ProgramState, RegulatoryNote
from .portfolio import next_identifier
from .program import has_pivotal_package


def regulatory_feedback_fee(question_set: list[str]) -> float:
    low, high = COST_BANDS["regulatory_feedback"]
    return low + min(len(question_set), 4) / 4.0 * (high - low)


def regulatory_submission_cost(program: ProgramState) -> float:
    low, high = COST_BANDS["regulatory_submission"]
    strictness = program.hidden_state.strategic_hidden["regulatory_strictness"]
    return low + (high - low) * strictness


def regulatory_review_duration(program: ProgramState) -> int:
    minimum, maximum = DURATION_BANDS["regulatory_review"]
    strictness = program.hidden_state.strategic_hidden["regulatory_strictness"]
    return minimum + int(round((maximum - minimum) * strictness))


def create_regulatory_note(
    portfolio: PortfolioState,
    *,
    note_type: str,
    question_set: list[str],
    summary: str,
    comments: list[str],
) -> RegulatoryNote:
    return RegulatoryNote(
        note_id=next_identifier(portfolio, "next_note_index", "note"),
        note_type=note_type,
        question_set=question_set,
        summary=summary,
        comments=comments,
        month=portfolio.elapsed_months,
    )


def instant_feedback(
    portfolio: PortfolioState,
    program: ProgramState,
    question_set: list[str],
) -> tuple[RegulatoryNote, dict[str, Any]]:
    comments = []
    for question in question_set:
        lower = question.lower()
        if "endpoint" in lower:
            comments.append("Regulator noted endpoint acceptability depends on assay interpretability and population alignment.")
        elif "biomarker" in lower:
            comments.append("Biomarker use may support enrichment if analytically validated before Phase II.")
        elif "safety" in lower:
            comments.append("Safety database expectations rise materially after the first patient cohort.")
        else:
            comments.append("Agency feedback remained directionally useful but did not remove development risk.")
    note = create_regulatory_note(
        portfolio,
        note_type="advisory_feedback",
        question_set=question_set,
        summary="Regulatory feedback issued",
        comments=comments,
    )
    report = {
        "note_id": note.note_id,
        "program_id": program.program_id,
        "question_set": question_set,
        "summary": note.summary,
        "comments": comments,
        "month": note.month,
    }
    return note, report


def submission_note(
    portfolio: PortfolioState,
    *,
    review_type: str,
) -> RegulatoryNote:
    return create_regulatory_note(
        portfolio,
        note_type=f"{review_type}_submission",
        question_set=["submission dossier"],
        summary=f"{review_type} dossier submitted",
        comments=["Submission accepted for formal review."],
    )


def complete_review(
    portfolio: PortfolioState,
    program: ProgramState,
    work_item,
) -> tuple[RegulatoryNote, dict[str, Any], str]:
    review_type = work_item.payload["review_type"]
    strictness = program.hidden_state.strategic_hidden["regulatory_strictness"]
    decision = "failed"
    comments = []
    if review_type == "IND":
        if (
            program.manufacturing_status == "acceptable"
            and program.nonclinical_safety_status == "acceptable"
            and program.latest_valid_design("phase1") is not None
            and strictness < 0.82
        ):
            decision = "IND_cleared"
            comments.append("Agency cleared IND based on coherent CMC, toxicology, and protocol package.")
        else:
            comments.append("Agency cited package deficiencies preventing first-in-human clearance.")
    elif review_type == "NDA":
        pivotal = has_pivotal_package(program)
        if (
            pivotal
            and program.manufacturing_status == "acceptable"
            and program.safety_database_status == "acceptable"
            and strictness < 0.88
        ):
            decision = "approved"
            comments.append("Application approved with benefit-risk profile considered favorable.")
        else:
            comments.append("Review concluded evidence package or readiness remained insufficient for approval.")
    note_type = f"{review_type}_decision_{decision}"
    note = create_regulatory_note(
        portfolio,
        note_type=note_type,
        question_set=[f"{review_type} review"],
        summary=f"{review_type} review completed with outcome {decision}",
        comments=comments,
    )
    report = {
        "note_id": note.note_id,
        "program_id": program.program_id,
        "review_type": review_type,
        "decision": decision,
        "summary": note.summary,
        "comments": comments,
        "month": note.month,
    }
    return note, report, decision
