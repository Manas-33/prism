import logging

logger = logging.getLogger(__name__)

def compute_confidence(file_path: str, symbol_name: str, call_count: int) -> float:
    logger.info(f"Computing confidence for file_path='{file_path}', symbol_name='{symbol_name}', call_count={call_count}")
    score = 0.5

    if call_count > 1:
        score += 0.2
        logger.debug("Rule 1 applied: Multiple calls in file (+0.2)")

    if symbol_name.startswith("_"):
        score -= 0.1
        logger.debug("Rule 2 applied: Private symbol (-0.1)")
    else:
        score += 0.1
        logger.debug("Rule 2 applied: Public symbol (+0.1)")

    if "tests/" in file_path or "test_" in file_path or file_path.startswith("tests"):
        score -= 0.3
        logger.debug("Rule 3 applied: File is a test file (-0.3)")

    score = max(0.0, min(1.0, score))
    logger.info(f"Final computed score: {score}")

    return score

def confidence_label(score: float) -> str:
    if score >= 0.75:
        label = "High"
    elif score >= 0.4:
        label = "Medium"
    else:
        label = "Low"
    logger.info(f"Assigning confidence label for score: {score}: {label}")
    return label
