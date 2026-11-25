"""Stateless, one-question-at-a-time coordinator onboarding flow."""

from pydantic import Field, field_validator

from core.schemas import StrictBaseModel

ONBOARDING_QUESTIONS = (
    "How do you trade today: which markets, timeframes, and style do you use?",
    "What do you want the bot to achieve?",
    "Which trading task takes up most of your screen time?",
    "What is your account size, and how much are you willing to risk per trade?",
    "Which broker or exchange do you use, and does it offer a paper-trading environment?",
)


class OnboardingProgress(StrictBaseModel):
    """Client-carried answers; the API does not store this potentially sensitive data."""

    answers: list[str] = Field(default_factory=list, max_length=len(ONBOARDING_QUESTIONS))

    @field_validator("answers")
    @classmethod
    def validate_answers(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("Each completed onboarding answer must be non-empty.")
        return cleaned


class OnboardingStep(StrictBaseModel):
    completed_count: int = Field(..., ge=0, le=len(ONBOARDING_QUESTIONS))
    total_questions: int = Field(default=len(ONBOARDING_QUESTIONS))
    next_question: str | None
    interview_complete: bool


def get_onboarding_step(progress: OnboardingProgress) -> OnboardingStep:
    """Return only the next question; never starts setup or trading actions."""
    count = len(progress.answers)
    complete = count == len(ONBOARDING_QUESTIONS)
    return OnboardingStep(
        completed_count=count,
        next_question=None if complete else ONBOARDING_QUESTIONS[count],
        interview_complete=complete,
    )
