SUMMARY_COLLECTION = "session_summaries"
DISTILLATION_COLLECTION = "distilled_knowledge"


def canonical_collection(collection: str) -> str:
    return {
        "_session_summaries": SUMMARY_COLLECTION,
        "_distilled_knowledge": DISTILLATION_COLLECTION,
    }.get(collection, collection)
