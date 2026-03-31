from typing import Any


def calculate_score(quiz_data: dict[str, Any], answers: list[dict[str, str]]) -> dict[str, Any]:
    questions = {question["id"]: question for question in quiz_data.get("questions", []) if "id" in question}
    total_questions = len(questions)

    if total_questions == 0:
        return {"score": 0, "total": 0, "percentage": 0.0}

    user_answers: dict[str, str] = {}
    for answer in answers:
        question_id = answer.get("question_id")
        choice = answer.get("choice")
        if question_id and choice:
            user_answers[question_id] = choice

    correct_count = 0
    for question_id, question in questions.items():
        if user_answers.get(question_id) == question.get("correct_option_id"):
            correct_count += 1

    return {
        "score": correct_count,
        "total": total_questions,
        "percentage": round(correct_count / total_questions * 100, 2),
    }
